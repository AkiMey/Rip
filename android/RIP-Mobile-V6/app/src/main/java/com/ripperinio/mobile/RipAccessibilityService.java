package com.ripperinio.mobile;

import android.accessibilityservice.AccessibilityService;
import android.accessibilityservice.GestureDescription;
import android.graphics.Bitmap;
import android.graphics.Path;
import android.hardware.HardwareBuffer;
import android.os.Build;
import android.content.Intent;
import android.view.Display;
import android.view.accessibility.AccessibilityEvent;

import java.io.ByteArrayOutputStream;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

public class RipAccessibilityService extends AccessibilityService {
    private static volatile RipAccessibilityService instance;

    @Override protected void onServiceConnected() {
        super.onServiceConnected();
        instance = this;
    }

    @Override public void onAccessibilityEvent(AccessibilityEvent event) {}
    @Override public void onInterrupt() {}

    @Override public void onDestroy() {
        if (instance == this) instance = null;
        super.onDestroy();
    }

    public static boolean available() { return instance != null; }

    public static boolean launch(Intent intent) {
        RipAccessibilityService service = instance;
        if (service == null || intent == null) return false;
        try {
            intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
            service.startActivity(intent);
            return true;
        } catch (Exception ignored) {
            return false;
        }
    }

    public static boolean perform(String action) {
        RipAccessibilityService service = instance;
        if (service == null) return false;
        int global;
        switch (action) {
            case "home": global = GLOBAL_ACTION_HOME; break;
            case "back": global = GLOBAL_ACTION_BACK; break;
            case "recents": global = GLOBAL_ACTION_RECENTS; break;
            case "notifications": global = GLOBAL_ACTION_NOTIFICATIONS; break;
            case "quick_settings": global = GLOBAL_ACTION_QUICK_SETTINGS; break;
            case "lock_screen":
                if (Build.VERSION.SDK_INT < 28) return false;
                global = GLOBAL_ACTION_LOCK_SCREEN;
                break;
            default: return false;
        }
        return service.performGlobalAction(global);
    }

    public static boolean tap(float x, float y) {
        RipAccessibilityService service = instance;
        if (service == null || Build.VERSION.SDK_INT < 24) return false;
        try {
            Path path = new Path();
            path.moveTo(Math.max(0f, x), Math.max(0f, y));
            GestureDescription.StrokeDescription stroke =
                    new GestureDescription.StrokeDescription(path, 0, 70);
            return service.dispatchGesture(
                    new GestureDescription.Builder().addStroke(stroke).build(), null, null);
        } catch (Exception ignored) { return false; }
    }

    public static boolean swipe(float x1, float y1, float x2, float y2, long durationMs) {
        RipAccessibilityService service = instance;
        if (service == null || Build.VERSION.SDK_INT < 24) return false;
        try {
            Path path = new Path();
            path.moveTo(Math.max(0f, x1), Math.max(0f, y1));
            path.lineTo(Math.max(0f, x2), Math.max(0f, y2));
            GestureDescription.StrokeDescription stroke =
                    new GestureDescription.StrokeDescription(path, 0, Math.max(80, Math.min(1600, durationMs)));
            return service.dispatchGesture(
                    new GestureDescription.Builder().addStroke(stroke).build(), null, null);
        } catch (Exception ignored) { return false; }
    }

    /** Android 11+ screenshot through the user-enabled Accessibility service. */
    public static byte[] screenshotJpeg(int quality, int maxWidth, long timeoutMs) throws Exception {
        RipAccessibilityService service = instance;
        if (service == null) throw new Exception("Accessibility servisi açık değil");
        if (Build.VERSION.SDK_INT < 30) throw new Exception("Canlı ekran Android 11+ gerektiriyor");

        CountDownLatch latch = new CountDownLatch(1);
        AtomicReference<byte[]> bytes = new AtomicReference<>();
        AtomicReference<String> error = new AtomicReference<>();
        service.takeScreenshot(Display.DEFAULT_DISPLAY, service.getMainExecutor(),
                new AccessibilityService.TakeScreenshotCallback() {
                    @Override public void onSuccess(AccessibilityService.ScreenshotResult result) {
                        HardwareBuffer hb = null;
                        Bitmap hardware = null;
                        Bitmap software = null;
                        Bitmap scaled = null;
                        try {
                            hb = result.getHardwareBuffer();
                            hardware = Bitmap.wrapHardwareBuffer(hb, result.getColorSpace());
                            if (hardware == null) throw new Exception("Screenshot bitmap oluşturulamadı");
                            software = hardware.copy(Bitmap.Config.ARGB_8888, false);
                            if (software == null) throw new Exception("Screenshot kopyalanamadı");
                            Bitmap use = software;
                            if (maxWidth > 0 && software.getWidth() > maxWidth) {
                                int h = Math.max(1, Math.round(software.getHeight() * (maxWidth / (float) software.getWidth())));
                                scaled = Bitmap.createScaledBitmap(software, maxWidth, h, true);
                                use = scaled;
                            }
                            ByteArrayOutputStream out = new ByteArrayOutputStream(256 * 1024);
                            if (!use.compress(Bitmap.CompressFormat.JPEG, Math.max(25, Math.min(85, quality)), out)) {
                                throw new Exception("Screenshot JPEG sıkıştırılamadı");
                            }
                            bytes.set(out.toByteArray());
                        } catch (Exception ex) {
                            error.set(ex.getMessage());
                        } finally {
                            try { if (scaled != null) scaled.recycle(); } catch (Exception ignored) {}
                            try { if (software != null) software.recycle(); } catch (Exception ignored) {}
                            try { if (hb != null) hb.close(); } catch (Exception ignored) {}
                            latch.countDown();
                        }
                    }
                    @Override public void onFailure(int errorCode) {
                        error.set("Android screenshot hatası: " + errorCode);
                        latch.countDown();
                    }
                });
        if (!latch.await(Math.max(500, timeoutMs), TimeUnit.MILLISECONDS)) {
            throw new Exception("Telefon ekran görüntüsü zaman aşımı");
        }
        if (bytes.get() == null) throw new Exception(error.get() == null ? "Telefon ekran görüntüsü alınamadı" : error.get());
        return bytes.get();
    }
}
