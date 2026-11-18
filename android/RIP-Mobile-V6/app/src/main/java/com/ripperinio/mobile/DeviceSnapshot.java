package com.ripperinio.mobile;

import android.Manifest;
import android.app.AppOpsManager;
import android.app.NotificationManager;
import android.app.usage.UsageStats;
import android.app.usage.UsageStatsManager;
import android.content.ComponentName;
import android.content.ContentUris;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.content.pm.ApplicationInfo;
import android.content.pm.PackageInfo;
import android.content.pm.PackageManager;
import android.database.Cursor;
import android.media.AudioManager;
import android.net.Uri;
import android.os.BatteryManager;
import android.os.Build;
import android.os.Environment;
import android.os.PowerManager;
import android.os.Process;
import android.provider.MediaStore;
import android.provider.Settings;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.File;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

public final class DeviceSnapshot {
    private DeviceSnapshot() {}

    public static JSONObject status(Context context) throws Exception {
        JSONObject p = new JSONObject();
        Intent battery = context.registerReceiver(null, new IntentFilter(Intent.ACTION_BATTERY_CHANGED));
        int level = battery == null ? -1 : battery.getIntExtra(BatteryManager.EXTRA_LEVEL, -1);
        int scale = battery == null ? 100 : battery.getIntExtra(BatteryManager.EXTRA_SCALE, 100);
        int pct = level < 0 ? -1 : Math.round(level * 100f / Math.max(1, scale));
        int batteryStatus = battery == null ? -1 : battery.getIntExtra(BatteryManager.EXTRA_STATUS, -1);
        boolean charging = batteryStatus == BatteryManager.BATTERY_STATUS_CHARGING || batteryStatus == BatteryManager.BATTERY_STATUS_FULL;
        float temp = battery == null ? -1f : battery.getIntExtra(BatteryManager.EXTRA_TEMPERATURE, -10) / 10f;
        int voltageMv = battery == null ? -1 : battery.getIntExtra(BatteryManager.EXTRA_VOLTAGE, -1);
        int health = battery == null ? -1 : battery.getIntExtra(BatteryManager.EXTRA_HEALTH, -1);

        AudioManager audio = (AudioManager) context.getSystemService(Context.AUDIO_SERVICE);
        String ringer = "unknown";
        if (audio != null) {
            int mode = audio.getRingerMode();
            ringer = mode == AudioManager.RINGER_MODE_SILENT ? "silent" :
                    mode == AudioManager.RINGER_MODE_VIBRATE ? "vibrate" : "normal";
        }

        File root = Environment.getDataDirectory();
        p.put("kind", "status");
        p.put("device", Build.MANUFACTURER + " " + Build.MODEL);
        p.put("android_version", Build.VERSION.RELEASE);
        p.put("sdk", Build.VERSION.SDK_INT);
        p.put("battery_percent", pct);
        p.put("charging", charging);
        p.put("battery_temp_c", temp);
        p.put("battery_voltage_mv", voltageMv);
        p.put("battery_health", batteryHealthLabel(health));
        p.put("ringer_mode", ringer);
        p.put("storage_free_bytes", root.getUsableSpace());
        p.put("storage_total_bytes", root.getTotalSpace());
        p.put("wifi", networkLabel(context));
        p.put("bridge_version", "6.2.1b");
        p.put("bridge_service", BridgeState.route);
        p.put("bridge_latency_ms", BridgeState.latencyMs);
        p.put("notification_access", notificationAccess(context));
        p.put("accessibility_access", accessibilityAccess(context));
        p.put("overlay_access", Settings.canDrawOverlays(context));
        p.put("battery_optimization_ignored", batteryOptimizationIgnored(context));
        p.put("usage_access", usageAccess(context));
        p.put("write_settings_access", Settings.System.canWrite(context));
        p.put("install_unknown_access", installUnknownAccess(context));
        p.put("dnd_access", dndAccess(context));
        p.put("camera_access", cameraAccess(context));
        p.put("answer_calls_access", answerCallsAccess(context));
        p.put("location_access", locationAccess(context));
        p.put("screen_stream_active", ScreenStreamService.isRunning());

        JSONObject volumes = volumeSnapshot(context);
        p.put("volumes", volumes);
        JSONObject music = volumes.optJSONObject("music");
        if (music == null) music = new JSONObject();
        p.put("music_volume_percent", music.optInt("percent", -1));
        p.put("music_volume_level", music.optInt("level", -1));
        p.put("music_volume_min", music.optInt("min", 0));
        p.put("music_volume_max", music.optInt("max", -1));
        putDeviceSettings(context, p);
        return p;
    }

    public static JSONObject inventory(Context context) throws Exception {
        JSONObject p = new JSONObject();
        p.put("kind", "inventory");
        p.put("device", Build.MANUFACTURER + " " + Build.MODEL);
        p.put("android_version", Build.VERSION.RELEASE);
        p.put("sdk", Build.VERSION.SDK_INT);

        JSONObject volumes = volumeSnapshot(context);
        p.put("volumes", volumes);
        JSONObject music = volumes.optJSONObject("music");
        if (music == null) music = new JSONObject();
        p.put("music_volume_percent", music.optInt("percent", -1));
        p.put("music_volume_level", music.optInt("level", -1));
        p.put("music_volume_min", music.optInt("min", 0));
        p.put("music_volume_max", music.optInt("max", -1));

        JSONObject permissions = new JSONObject();
        permissions.put("notification", notificationAccess(context));
        permissions.put("accessibility", accessibilityAccess(context));
        permissions.put("overlay", Settings.canDrawOverlays(context));
        permissions.put("battery_ignored", batteryOptimizationIgnored(context));
        permissions.put("usage", usageAccess(context));
        permissions.put("write_settings", Settings.System.canWrite(context));
        permissions.put("install_unknown", installUnknownAccess(context));
        permissions.put("dnd", dndAccess(context));
        permissions.put("camera", cameraAccess(context));
        permissions.put("answer_calls", answerCallsAccess(context));
        permissions.put("location", locationAccess(context));
        permissions.put("bridge_enabled", new BridgeConfig(context).enabled());
        permissions.put("screen_stream_active", ScreenStreamService.isRunning());
        p.put("permissions", permissions);
        putDeviceSettings(context, p);
        p.put("apps", installedApps(context));
        p.put("downloads", ripDownloads(context));
        return p;
    }

    private static void putDeviceSettings(Context context, JSONObject p) throws Exception {
        int brightness = Settings.System.getInt(context.getContentResolver(), Settings.System.SCREEN_BRIGHTNESS, -1);
        int mode = Settings.System.getInt(context.getContentResolver(), Settings.System.SCREEN_BRIGHTNESS_MODE,
                Settings.System.SCREEN_BRIGHTNESS_MODE_MANUAL);
        int timeout = Settings.System.getInt(context.getContentResolver(), Settings.System.SCREEN_OFF_TIMEOUT, -1);
        int rotate = Settings.System.getInt(context.getContentResolver(), Settings.System.ACCELEROMETER_ROTATION, 1);
        int brightnessPct = brightness < 0 ? -1 : Math.max(0, Math.min(100, Math.round(brightness * 100f / 255f)));
        p.put("brightness_percent", brightnessPct);
        p.put("brightness_auto", mode == Settings.System.SCREEN_BRIGHTNESS_MODE_AUTOMATIC);
        p.put("screen_timeout_ms", timeout);
        p.put("auto_rotate", rotate == 1);
        NotificationManager nm = (NotificationManager) context.getSystemService(Context.NOTIFICATION_SERVICE);
        p.put("dnd_filter", nm == null ? -1 : nm.getCurrentInterruptionFilter());
    }

    public static boolean notificationAccess(Context context) {
        String flat = Settings.Secure.getString(context.getContentResolver(), "enabled_notification_listeners");
        if (flat == null || flat.isEmpty()) return false;
        String mine = context.getPackageName();
        for (String name : flat.split(":")) {
            ComponentName cn = ComponentName.unflattenFromString(name);
            if (cn != null && mine.equals(cn.getPackageName())) return true;
        }
        return false;
    }

    public static boolean accessibilityAccess(Context context) {
        String flat = Settings.Secure.getString(context.getContentResolver(), Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES);
        if (flat == null || flat.isEmpty()) return false;
        String mine = context.getPackageName();
        for (String name : flat.split(":")) {
            ComponentName cn = ComponentName.unflattenFromString(name);
            if (cn != null && mine.equals(cn.getPackageName()) && cn.getClassName().endsWith("RipAccessibilityService")) return true;
        }
        return RipAccessibilityService.available();
    }

    public static boolean batteryOptimizationIgnored(Context context) {
        try {
            PowerManager pm = (PowerManager) context.getSystemService(Context.POWER_SERVICE);
            return pm != null && pm.isIgnoringBatteryOptimizations(context.getPackageName());
        } catch (Exception ignored) { return false; }
    }

    public static boolean usageAccess(Context context) {
        try {
            AppOpsManager ops = (AppOpsManager) context.getSystemService(Context.APP_OPS_SERVICE);
            if (ops == null) return false;
            int mode;
            if (Build.VERSION.SDK_INT >= 29) {
                mode = ops.unsafeCheckOpNoThrow(AppOpsManager.OPSTR_GET_USAGE_STATS, Process.myUid(), context.getPackageName());
            } else {
                mode = ops.checkOpNoThrow(AppOpsManager.OPSTR_GET_USAGE_STATS, Process.myUid(), context.getPackageName());
            }
            return mode == AppOpsManager.MODE_ALLOWED;
        } catch (Exception ignored) { return false; }
    }

    public static boolean dndAccess(Context context) {
        try {
            NotificationManager nm = (NotificationManager) context.getSystemService(Context.NOTIFICATION_SERVICE);
            return nm != null && nm.isNotificationPolicyAccessGranted();
        } catch (Exception ignored) { return false; }
    }

    public static boolean installUnknownAccess(Context context) {
        try { return Build.VERSION.SDK_INT < 26 || context.getPackageManager().canRequestPackageInstalls(); }
        catch (Exception ignored) { return false; }
    }

    public static boolean cameraAccess(Context context) {
        try { return context.checkSelfPermission(Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED; }
        catch (Exception ignored) { return false; }
    }


    public static boolean locationAccess(Context context) {
        try {
            if (Build.VERSION.SDK_INT < 23) return true;
            return context.checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED
                    || context.checkSelfPermission(Manifest.permission.ACCESS_COARSE_LOCATION) == PackageManager.PERMISSION_GRANTED;
        } catch (Exception ignored) { return false; }
    }

    public static boolean answerCallsAccess(Context context) {
        try {
            return Build.VERSION.SDK_INT < 26 || context.checkSelfPermission(Manifest.permission.ANSWER_PHONE_CALLS) == PackageManager.PERMISSION_GRANTED;
        } catch (Exception ignored) { return false; }
    }

    private static JSONObject volumeSnapshot(Context context) {
        JSONObject out = new JSONObject();
        try {
            out.put("music", streamVolume(context, AudioManager.STREAM_MUSIC));
            out.put("ring", streamVolume(context, AudioManager.STREAM_RING));
            out.put("alarm", streamVolume(context, AudioManager.STREAM_ALARM));
            out.put("notification", streamVolume(context, AudioManager.STREAM_NOTIFICATION));
        } catch (Exception ignored) {}
        return out;
    }

    private static JSONObject streamVolume(Context context, int stream) {
        JSONObject out = new JSONObject();
        try {
            AudioManager a = (AudioManager) context.getSystemService(Context.AUDIO_SERVICE);
            if (a == null) throw new Exception("audio unavailable");
            int min = 0;
            if (Build.VERSION.SDK_INT >= 28) {
                try { min = a.getStreamMinVolume(stream); } catch (Throwable ignored) {}
            }
            int max = Math.max(min + 1, a.getStreamMaxVolume(stream));
            int now = Math.max(min, Math.min(max, a.getStreamVolume(stream)));
            int span = Math.max(1, max - min);
            int percent = now <= min ? 0 : Math.round((now - min) * 100f / span);
            out.put("percent", Math.max(0, Math.min(100, percent)));
            out.put("level", now);
            out.put("min", min);
            out.put("max", max);
        } catch (Exception ignored) {
            try { out.put("percent", -1); out.put("level", -1); out.put("min", 0); out.put("max", -1); } catch (Exception ignored2) {}
        }
        return out;
    }

    private static Map<String, Long> lastUsedMap(Context context) {
        Map<String, Long> map = new HashMap<>();
        if (!usageAccess(context)) return map;
        try {
            UsageStatsManager usm = (UsageStatsManager) context.getSystemService(Context.USAGE_STATS_SERVICE);
            if (usm == null) return map;
            long end = System.currentTimeMillis();
            long start = end - 370L * 24L * 60L * 60L * 1000L;
            List<UsageStats> rows = usm.queryUsageStats(UsageStatsManager.INTERVAL_DAILY, start, end);
            if (rows == null) return map;
            for (UsageStats row : rows) {
                if (row == null || row.getPackageName() == null) continue;
                long old = map.containsKey(row.getPackageName()) ? map.get(row.getPackageName()) : 0L;
                map.put(row.getPackageName(), Math.max(old, row.getLastTimeUsed()));
            }
        } catch (Exception ignored) {}
        return map;
    }

    private static JSONArray installedApps(Context context) {
        JSONArray out = new JSONArray();
        PackageManager pm = context.getPackageManager();
        List<ApplicationInfo> infos;
        try { infos = pm.getInstalledApplications(PackageManager.ApplicationInfoFlags.of(0)); }
        catch (Throwable ignored) { infos = pm.getInstalledApplications(0); }

        Map<String, Long> lastUsed = lastUsedMap(context);
        List<ApplicationInfo> ordered = new ArrayList<>(infos);
        Collections.sort(ordered, Comparator.comparing(a -> String.valueOf(pm.getApplicationLabel(a)), String.CASE_INSENSITIVE_ORDER));
        for (ApplicationInfo info : ordered) {
            try {
                JSONObject row = new JSONObject();
                String pkg = info.packageName;
                PackageInfo pi;
                try { pi = pm.getPackageInfo(pkg, 0); } catch (Exception ignored) { pi = null; }
                boolean launchable = false;
                try { launchable = pm.getLaunchIntentForPackage(pkg) != null; } catch (Exception ignored) {}

                long apkBytes = fileLength(info.sourceDir);
                if (info.splitSourceDirs != null) for (String split : info.splitSourceDirs) apkBytes += fileLength(split);

                row.put("label", String.valueOf(pm.getApplicationLabel(info)));
                row.put("package", pkg);
                row.put("system", (info.flags & ApplicationInfo.FLAG_SYSTEM) != 0);
                row.put("enabled", info.enabled);
                row.put("launchable", launchable);
                row.put("apk_bytes", apkBytes);
                row.put("first_install_ms", pi == null ? 0L : pi.firstInstallTime);
                row.put("last_update_ms", pi == null ? 0L : pi.lastUpdateTime);
                row.put("last_used_ms", lastUsed.containsKey(pkg) ? lastUsed.get(pkg) : 0L);
                String installer = "";
                try {
                    if (Build.VERSION.SDK_INT >= 30) installer = pm.getInstallSourceInfo(pkg).getInstallingPackageName();
                    else installer = pm.getInstallerPackageName(pkg);
                } catch (Exception ignored) {}
                row.put("installer", installer == null ? "" : installer);
                out.put(row);
            } catch (Exception ignored) {}
            if (out.length() >= 800) break;
        }
        return out;
    }

    private static long fileLength(String path) {
        try { return path == null ? 0L : new File(path).length(); }
        catch (Exception ignored) { return 0L; }
    }

    private static JSONArray ripDownloads(Context context) {
        JSONArray out = new JSONArray();
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            String[] projection = new String[]{
                    MediaStore.Downloads._ID, MediaStore.Downloads.DISPLAY_NAME, MediaStore.Downloads.SIZE,
                    MediaStore.Downloads.DATE_MODIFIED, MediaStore.Downloads.MIME_TYPE, MediaStore.Downloads.RELATIVE_PATH
            };
            String selection = MediaStore.Downloads.RELATIVE_PATH + " LIKE ?";
            String[] args = new String[]{Environment.DIRECTORY_DOWNLOADS + "/RIP%"};
            try (Cursor c = context.getContentResolver().query(
                    MediaStore.Downloads.EXTERNAL_CONTENT_URI, projection, selection, args,
                    MediaStore.Downloads.DATE_MODIFIED + " DESC")) {
                if (c != null) {
                    int idIx = c.getColumnIndexOrThrow(MediaStore.Downloads._ID);
                    int nameIx = c.getColumnIndexOrThrow(MediaStore.Downloads.DISPLAY_NAME);
                    int sizeIx = c.getColumnIndexOrThrow(MediaStore.Downloads.SIZE);
                    int dateIx = c.getColumnIndexOrThrow(MediaStore.Downloads.DATE_MODIFIED);
                    int mimeIx = c.getColumnIndexOrThrow(MediaStore.Downloads.MIME_TYPE);
                    while (c.moveToNext() && out.length() < 200) {
                        long id = c.getLong(idIx);
                        Uri uri = ContentUris.withAppendedId(MediaStore.Downloads.EXTERNAL_CONTENT_URI, id);
                        JSONObject row = new JSONObject();
                        row.put("name", c.getString(nameIx));
                        row.put("size", c.getLong(sizeIx));
                        row.put("modified", c.getLong(dateIx));
                        row.put("mime", c.getString(mimeIx));
                        row.put("uri", uri.toString());
                        out.put(row);
                    }
                }
            } catch (Exception ignored) {}
        } else {
            File dir = new File(context.getExternalFilesDir(Environment.DIRECTORY_DOWNLOADS), "RIP");
            File[] files = dir.listFiles();
            if (files != null) {
                for (File f : files) {
                    if (!f.isFile()) continue;
                    try {
                        JSONObject row = new JSONObject();
                        row.put("name", f.getName());
                        row.put("size", f.length());
                        row.put("modified", f.lastModified() / 1000L);
                        row.put("mime", "application/octet-stream");
                        row.put("uri", Uri.fromFile(f).toString());
                        out.put(row);
                    } catch (Exception ignored) {}
                }
            }
        }
        return out;
    }

    private static String networkLabel(Context context) {
        try {
            android.net.ConnectivityManager cm = (android.net.ConnectivityManager) context.getSystemService(Context.CONNECTIVITY_SERVICE);
            if (cm == null) return "";
            android.net.Network n = cm.getActiveNetwork();
            android.net.NetworkCapabilities caps = cm.getNetworkCapabilities(n);
            if (caps == null) return "";
            if (caps.hasTransport(android.net.NetworkCapabilities.TRANSPORT_WIFI)) return "Wi-Fi";
            if (caps.hasTransport(android.net.NetworkCapabilities.TRANSPORT_VPN)) return "VPN / Tailscale";
            if (caps.hasTransport(android.net.NetworkCapabilities.TRANSPORT_CELLULAR)) return "Mobil veri / Tailscale";
        } catch (Exception ignored) {}
        return "Ağ bağlı";
    }

    private static String batteryHealthLabel(int health) {
        switch (health) {
            case BatteryManager.BATTERY_HEALTH_GOOD: return "good";
            case BatteryManager.BATTERY_HEALTH_OVERHEAT: return "overheat";
            case BatteryManager.BATTERY_HEALTH_DEAD: return "dead";
            case BatteryManager.BATTERY_HEALTH_OVER_VOLTAGE: return "over_voltage";
            case BatteryManager.BATTERY_HEALTH_COLD: return "cold";
            case BatteryManager.BATTERY_HEALTH_UNSPECIFIED_FAILURE: return "failure";
            default: return "unknown";
        }
    }
}
