package com.ripperinio.mobile;

import android.Manifest;
import android.app.NotificationManager;
import android.content.ContentResolver;
import android.content.ContentValues;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ApplicationInfo;
import android.content.pm.PackageManager;
import android.hardware.camera2.CameraCharacteristics;
import android.hardware.camera2.CameraManager;
import android.media.AudioManager;
import android.media.Ringtone;
import android.media.RingtoneManager;
import android.location.Location;
import android.location.LocationListener;
import android.location.LocationManager;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Looper;
import android.os.VibrationEffect;
import android.os.Vibrator;
import android.provider.MediaStore;
import android.provider.Settings;
import android.util.Base64;
import android.util.DisplayMetrics;
import android.view.KeyEvent;

import org.json.JSONObject;

import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

public final class PhoneActions {
    private static volatile boolean torchEnabled = false;
    private PhoneActions() {}

    public static String handle(Context context, BridgeClient client, JSONObject payload) throws Exception {
        String action = payload.optString("action", "").trim().toLowerCase(java.util.Locale.ROOT);
        if (action.isEmpty()) throw new Exception("Telefon işlemi belirtilmedi");

        switch (action) {
            case "refresh_inventory":
                if (!client.sendEncrypted(DeviceSnapshot.inventory(context))) throw new Exception("Telefon envanteri PC'ye gönderilemedi");
                return "Telefon envanteri güncellendi";
            case "home": case "back": case "recents": case "notifications": case "quick_settings": case "lock_screen":
                if (!RipAccessibilityService.perform(action)) throw new Exception("Accessibility izni gerekli veya işlem desteklenmiyor");
                return "Telefon işlemi uygulandı: " + action;
            case "open_app": return openApp(context, payload.optString("package", ""));
            case "open_camera": return openCamera(context);
            case "uninstall_app": return uninstallApp(context, payload.optString("package", ""));
            case "app_settings": return appSettings(context, payload.optString("package", ""));
            case "package_check": return packageCheck(context, payload.optString("package", ""));
            case "volume_set": return setVolume(context, client, payload.optInt("percent", 50), payload.optString("stream", "music"));
            case "media_play_pause": mediaKey(context, KeyEvent.KEYCODE_MEDIA_PLAY_PAUSE); return "Medya oynat/duraklat gönderildi";
            case "media_next": mediaKey(context, KeyEvent.KEYCODE_MEDIA_NEXT); return "Sonraki parça gönderildi";
            case "media_previous": mediaKey(context, KeyEvent.KEYCODE_MEDIA_PREVIOUS); return "Önceki parça gönderildi";
            case "vibrate": return vibrate(context, payload.optInt("duration_ms", 450));
            case "find_phone": return findPhone(context, payload.optInt("duration_ms", 9000));
            case "get_location": return getLocation(context);
            case "clear_notifications":
                if (!RipNotificationListener.clearAllNow()) throw new Exception("Bildirim erişimi gerekli");
                return "Telefon bildirimleri temizlendi";
            case "dismiss_notification": return dismissNotification(payload.optString("key", ""));
            case "open_notification": return openNotification(payload.optString("key", ""));
            case "reply_notification": return RipNotificationListener.replyNow(context, payload.optString("key", ""), payload.optString("text", ""));
            case "open_permission": return openPermission(context, payload.optString("permission", ""));
            case "brightness_set": return setBrightness(context, client, payload.optInt("percent", 50));
            case "screen_timeout_set": return setScreenTimeout(context, client, payload.optInt("seconds", 30));
            case "autorotate_set": return setAutoRotate(context, client, payload.optBoolean("enabled", true));
            case "dnd_toggle": return toggleDnd(context, client);
            case "dnd_set": return setDnd(context, client, payload.optBoolean("enabled", true));
            case "flashlight_toggle": return toggleFlashlight(context, client);
            case "open_system_panel": return openSystemPanel(context, payload.optString("panel", "settings"));
            case "upload_file_to_pc": return uploadFile(context, client, payload.optString("uri", ""), payload.optString("filename", ""));
            case "delete_file": return deleteFile(context, client, payload.optString("uri", ""));
            case "rename_file": return renameFile(context, client, payload.optString("uri", ""), payload.optString("new_name", ""));
            case "install_apk": return installApk(context, payload.optString("uri", ""));
            case "screen_snapshot": return screenSnapshot(context, client, payload.optInt("quality", 45), payload.optInt("max_width", 720));
            case "screen_stream_start": return screenStreamStart(context, payload);
            case "screen_stream_stop": return screenStreamStop(context);
            case "screen_tap": return screenTap(context, payload);
            case "screen_swipe": return screenSwipe(context, payload);
            default: throw new Exception("Desteklenmeyen telefon işlemi: " + action);
        }
    }

    private static String openCamera(Context context) throws Exception {
        Intent intent = new Intent(MediaStore.INTENT_ACTION_STILL_IMAGE_CAMERA);
        if (intent.resolveActivity(context.getPackageManager()) == null) {
            intent = new Intent("android.media.action.IMAGE_CAPTURE");
        }
        if (intent.resolveActivity(context.getPackageManager()) == null) throw new Exception("Telefon kamera uygulaması bulunamadı");
        startActivity(context, intent);
        return "Telefon kamera uygulaması açıldı";
    }

    private static String openApp(Context context, String packageName) throws Exception {
        String pkg = cleanPackage(packageName);
        Intent launch = context.getPackageManager().getLaunchIntentForPackage(pkg);
        if (launch == null) throw new Exception("Uygulama açılamıyor veya launch activity yok");
        startActivity(context, launch);
        return "Uygulama açıldı: " + pkg;
    }

    private static String uninstallApp(Context context, String packageName) throws Exception {
        String pkg = cleanPackage(packageName);
        if (pkg.equals(context.getPackageName())) throw new Exception("RIP Mobile kendisini bu panelden kaldıramaz");
        PackageManager pm = context.getPackageManager();
        ApplicationInfo info;
        try {
            if (Build.VERSION.SDK_INT >= 33) info = pm.getApplicationInfo(pkg, PackageManager.ApplicationInfoFlags.of(0));
            else info = pm.getApplicationInfo(pkg, 0);
        } catch (PackageManager.NameNotFoundException ex) { throw new Exception("Uygulama telefonda kurulu değil: " + pkg); }

        Intent intent = new Intent(Intent.ACTION_UNINSTALL_PACKAGE, Uri.fromParts("package", pkg, null));
        intent.putExtra(Intent.EXTRA_RETURN_RESULT, false);
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP);
        if (intent.resolveActivity(pm) == null) throw new Exception("Android kaldırma ekranı bu cihazda bulunamadı");
        startActivity(context, intent);
        boolean system = (info.flags & ApplicationInfo.FLAG_SYSTEM) != 0;
        return (system ? "Sistem uygulaması için Android kaldırma/devre dışı bırakma ekranı açıldı: " : "Android kaldırma onayı açıldı: ") + pkg;
    }

    private static String packageCheck(Context context, String packageName) throws Exception {
        String pkg = cleanPackage(packageName);
        try {
            if (Build.VERSION.SDK_INT >= 33) context.getPackageManager().getApplicationInfo(pkg, PackageManager.ApplicationInfoFlags.of(0));
            else context.getPackageManager().getApplicationInfo(pkg, 0);
            return "PACKAGE_PRESENT";
        } catch (PackageManager.NameNotFoundException ex) {
            return "PACKAGE_MISSING";
        }
    }

    private static String appSettings(Context context, String packageName) throws Exception {
        String pkg = cleanPackage(packageName);
        startActivity(context, new Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:" + pkg)));
        return "Uygulama ayarları açıldı: " + pkg;
    }

    private static int streamId(String stream) {
        String s = stream == null ? "music" : stream.trim().toLowerCase(java.util.Locale.ROOT);
        if (s.equals("ring")) return AudioManager.STREAM_RING;
        if (s.equals("alarm")) return AudioManager.STREAM_ALARM;
        if (s.equals("notification")) return AudioManager.STREAM_NOTIFICATION;
        return AudioManager.STREAM_MUSIC;
    }

    private static String streamLabel(String stream) {
        String s = stream == null ? "music" : stream.trim().toLowerCase(java.util.Locale.ROOT);
        if (s.equals("ring")) return "Zil";
        if (s.equals("alarm")) return "Alarm";
        if (s.equals("notification")) return "Bildirim";
        return "Medya";
    }

    private static String setVolume(Context context, BridgeClient client, int percent, String streamName) throws Exception {
        AudioManager a = (AudioManager) context.getSystemService(Context.AUDIO_SERVICE);
        if (a == null) throw new Exception("Ses servisi yok");
        int stream = streamId(streamName);
        int requested = Math.max(0, Math.min(100, percent));
        int min = 0;
        if (Build.VERSION.SDK_INT >= 28) try { min = a.getStreamMinVolume(stream); } catch (Throwable ignored) {}
        int max = Math.max(min + 1, a.getStreamMaxVolume(stream));
        int span = Math.max(1, max - min);
        int target = requested <= 0 ? min : requested >= 100 ? max : min + Math.round(span * (requested / 100f));
        target = Math.max(min, Math.min(max, target));
        a.setStreamVolume(stream, target, AudioManager.FLAG_REMOVE_SOUND_AND_VIBRATE);
        try { Thread.sleep(80L); } catch (InterruptedException ignored) { Thread.currentThread().interrupt(); }
        int actual = Math.max(min, Math.min(max, a.getStreamVolume(stream)));
        int actualPercent = actual <= min ? 0 : Math.round((actual - min) * 100f / span);
        try { client.sendEncrypted(DeviceSnapshot.status(context)); } catch (Exception ignored) {}
        return streamLabel(streamName) + " sesi %" + Math.max(0, Math.min(100, actualPercent)) + " (kademe " + actual + "/" + max + ")";
    }

    private static void mediaKey(Context context, int key) throws Exception {
        AudioManager a = (AudioManager) context.getSystemService(Context.AUDIO_SERVICE);
        if (a == null) throw new Exception("Medya servisi yok");
        long now = android.os.SystemClock.uptimeMillis();
        a.dispatchMediaKeyEvent(new KeyEvent(now, now, KeyEvent.ACTION_DOWN, key, 0));
        a.dispatchMediaKeyEvent(new KeyEvent(now, now, KeyEvent.ACTION_UP, key, 0));
    }

    private static String findPhone(Context context, int duration) throws Exception {
        int ms = Math.max(2500, Math.min(15000, duration));
        Vibrator vibrator = (Vibrator) context.getSystemService(Context.VIBRATOR_SERVICE);
        if (vibrator != null && vibrator.hasVibrator()) {
            long[] pattern = new long[]{0, 450, 220, 450, 220, 850, 220, 450};
            if (Build.VERSION.SDK_INT >= 26) vibrator.vibrate(VibrationEffect.createWaveform(pattern, 0));
            else vibrator.vibrate(pattern, 0);
        }
        final Ringtone ringtone;
        try {
            Uri uri = RingtoneManager.getDefaultUri(RingtoneManager.TYPE_RINGTONE);
            ringtone = RingtoneManager.getRingtone(context, uri);
            if (ringtone != null) ringtone.play();
        } catch (Exception ignored) {
            throw new Exception("Telefon titreşti ancak zil sesi başlatılamadı");
        }
        new Thread(() -> {
            try { Thread.sleep(ms); } catch (InterruptedException ignored) {}
            try { if (ringtone != null && ringtone.isPlaying()) ringtone.stop(); } catch (Exception ignored) {}
            try { if (vibrator != null) vibrator.cancel(); } catch (Exception ignored) {}
        }, "rip-find-phone-stop").start();
        return "Telefon bulma alarmı başlatıldı";
    }

    private static String vibrate(Context context, int duration) throws Exception {
        Vibrator v = (Vibrator) context.getSystemService(Context.VIBRATOR_SERVICE);
        if (v == null || !v.hasVibrator()) throw new Exception("Titreşim motoru yok");
        int ms = Math.max(80, Math.min(3000, duration));
        if (Build.VERSION.SDK_INT >= 26) v.vibrate(VibrationEffect.createOneShot(ms, VibrationEffect.DEFAULT_AMPLITUDE)); else v.vibrate(ms);
        return "Telefon titreştirildi";
    }

    private static String dismissNotification(String key) throws Exception {
        if (key == null || key.trim().isEmpty()) throw new Exception("Bildirim anahtarı yok");
        if (!RipNotificationListener.dismissNow(key.trim())) throw new Exception("Bildirim bulunamadı veya bildirim erişimi kapalı");
        return "Bildirim kapatıldı";
    }

    private static String openNotification(String key) throws Exception {
        if (key == null || key.trim().isEmpty()) throw new Exception("Bildirim anahtarı yok");
        if (!RipNotificationListener.openNow(key.trim())) throw new Exception("Bildirim açılamadı; uygulama bildirimi tıklanabilir olmayabilir");
        return "Bildirim telefonda açıldı";
    }

    private static String setBrightness(Context context, BridgeClient client, int percent) throws Exception {
        if (!Settings.System.canWrite(context)) throw new Exception("Sistem ayarlarını değiştirme izni gerekli (İZİNLER > SİSTEM AYARLARI)");
        int pct = Math.max(1, Math.min(100, percent));
        int raw = Math.max(1, Math.min(255, Math.round(pct * 255f / 100f)));
        Settings.System.putInt(context.getContentResolver(), Settings.System.SCREEN_BRIGHTNESS_MODE, Settings.System.SCREEN_BRIGHTNESS_MODE_MANUAL);
        Settings.System.putInt(context.getContentResolver(), Settings.System.SCREEN_BRIGHTNESS, raw);
        try { client.sendEncrypted(DeviceSnapshot.status(context)); } catch (Exception ignored) {}
        return "Ekran parlaklığı %" + pct;
    }

    private static String setScreenTimeout(Context context, BridgeClient client, int seconds) throws Exception {
        if (!Settings.System.canWrite(context)) throw new Exception("Sistem ayarlarını değiştirme izni gerekli");
        int sec = Math.max(15, Math.min(1800, seconds));
        Settings.System.putInt(context.getContentResolver(), Settings.System.SCREEN_OFF_TIMEOUT, sec * 1000);
        try { client.sendEncrypted(DeviceSnapshot.status(context)); } catch (Exception ignored) {}
        return "Ekran zaman aşımı " + sec + " saniye";
    }

    private static String setAutoRotate(Context context, BridgeClient client, boolean enabled) throws Exception {
        if (!Settings.System.canWrite(context)) throw new Exception("Sistem ayarlarını değiştirme izni gerekli");
        Settings.System.putInt(context.getContentResolver(), Settings.System.ACCELEROMETER_ROTATION, enabled ? 1 : 0);
        try { client.sendEncrypted(DeviceSnapshot.status(context)); } catch (Exception ignored) {}
        return "Otomatik döndürme " + (enabled ? "açıldı" : "kapatıldı");
    }

    private static String toggleDnd(Context context, BridgeClient client) throws Exception {
        NotificationManager nm = (NotificationManager) context.getSystemService(Context.NOTIFICATION_SERVICE);
        if (nm == null || !nm.isNotificationPolicyAccessGranted()) throw new Exception("Rahatsız Etmeyin erişimi gerekli");
        boolean on = nm.getCurrentInterruptionFilter() != NotificationManager.INTERRUPTION_FILTER_ALL;
        nm.setInterruptionFilter(on ? NotificationManager.INTERRUPTION_FILTER_ALL : NotificationManager.INTERRUPTION_FILTER_PRIORITY);
        try { client.sendEncrypted(DeviceSnapshot.status(context)); } catch (Exception ignored) {}
        return "Rahatsız Etmeyin " + (on ? "kapatıldı" : "açıldı");
    }

    private static String setDnd(Context context, BridgeClient client, boolean enabled) throws Exception {
        NotificationManager nm = (NotificationManager) context.getSystemService(Context.NOTIFICATION_SERVICE);
        if (nm == null || !nm.isNotificationPolicyAccessGranted()) throw new Exception("Rahatsız Etmeyin erişimi gerekli");
        nm.setInterruptionFilter(enabled ? NotificationManager.INTERRUPTION_FILTER_PRIORITY : NotificationManager.INTERRUPTION_FILTER_ALL);
        try { client.sendEncrypted(DeviceSnapshot.status(context)); } catch (Exception ignored) {}
        return "Rahatsız Etmeyin " + (enabled ? "açıldı" : "kapatıldı");
    }

    private static String toggleFlashlight(Context context, BridgeClient client) throws Exception {
        if (context.checkSelfPermission(Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) throw new Exception("Fener için Kamera iznini telefondan ver");
        CameraManager cm = (CameraManager) context.getSystemService(Context.CAMERA_SERVICE);
        if (cm == null) throw new Exception("Kamera servisi yok");
        String chosen = null;
        for (String id : cm.getCameraIdList()) {
            Boolean flash = cm.getCameraCharacteristics(id).get(CameraCharacteristics.FLASH_INFO_AVAILABLE);
            if (Boolean.TRUE.equals(flash)) { chosen = id; break; }
        }
        if (chosen == null) throw new Exception("Flaş bulunamadı");
        torchEnabled = !torchEnabled;
        cm.setTorchMode(chosen, torchEnabled);
        try { client.sendEncrypted(DeviceSnapshot.status(context)); } catch (Exception ignored) {}
        return "El feneri " + (torchEnabled ? "açıldı" : "kapatıldı");
    }

    private static String openSystemPanel(Context context, String panel) throws Exception {
        String p = panel == null ? "settings" : panel.trim().toLowerCase(java.util.Locale.ROOT);
        String action;
        switch (p) {
            case "wifi": action = Settings.ACTION_WIFI_SETTINGS; break;
            case "bluetooth": action = Settings.ACTION_BLUETOOTH_SETTINGS; break;
            case "display": action = Settings.ACTION_DISPLAY_SETTINGS; break;
            case "sound": action = Settings.ACTION_SOUND_SETTINGS; break;
            case "apps": action = Settings.ACTION_APPLICATION_SETTINGS; break;
            default: action = Settings.ACTION_SETTINGS; break;
        }
        startActivity(context, new Intent(action));
        return "Telefon ayar ekranı açıldı: " + p;
    }

    private static String getLocation(Context context) throws Exception {
        boolean fine = Build.VERSION.SDK_INT < 23 || context.checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED;
        boolean coarse = Build.VERSION.SDK_INT < 23 || context.checkSelfPermission(Manifest.permission.ACCESS_COARSE_LOCATION) == PackageManager.PERMISSION_GRANTED;
        if (!fine && !coarse) {
            throw new Exception("Konum izni gerekli. RIP Mobile > YETKİ MERKEZİ > KONUM İZNİ'ni aç.");
        }

        LocationManager manager = (LocationManager) context.getSystemService(Context.LOCATION_SERVICE);
        if (manager == null) throw new Exception("Android konum servisi bulunamadı");

        Location best = null;
        try {
            List<String> providers = manager.getProviders(true);
            for (String provider : providers) {
                try {
                    Location row = manager.getLastKnownLocation(provider);
                    if (row == null) continue;
                    if (best == null || row.getTime() > best.getTime() ||
                            (row.getTime() == best.getTime() && row.hasAccuracy() && (!best.hasAccuracy() || row.getAccuracy() < best.getAccuracy()))) {
                        best = row;
                    }
                } catch (SecurityException ignored) {}
            }
        } catch (Exception ignored) {}

        // If the cached fix is old, briefly ask Android for a fresh one. Some
        // vendors restrict background fixes; in that case the best cached fix
        // is still returned instead of breaking the bridge.
        long now = System.currentTimeMillis();
        boolean stale = best == null || best.getTime() <= 0 || now - best.getTime() > 120000L;
        if (stale) {
            String provider = null;
            try {
                if (fine && manager.isProviderEnabled(LocationManager.GPS_PROVIDER)) provider = LocationManager.GPS_PROVIDER;
                else if (manager.isProviderEnabled(LocationManager.NETWORK_PROVIDER)) provider = LocationManager.NETWORK_PROVIDER;
            } catch (Exception ignored) {}
            if (provider != null) {
                AtomicReference<Location> fresh = new AtomicReference<>();
                CountDownLatch latch = new CountDownLatch(1);
                LocationListener listener = new LocationListener() {
                    @Override public void onLocationChanged(Location location) { fresh.set(location); latch.countDown(); }
                    @Override public void onProviderEnabled(String provider) {}
                    @Override public void onProviderDisabled(String provider) {}
                    @Override public void onStatusChanged(String provider, int status, Bundle extras) {}
                };
                try {
                    manager.requestSingleUpdate(provider, listener, Looper.getMainLooper());
                    latch.await(7, TimeUnit.SECONDS);
                } catch (Exception ignored) {
                } finally {
                    try { manager.removeUpdates(listener); } catch (Exception ignored) {}
                }
                if (fresh.get() != null) best = fresh.get();
            }
        }

        if (best == null) {
            throw new Exception("Konum alınamadı. Telefonda Konum/GPS açık olsun ve RIP Mobile'a konum izni ver.");
        }

        JSONObject out = new JSONObject();
        out.put("kind", "location");
        out.put("latitude", best.getLatitude());
        out.put("longitude", best.getLongitude());
        out.put("accuracy_m", best.hasAccuracy() ? best.getAccuracy() : -1);
        out.put("provider", best.getProvider() == null ? "Android" : best.getProvider());
        out.put("location_time", best.getTime());
        return out.toString();
    }

    private static String openPermission(Context context, String permission) throws Exception {
        String p = permission == null ? "" : permission.trim().toLowerCase(java.util.Locale.ROOT);
        Intent intent;
        switch (p) {
            case "notification": intent = new Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS); break;
            case "accessibility": intent = new Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS); break;
            case "overlay": intent = new Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, Uri.parse("package:" + context.getPackageName())); break;
            case "battery": intent = new Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:" + context.getPackageName())); break;
            case "usage": intent = new Intent(Settings.ACTION_USAGE_ACCESS_SETTINGS); break;
            case "write_settings": intent = new Intent(Settings.ACTION_MANAGE_WRITE_SETTINGS, Uri.parse("package:" + context.getPackageName())); break;
            case "install_unknown": intent = new Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:" + context.getPackageName())); break;
            case "dnd": intent = new Intent(Settings.ACTION_NOTIFICATION_POLICY_ACCESS_SETTINGS); break;
            case "answer_calls":
                intent = new Intent(context, MainActivity.class);
                intent.putExtra("request_permission", "answer_calls");
                break;
            case "location":
                intent = new Intent(context, MainActivity.class);
                intent.putExtra("request_permission", "location");
                break;
            case "camera":
            default: intent = new Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:" + context.getPackageName())); break;
        }
        startActivity(context, intent);
        return "Telefon izin ekranı açıldı: " + p;
    }

    private static String uploadFile(Context context, BridgeClient client, String rawUri, String filename) throws Exception {
        if (rawUri == null || rawUri.trim().isEmpty()) throw new Exception("Dosya URI yok");
        JSONObject result = client.uploadToPc(Uri.parse(rawUri), filename);
        return "Dosya PC'ye gönderildi: " + result.optString("path", filename);
    }

    private static String deleteFile(Context context, BridgeClient client, String rawUri) throws Exception {
        if (rawUri == null || rawUri.trim().isEmpty()) throw new Exception("Dosya URI yok");
        int deleted = context.getContentResolver().delete(Uri.parse(rawUri), null, null);
        if (deleted <= 0) throw new Exception("Dosya silinemedi veya artık mevcut değil");
        try { client.sendEncrypted(DeviceSnapshot.inventory(context)); } catch (Exception ignored) {}
        return "Telefon dosyası silindi";
    }

    private static String renameFile(Context context, BridgeClient client, String rawUri, String newName) throws Exception {
        if (rawUri == null || rawUri.trim().isEmpty()) throw new Exception("Dosya URI yok");
        String name = newName == null ? "" : newName.trim().replace('/', '_').replace('\\', '_');
        if (name.isEmpty()) throw new Exception("Yeni dosya adı boş");
        ContentValues values = new ContentValues();
        values.put(MediaStore.MediaColumns.DISPLAY_NAME, name);
        int changed = context.getContentResolver().update(Uri.parse(rawUri), values, null, null);
        if (changed <= 0) throw new Exception("Dosya yeniden adlandırılamadı");
        try { client.sendEncrypted(DeviceSnapshot.inventory(context)); } catch (Exception ignored) {}
        return "Dosyanın adı değiştirildi: " + name;
    }

    private static String installApk(Context context, String rawUri) throws Exception {
        if (rawUri == null || rawUri.trim().isEmpty()) throw new Exception("APK URI yok");
        if (Build.VERSION.SDK_INT >= 26 && !context.getPackageManager().canRequestPackageInstalls()) throw new Exception("RIP Mobile için 'Bilinmeyen uygulama yükle' iznini aç");
        Uri uri = Uri.parse(rawUri);
        Intent intent = new Intent(Intent.ACTION_VIEW);
        intent.setDataAndType(uri, "application/vnd.android.package-archive");
        intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION | Intent.FLAG_ACTIVITY_NEW_TASK);
        startActivity(context, intent);
        return "APK kurulum ekranı telefonda açıldı";
    }

    private static void startActivity(Context context, Intent intent) throws Exception {
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        if (RipAccessibilityService.launch(intent)) return;
        try { context.startActivity(intent); }
        catch (Exception ex) { throw new Exception("Android ekranı arka plandan açılamadı. RIP Mobile'ı öne getir veya Accessibility iznini aç."); }
    }

    private static String screenStreamStart(Context context, JSONObject payload) throws Exception {
        int fps = Math.max(4, Math.min(30, payload.optInt("fps", 12)));
        int width = Math.max(420, Math.min(1080, payload.optInt("max_width", 720)));
        int quality = Math.max(30, Math.min(75, payload.optInt("quality", 50)));
        Intent intent = new Intent(context, MainActivity.class);
        intent.putExtra("request_screen_capture", true);
        intent.putExtra("screen_fps", fps);
        intent.putExtra("screen_width", width);
        intent.putExtra("screen_quality", quality);
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_SINGLE_TOP | Intent.FLAG_ACTIVITY_CLEAR_TOP);
        startActivity(context, intent);
        return "Telefonda ekran paylaşım onayı açıldı • hedef " + fps + " FPS";
    }

    private static String screenStreamStop(Context context) {
        ScreenStreamService.stop(context);
        return "Akıcı ekran yayını durduruldu";
    }

    private static String screenSnapshot(Context context, BridgeClient client, int quality, int maxWidth) throws Exception {
        byte[] jpeg = RipAccessibilityService.screenshotJpeg(quality, Math.max(360, Math.min(1080, maxWidth)), 2200);
        DisplayMetrics dm = context.getResources().getDisplayMetrics();
        JSONObject frame = new JSONObject();
        frame.put("kind", "screen_frame");
        frame.put("jpeg_b64", Base64.encodeToString(jpeg, Base64.NO_WRAP));
        frame.put("screen_width", dm.widthPixels);
        frame.put("screen_height", dm.heightPixels);
        frame.put("captured_at", System.currentTimeMillis());
        if (!client.sendEncrypted(frame)) throw new Exception("Ekran karesi PC'ye gönderilemedi");
        return "Telefon ekran karesi gönderildi";
    }

    private static String screenTap(Context context, JSONObject payload) throws Exception {
        float x = (float) payload.optDouble("x", -1);
        float y = (float) payload.optDouble("y", -1);
        if (x < 0 || y < 0 || !RipAccessibilityService.tap(x, y))
            throw new Exception("Ekrana dokunma uygulanamadı; Accessibility iznini kontrol et");
        return "Telefon ekranına dokunuldu";
    }

    private static String screenSwipe(Context context, JSONObject payload) throws Exception {
        float x1=(float)payload.optDouble("x1",-1), y1=(float)payload.optDouble("y1",-1);
        float x2=(float)payload.optDouble("x2",-1), y2=(float)payload.optDouble("y2",-1);
        long duration=payload.optLong("duration_ms",350);
        if (x1<0||y1<0||x2<0||y2<0||!RipAccessibilityService.swipe(x1,y1,x2,y2,duration))
            throw new Exception("Ekran kaydırma uygulanamadı; Accessibility iznini kontrol et");
        return "Telefon ekranında kaydırma yapıldı";
    }

    private static String cleanPackage(String packageName) throws Exception {
        String pkg = packageName == null ? "" : packageName.trim();
        if (pkg.isEmpty()) throw new Exception("Paket adı yok");
        return pkg;
    }
}
