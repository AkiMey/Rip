package com.ripperinio.mobile;

import android.app.Activity;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.graphics.Bitmap;
import android.graphics.PixelFormat;
import android.hardware.display.DisplayManager;
import android.hardware.display.VirtualDisplay;
import android.media.Image;
import android.media.ImageReader;
import android.media.projection.MediaProjection;
import android.media.projection.MediaProjectionManager;
import android.os.Build;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.IBinder;
import android.util.DisplayMetrics;
import android.view.WindowManager;

import java.io.ByteArrayOutputStream;
import java.io.DataOutputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.InetSocketAddress;
import java.net.Socket;
import java.net.URI;
import java.net.URL;
import java.nio.ByteBuffer;

import org.json.JSONObject;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;

/**
 * High-FPS phone screen capture using MediaProjection.
 *
 * Accessibility screenshots are intentionally rate limited by Android and are
 * useful as a compatibility fallback only. This service keeps a real virtual
 * display alive, encodes the newest frame, and sends it over one long-lived
 * authenticated TCP stream (HTTP chunked POST remains a compatibility fallback).
 * Old frames are dropped when the network falls behind so
 * latency stays low instead of building a several-second video queue.
 */
public final class ScreenStreamService extends Service {
    private static final String CHANNEL = "rip_screen_stream_v61";
    private static final int NOTIFICATION_ID = 621;
    private static volatile boolean running = false;
    private static volatile long captureFrames = 0L;
    private static volatile long sentFrames = 0L;
    private static volatile long tcpFrames = 0L;
    private static volatile long httpFrames = 0L;
    private static volatile long lastCaptureAtMs = 0L;
    private static volatile long lastSendAtMs = 0L;
    private static volatile String lastStreamError = "";
    private static volatile String liveTransport = "";

    private MediaProjection projection;
    private VirtualDisplay virtualDisplay;
    private ImageReader imageReader;
    private HandlerThread captureThread;
    private Handler captureHandler;
    private ExecutorService networkExecutor;
    private ScheduledExecutorService statusExecutor;
    private final ArrayBlockingQueue<Frame> frames = new ArrayBlockingQueue<>(2);

    private volatile boolean stopping = false;
    private int sourceWidth;
    private int sourceHeight;
    private int captureWidth;
    private int captureHeight;
    private int quality = 50;
    private int targetFps = 12;
    private long lastEncodeNs = 0L;

    private HttpURLConnection streamConnection;
    private Socket streamSocket;
    private DataOutputStream streamOut;
    private volatile String streamTransport = "";

    public static boolean isRunning() { return running; }

    @Override public void onCreate() {
        super.onCreate();
        createChannel();
        networkExecutor = Executors.newSingleThreadExecutor();
        networkExecutor.execute(this::networkLoop);
        statusExecutor = Executors.newSingleThreadScheduledExecutor();
        statusExecutor.scheduleWithFixedDelay(this::sendStreamStatusSafe, 250, 1000, TimeUnit.MILLISECONDS);
    }

    @Override public int onStartCommand(Intent intent, int flags, int startId) {
        if (intent == null) return START_NOT_STICKY;
        if (intent.getBooleanExtra("stop", false)) {
            stopSelf();
            return START_NOT_STICKY;
        }
        // IMPORTANT: Activity.RESULT_OK is -1. Do not use -1 as the
        // "missing" sentinel or every user-approved MediaProjection request
        // is rejected before capture starts.
        int resultCode = intent.getIntExtra("result_code", Integer.MIN_VALUE);
        Intent resultData;
        if (Build.VERSION.SDK_INT >= 33) {
            resultData = intent.getParcelableExtra("result_data", Intent.class);
        } else {
            //noinspection deprecation
            resultData = intent.getParcelableExtra("result_data");
        }
        targetFps = clamp(intent.getIntExtra("fps", 12), 4, 30);
        quality = clamp(intent.getIntExtra("quality", 50), 30, 75);
        int maxWidth = clamp(intent.getIntExtra("max_width", 720), 420, 1080);
        if (resultCode != Activity.RESULT_OK || resultData == null) {
            lastStreamError = "MediaProjection izin sonucu geçersiz (code=" + resultCode + ")";
            sendStreamStatusSafe();
            stopSelf();
            return START_NOT_STICKY;
        }
        try {
            // Android 14+ requires the user-approved mediaProjection foreground
            // service to be promoted before getMediaProjection() is called.
            startProjectionForeground("Ekran yayını hazırlanıyor…");
            startCapture(resultCode, resultData, maxWidth);
        } catch (Throwable ex) {
            lastStreamError = ex.getClass().getSimpleName() + ": " + String.valueOf(ex.getMessage());
            updateNotification("Ekran yayını başlatılamadı: " + lastStreamError);
            sendStreamStatusSafe();
            stopSelf();
        }
        return START_NOT_STICKY;
    }

    private void startCapture(int resultCode, Intent data, int maxWidth) throws Exception {
        if (projection != null) return;
        captureFrames = 0L;
        sentFrames = 0L;
        tcpFrames = 0L;
        httpFrames = 0L;
        lastCaptureAtMs = 0L;
        lastSendAtMs = 0L;
        lastStreamError = "";
        liveTransport = "CAPTURE";
        WindowManager wm = (WindowManager) getSystemService(Context.WINDOW_SERVICE);
        if (wm == null) throw new Exception("WindowManager yok");
        DisplayMetrics dm = new DisplayMetrics();
        //noinspection deprecation
        wm.getDefaultDisplay().getRealMetrics(dm);
        sourceWidth = Math.max(1, dm.widthPixels);
        sourceHeight = Math.max(1, dm.heightPixels);
        float scale = Math.min(1f, maxWidth / (float) sourceWidth);
        captureWidth = makeEven(Math.max(2, Math.round(sourceWidth * scale)));
        captureHeight = makeEven(Math.max(2, Math.round(sourceHeight * scale)));

        MediaProjectionManager manager = (MediaProjectionManager) getSystemService(Context.MEDIA_PROJECTION_SERVICE);
        if (manager == null) throw new Exception("MediaProjectionManager yok");
        projection = manager.getMediaProjection(resultCode, data);
        if (projection == null) throw new Exception("Ekran paylaşım izni alınamadı");
        projection.registerCallback(new MediaProjection.Callback() {
            @Override public void onStop() { stopSelf(); }
        }, new Handler(getMainLooper()));

        captureThread = new HandlerThread("rip-screen-capture");
        captureThread.start();
        captureHandler = new Handler(captureThread.getLooper());
        imageReader = ImageReader.newInstance(captureWidth, captureHeight, PixelFormat.RGBA_8888, 3);
        imageReader.setOnImageAvailableListener(this::onImageAvailable, captureHandler);
        virtualDisplay = projection.createVirtualDisplay(
                "RIP Screen Stream",
                captureWidth,
                captureHeight,
                Math.max(160, dm.densityDpi),
                DisplayManager.VIRTUAL_DISPLAY_FLAG_AUTO_MIRROR,
                imageReader.getSurface(),
                null,
                captureHandler);
        running = true;
        liveTransport = "CAPTURE";
        updateNotification("Canlı ekran • " + targetFps + " FPS hedef • " + captureWidth + "×" + captureHeight);
        broadcastState();
        sendStreamStatusSafe();
    }

    private void onImageAvailable(ImageReader reader) {
        Image image = null;
        try {
            image = reader.acquireLatestImage();
            if (image == null || stopping) return;
            long now = System.nanoTime();
            long minInterval = 1_000_000_000L / Math.max(1, targetFps);
            if (lastEncodeNs != 0L && now - lastEncodeNs < minInterval) return;
            lastEncodeNs = now;

            Image.Plane plane = image.getPlanes()[0];
            ByteBuffer buffer = plane.getBuffer();
            int pixelStride = plane.getPixelStride();
            int rowStride = plane.getRowStride();
            int rowPadding = rowStride - pixelStride * captureWidth;
            int paddedWidth = captureWidth + Math.max(0, rowPadding / Math.max(1, pixelStride));

            Bitmap padded = Bitmap.createBitmap(paddedWidth, captureHeight, Bitmap.Config.ARGB_8888);
            padded.copyPixelsFromBuffer(buffer);
            Bitmap cropped = paddedWidth == captureWidth
                    ? padded
                    : Bitmap.createBitmap(padded, 0, 0, captureWidth, captureHeight);
            ByteArrayOutputStream out = new ByteArrayOutputStream(160 * 1024);
            boolean ok = cropped.compress(Bitmap.CompressFormat.JPEG, quality, out);
            byte[] jpeg = ok ? out.toByteArray() : null;
            if (cropped != padded) cropped.recycle();
            padded.recycle();
            if (jpeg == null || jpeg.length == 0) return;

            captureFrames++;
            lastCaptureAtMs = System.currentTimeMillis();
            lastStreamError = "";
            Frame frame = new Frame(jpeg, lastCaptureAtMs, sourceWidth, sourceHeight);
            // Latest-frame semantics: never let old frames queue up and create lag.
            if (!frames.offer(frame)) {
                frames.poll();
                frames.offer(frame);
            }
        } catch (Throwable ex) {
            lastStreamError = "CAPTURE " + ex.getClass().getSimpleName() + ": " + String.valueOf(ex.getMessage());
        } finally {
            if (image != null) image.close();
        }
    }

    private void networkLoop() {
        long lastTcpAttemptMs = 0L;
        while (!stopping) {
            try {
                Frame frame = frames.poll(500, TimeUnit.MILLISECONDS);
                if (frame == null) continue;

                boolean sent = false;
                long nowMs = System.currentTimeMillis();
                if (streamOut != null || nowMs - lastTcpAttemptMs >= 1800L) {
                    try {
                        lastTcpAttemptMs = nowMs;
                        ensureTcpStream();
                        writeTcpFrame(frame);
                        sent = true;
                        sentFrames++;
                        tcpFrames++;
                        lastSendAtMs = System.currentTimeMillis();
                        liveTransport = "TCP";
                        lastStreamError = "";
                    } catch (Exception tcpError) {
                        lastStreamError = "TCP " + tcpError.getClass().getSimpleName() + ": " + String.valueOf(tcpError.getMessage());
                        closeStream();
                    }
                }

                // V6.2.1 fallback: if TCP 8766 is blocked/stale, post this single
                // frame over normal bridge 8765. Unlike a long chunked POST this
                // cannot be buffered indefinitely by Samsung HttpURLConnection.
                if (!sent) {
                    try {
                        sendFrameHttp(frame);
                        streamTransport = "HTTP-FRAME";
                        liveTransport = "HTTP-FRAME";
                        sentFrames++;
                        httpFrames++;
                        lastSendAtMs = System.currentTimeMillis();
                        lastStreamError = "";
                    } catch (Exception httpError) {
                        lastStreamError = "HTTP " + httpError.getClass().getSimpleName() + ": " + String.valueOf(httpError.getMessage());
                        try { Thread.sleep(120L); } catch (InterruptedException ignored) {
                            Thread.currentThread().interrupt();
                            break;
                        }
                    }
                }
            } catch (InterruptedException ex) {
                Thread.currentThread().interrupt();
                break;
            }
        }
        closeStream();
    }

    private void writeTcpFrame(Frame frame) throws Exception {
        if (streamOut == null) throw new Exception("TCP ekran stream bağlı değil");
        streamOut.writeBytes("RIPF");
        streamOut.writeInt(frame.jpeg.length);
        streamOut.writeLong(frame.timestampMs);
        streamOut.writeInt(frame.screenWidth);
        streamOut.writeInt(frame.screenHeight);
        streamOut.write(frame.jpeg);
        streamOut.flush();
    }

    private void ensureTcpStream() throws Exception {
        if (streamOut != null && streamSocket != null && streamSocket.isConnected() && !streamSocket.isClosed()) return;
        BridgeConfig config = new BridgeConfig(this);
        String base = BridgeState.baseUrl == null ? "" : BridgeState.baseUrl.trim();
        if (base.isEmpty()) {
            BridgeClient client = new BridgeClient(this);
            if (!client.probe()) throw new Exception("PC bridge bağlantısı yok");
            base = BridgeState.baseUrl == null ? "" : BridgeState.baseUrl.trim();
        }
        if (base.isEmpty() || config.token().isEmpty()) throw new Exception("Bridge eşleşmesi yok");

        URI uri = URI.create(base);
        String host = uri.getHost();
        if (host == null || host.trim().isEmpty()) throw new Exception("Bridge host yok");
        Socket socket = new Socket();
        socket.setTcpNoDelay(true);
        socket.setKeepAlive(true);
        socket.setSendBufferSize(512 * 1024);
        socket.connect(new InetSocketAddress(host, 8766), 1800);
        DataOutputStream out = new DataOutputStream(socket.getOutputStream());
        byte[] tokenBytes = config.token().getBytes(java.nio.charset.StandardCharsets.UTF_8);
        if (tokenBytes.length <= 0 || tokenBytes.length > 512) throw new Exception("Bridge token geçersiz");
        out.writeBytes("RIPS");
        out.writeShort(tokenBytes.length);
        out.write(tokenBytes);
        out.flush();
        socket.setSoTimeout(1800);
        int ack = socket.getInputStream().read();
        if (ack != 1) {
            try { socket.close(); } catch (Exception ignored) {}
            throw new Exception("Ekran stream yetkilendirmesi reddedildi");
        }
        socket.setSoTimeout(0);
        streamSocket = socket;
        streamOut = out;
        streamTransport = "TCP";
        liveTransport = "TCP";
        updateNotification("Canlı ekran • TCP • " + targetFps + " FPS hedef • " + captureWidth + "×" + captureHeight);
    }

    private void sendFrameHttp(Frame frame) throws Exception {
        BridgeConfig config = new BridgeConfig(this);
        String base = BridgeState.baseUrl == null ? "" : BridgeState.baseUrl.trim();
        if (base.isEmpty()) {
            BridgeClient client = new BridgeClient(this);
            if (!client.probe()) throw new Exception("PC bridge bağlantısı yok");
            base = BridgeState.baseUrl == null ? "" : BridgeState.baseUrl.trim();
        }
        if (base.isEmpty() || config.token().isEmpty()) throw new Exception("Bridge eşleşmesi yok");
        HttpURLConnection c = (HttpURLConnection) new URL(base + "/api/phone-screen-frame").openConnection();
        c.setRequestMethod("POST");
        c.setDoOutput(true);
        c.setUseCaches(false);
        c.setConnectTimeout(1600);
        c.setReadTimeout(1800);
        c.setFixedLengthStreamingMode(frame.jpeg.length);
        c.setRequestProperty("X-RIP-Token", config.token());
        c.setRequestProperty("Content-Type", "image/jpeg");
        c.setRequestProperty("X-RIP-Captured-At", Long.toString(frame.timestampMs));
        c.setRequestProperty("X-RIP-Screen-Width", Integer.toString(frame.screenWidth));
        c.setRequestProperty("X-RIP-Screen-Height", Integer.toString(frame.screenHeight));
        try (OutputStream raw = c.getOutputStream()) {
            raw.write(frame.jpeg);
            raw.flush();
        }
        int code = c.getResponseCode();
        if (code < 200 || code >= 300) throw new Exception("HTTP frame " + code);
        c.disconnect();
        if (!"HTTP-FRAME".equals(streamTransport)) {
            updateNotification("Canlı ekran • HTTP fallback • " + targetFps + " FPS hedef");
        }
    }

    private JSONObject streamStatusPayload() throws Exception {
        JSONObject p = new JSONObject();
        p.put("kind", "screen_stream_status");
        p.put("device", Build.MANUFACTURER + " " + Build.MODEL);
        p.put("running", running);
        p.put("capture_frames", captureFrames);
        p.put("sent_frames", sentFrames);
        p.put("tcp_frames", tcpFrames);
        p.put("http_frames", httpFrames);
        p.put("last_capture_at", lastCaptureAtMs);
        p.put("last_send_at", lastSendAtMs);
        p.put("transport", liveTransport);
        p.put("last_error", lastStreamError == null ? "" : lastStreamError);
        p.put("target_fps", targetFps);
        p.put("capture_width", captureWidth);
        p.put("capture_height", captureHeight);
        p.put("source_width", sourceWidth);
        p.put("source_height", sourceHeight);
        return p;
    }

    private void sendStreamStatusSafe() {
        try {
            BridgeClient client = new BridgeClient(this);
            client.sendEncrypted(streamStatusPayload());
        } catch (Throwable ignored) {}
    }

    private void closeStream() {
        try { if (streamOut != null) streamOut.close(); } catch (Exception ignored) {}
        try { if (streamSocket != null) streamSocket.close(); } catch (Exception ignored) {}
        try { if (streamConnection != null) streamConnection.disconnect(); } catch (Exception ignored) {}
        streamOut = null;
        streamSocket = null;
        streamConnection = null;
        streamTransport = "";
    }

    @Override public void onDestroy() {
        stopping = true;
        running = false;
        liveTransport = "STOPPED";
        try { if (imageReader != null) imageReader.setOnImageAvailableListener(null, null); } catch (Exception ignored) {}
        try { if (virtualDisplay != null) virtualDisplay.release(); } catch (Exception ignored) {}
        try { if (imageReader != null) imageReader.close(); } catch (Exception ignored) {}
        try { if (projection != null) projection.stop(); } catch (Exception ignored) {}
        try { if (captureThread != null) captureThread.quitSafely(); } catch (Exception ignored) {}
        if (networkExecutor != null) networkExecutor.shutdownNow();
        if (statusExecutor != null) statusExecutor.shutdownNow();
        sendStreamStatusSafe();
        closeStream();
        broadcastState();
        super.onDestroy();
    }

    private void createChannel() {
        if (Build.VERSION.SDK_INT < 26) return;
        NotificationManager nm = (NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE);
        if (nm == null) return;
        NotificationChannel ch = new NotificationChannel(CHANNEL, "RIP Canlı Ekran", NotificationManager.IMPORTANCE_LOW);
        ch.setDescription("PC Phone Control Center için canlı telefon ekranı");
        nm.createNotificationChannel(ch);
    }

    private void startProjectionForeground(String text) {
        Notification n = notification(text);
        if (Build.VERSION.SDK_INT >= 29) {
            startForeground(NOTIFICATION_ID, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION);
        } else {
            startForeground(NOTIFICATION_ID, n);
        }
    }

    private void updateNotification(String text) {
        NotificationManager nm = (NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE);
        if (nm != null) nm.notify(NOTIFICATION_ID, notification(text));
    }

    private Notification notification(String text) {
        Intent open = new Intent(this, MainActivity.class);
        PendingIntent pi = PendingIntent.getActivity(this, 61, open,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        Notification.Builder b = Build.VERSION.SDK_INT >= 26
                ? new Notification.Builder(this, CHANNEL)
                : new Notification.Builder(this);
        return b.setContentTitle("RIP Canlı Ekran")
                .setContentText(text)
                .setSmallIcon(android.R.drawable.stat_notify_sync)
                .setOngoing(true)
                .setOnlyAlertOnce(true)
                .setContentIntent(pi)
                .build();
    }

    private void broadcastState() {
        try { sendBroadcast(new Intent(MainActivity.ACTION_STATE).setPackage(getPackageName())); } catch (Exception ignored) {}
    }

    public static void start(Context context, int resultCode, Intent data, int fps, int maxWidth, int quality) {
        Intent i = new Intent(context, ScreenStreamService.class);
        i.putExtra("result_code", resultCode);
        i.putExtra("result_data", data);
        i.putExtra("fps", fps);
        i.putExtra("max_width", maxWidth);
        i.putExtra("quality", quality);
        if (Build.VERSION.SDK_INT >= 26) context.startForegroundService(i); else context.startService(i);
    }

    public static void stop(Context context) {
        try { context.stopService(new Intent(context, ScreenStreamService.class)); } catch (Exception ignored) {}
    }

    @Override public IBinder onBind(Intent intent) { return null; }

    private static int clamp(int value, int lo, int hi) { return Math.max(lo, Math.min(hi, value)); }
    private static int makeEven(int value) { return (value & 1) == 0 ? value : value - 1; }

    private static final class Frame {
        final byte[] jpeg;
        final long timestampMs;
        final int screenWidth;
        final int screenHeight;
        Frame(byte[] jpeg, long timestampMs, int screenWidth, int screenHeight) {
            this.jpeg = jpeg;
            this.timestampMs = timestampMs;
            this.screenWidth = screenWidth;
            this.screenHeight = screenHeight;
        }
    }
}
