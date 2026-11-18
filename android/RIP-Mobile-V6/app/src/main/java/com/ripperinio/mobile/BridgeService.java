package com.ripperinio.mobile;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.app.Service;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.ContentResolver;
import android.content.ContentValues;
import android.content.Context;
import android.content.Intent;
import android.net.Uri;
import android.os.Build;
import android.os.Environment;
import android.os.IBinder;
import android.provider.MediaStore;

import org.json.JSONObject;

import java.io.BufferedInputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.security.MessageDigest;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;

public class BridgeService extends Service {
    public static final String CHANNEL = "rip_bridge_v5";
    private ScheduledExecutorService scheduler;
    private BridgeClient client;
    private BridgeConfig config;

    @Override public void onCreate() {
        super.onCreate();
        client = new BridgeClient(this);
        config = new BridgeConfig(this);
        createChannel();
        startForeground(520, notification("Bağlantı hazırlanıyor…"));
        scheduler = Executors.newScheduledThreadPool(2);
        scheduler.scheduleWithFixedDelay(this::statusTick, 0, 15, TimeUnit.SECONDS);
        // V5.4.1: commandTick is a 12 s long-poll. When a PC command arrives the
        // server returns immediately; after an idle timeout we reconnect after 35 ms.
        // This removes the old 0-4 s polling delay without hammering the phone/network.
        scheduler.scheduleWithFixedDelay(this::commandTick, 0, 35, TimeUnit.MILLISECONDS);
    }

    @Override public int onStartCommand(Intent intent, int flags, int startId) {
        if (!config.enabled()) {
            stopSelf();
            return START_NOT_STICKY;
        }
        return START_STICKY;
    }

    private void statusTick() {
        try {
            if (!config.enabled()) { stopSelf(); return; }
            client.probe();
            if (BridgeState.connected) client.sendEncrypted(DeviceSnapshot.status(this));
        } catch (Exception ignored) {}
        updateNotification();
    }

    private void commandTick() {
        try {
            if (!config.enabled()) return;
            JSONObject cmd = client.pollCommand();
            if (cmd == null) return;
            String id = cmd.optString("id", "");
            String type = cmd.optString("type", "");
            JSONObject payload = cmd.optJSONObject("payload");
            if (payload == null) payload = new JSONObject();
            try {
                String detail = handleCommand(type, payload);
                client.ackCommand(id, "ok", detail);
            } catch (Exception ex) {
                client.ackCommand(id, "error", ex.getMessage());
            }
        } catch (Exception ignored) {}
    }

    private String handleCommand(String type, JSONObject payload) throws Exception {
        if ("set_clipboard".equals(type)) {
            ClipboardManager cb = (ClipboardManager) getSystemService(Context.CLIPBOARD_SERVICE);
            if (cb == null) throw new Exception("Clipboard servisi yok");
            cb.setPrimaryClip(ClipData.newPlainText("RIP", payload.optString("text", "")));
            return "Telefon panosu güncellendi";
        }
        if ("download_file".equals(type)) {
            // V5.2.5: Eski DownloadManager akisi komutu daha dosya inmeden ACK'liyordu.
            // Artik transfer burada senkron tamamlanir, SHA-256 dogrulanir ve ancak
            // ondan sonra PC'ye basarili ACK doner.
            return downloadFile(payload);
        }
        if ("device_action".equals(type)) {
            return PhoneActions.handle(this, client, payload);
        }
        if ("call_action".equals(type)) {
            return RipNotificationListener.performCallAction(
                    this, payload.optString("call_id", ""), payload.optString("action", ""));
        }
        throw new Exception("Desteklenmeyen telefon komutu: " + type);
    }

    private String downloadFile(JSONObject payload) throws Exception {
        String transferId = payload.optString("transfer_id", "").trim();
        String filename = safeFilename(payload.optString("filename", "rip-file"));
        String mime = payload.optString("mime", "application/octet-stream");
        String expectedSha = payload.optString("sha256", "").trim().toLowerCase();
        long expectedSize = payload.optLong("size", -1L);
        if (transferId.isEmpty()) throw new Exception("transfer_id yok");

        HttpURLConnection connection = (HttpURLConnection) new URL(
                client.authenticatedFileUrl(transferId)).openConnection();
        connection.setRequestMethod("GET");
        connection.setConnectTimeout(6000);
        connection.setReadTimeout(300000);
        connection.setUseCaches(false);
        connection.setRequestProperty("Accept", "*/*");

        int code = connection.getResponseCode();
        if (code < 200 || code >= 300) {
            connection.disconnect();
            throw new Exception("Dosya sunucusu HTTP " + code);
        }

        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        long written = 0L;
        Uri mediaUri = null;
        File legacyFile = null;
        boolean success = false;
        try (InputStream raw = new BufferedInputStream(connection.getInputStream())) {
            OutputStream out;
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                ContentResolver resolver = getContentResolver();
                ContentValues values = new ContentValues();
                values.put(MediaStore.Downloads.DISPLAY_NAME, filename);
                values.put(MediaStore.Downloads.MIME_TYPE, mime);
                values.put(MediaStore.Downloads.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS + "/RIP");
                values.put(MediaStore.Downloads.IS_PENDING, 1);
                mediaUri = resolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values);
                if (mediaUri == null) throw new Exception("Downloads kaydi olusturulamadi");
                out = resolver.openOutputStream(mediaUri, "w");
                if (out == null) throw new Exception("Downloads dosyasi acilamadi");
            } else {
                File dir = new File(getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS), "RIP");
                if (!dir.exists() && !dir.mkdirs()) throw new Exception("Download klasoru olusturulamadi");
                legacyFile = dedupeFile(dir, filename);
                out = new FileOutputStream(legacyFile);
            }

            try (OutputStream target = out) {
                byte[] buffer = new byte[128 * 1024];
                int n;
                while ((n = raw.read(buffer)) != -1) {
                    target.write(buffer, 0, n);
                    digest.update(buffer, 0, n);
                    written += n;
                }
                target.flush();
            }

            String actualSha = hex(digest.digest());
            if (expectedSize >= 0 && written != expectedSize) {
                throw new Exception("Boyut dogrulamasi basarisiz: " + written + "/" + expectedSize);
            }
            if (!expectedSha.isEmpty() && !expectedSha.equals(actualSha)) {
                throw new Exception("SHA-256 dogrulamasi basarisiz");
            }

            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q && mediaUri != null) {
                ContentValues ready = new ContentValues();
                ready.put(MediaStore.Downloads.IS_PENDING, 0);
                getContentResolver().update(mediaUri, ready, null, null);
            }
            success = true;
            return "Dosya alindi ✓ Downloads/RIP/" + filename;
        } finally {
            connection.disconnect();
            if (!success) {
                try {
                    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q && mediaUri != null) {
                        getContentResolver().delete(mediaUri, null, null);
                    } else if (legacyFile != null) {
                        //noinspection ResultOfMethodCallIgnored
                        legacyFile.delete();
                    }
                } catch (Exception ignored) {}
            }
        }
    }

    private static String safeFilename(String raw) {
        String name = raw == null ? "rip-file" : raw.trim();
        name = name.replace('\\', '_').replace('/', '_').replace('\u0000', '_');
        if (name.isEmpty()) name = "rip-file";
        if (name.length() > 160) name = name.substring(name.length() - 160);
        return name;
    }

    private static File dedupeFile(File dir, String filename) {
        File out = new File(dir, filename);
        if (!out.exists()) return out;
        String base = filename;
        String ext = "";
        int dot = filename.lastIndexOf('.');
        if (dot > 0) {
            base = filename.substring(0, dot);
            ext = filename.substring(dot);
        }
        for (int i = 2; i < 1000; i++) {
            File candidate = new File(dir, base + " (" + i + ")" + ext);
            if (!candidate.exists()) return candidate;
        }
        return new File(dir, System.currentTimeMillis() + "-" + filename);
    }

    private static String hex(byte[] bytes) {
        StringBuilder sb = new StringBuilder(bytes.length * 2);
        for (byte b : bytes) sb.append(String.format("%02x", b & 0xff));
        return sb.toString();
    }

    private void updateNotification() {
        NotificationManager nm = (NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE);
        if (nm == null) return;
        String text = BridgeState.connected
                ? BridgeState.route + " • " + BridgeState.latencyMs + " ms • " + BridgeState.computer
                : (BridgeState.lastError.isEmpty() ? "Bağlantı bekleniyor" : BridgeState.lastError);
        nm.notify(520, notification(text));
    }

    private Notification notification(String text) {
        Intent open = new Intent(this, MainActivity.class);
        PendingIntent pi = PendingIntent.getActivity(this, 0, open,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
        Notification.Builder b = Build.VERSION.SDK_INT >= 26
                ? new Notification.Builder(this, CHANNEL)
                : new Notification.Builder(this);
        return b.setContentTitle("RIP Mobile")
                .setContentText(text)
                .setSmallIcon(android.R.drawable.stat_notify_sync)
                .setOngoing(true)
                .setOnlyAlertOnce(true)
                .setContentIntent(pi)
                .build();
    }

    private void createChannel() {
        if (Build.VERSION.SDK_INT < 26) return;
        NotificationManager nm = (NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE);
        if (nm == null) return;
        NotificationChannel ch = new NotificationChannel(CHANNEL, "RIP Bridge", NotificationManager.IMPORTANCE_LOW);
        ch.setDescription("RIP PC bağlantısını arka planda canlı tutar");
        nm.createNotificationChannel(ch);
    }

    @Override public void onDestroy() {
        if (scheduler != null) scheduler.shutdownNow();
        super.onDestroy();
    }

    @Override public IBinder onBind(Intent intent) { return null; }

    public static void start(Context context) {
        Intent i = new Intent(context, BridgeService.class);
        if (Build.VERSION.SDK_INT >= 26) context.startForegroundService(i); else context.startService(i);
    }
}
