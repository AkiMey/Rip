package com.ripperinio.mobile;

import android.Manifest;
import android.app.Notification;
import android.app.PendingIntent;
import android.app.RemoteInput;
import android.content.Intent;
import android.content.Context;
import android.content.pm.PackageManager;
import android.os.Build;
import android.os.Bundle;
import android.service.notification.NotificationListenerService;
import android.service.notification.StatusBarNotification;
import android.telecom.TelecomManager;

import org.json.JSONObject;

import java.util.Locale;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

/**
 * Notification bridge + call action bridge.
 *
 * V6 call strategy:
 *  1) Prefer the real PendingIntent exported by the call notification. This is the
 *     most reliable path for WhatsApp/Telegram/Messenger style VoIP calls because
 *     the originating app grants permission to execute that exact action.
 *  2) For normal cellular calls, fall back to TelecomManager when the user has
 *     explicitly granted ANSWER_PHONE_CALLS.
 */
public class RipNotificationListener extends NotificationListenerService {
    private static volatile RipNotificationListener instance;
    private final ExecutorService executor = Executors.newSingleThreadExecutor();
    private final ConcurrentHashMap<String, CallHandle> activeCalls = new ConcurrentHashMap<>();
    private final ConcurrentHashMap<String, ReplyHandle> activeReplies = new ConcurrentHashMap<>();

    private static final class ReplyHandle {
        final Notification.Action action;
        final RemoteInput[] inputs;
        ReplyHandle(Notification.Action action, RemoteInput[] inputs) { this.action = action; this.inputs = inputs; }
    }

    private static final class CallHandle {
        final String app;
        final String title;
        final String body;
        final String pkg;
        final String notificationKey;
        final PendingIntent acceptIntent;
        final PendingIntent declineIntent;

        CallHandle(String app, String title, String body, String pkg, String notificationKey,
                   PendingIntent acceptIntent, PendingIntent declineIntent) {
            this.app = app;
            this.title = title;
            this.body = body;
            this.pkg = pkg;
            this.notificationKey = notificationKey;
            this.acceptIntent = acceptIntent;
            this.declineIntent = declineIntent;
        }
    }

    @Override public void onListenerConnected() {
        super.onListenerConnected();
        instance = this;
    }

    @Override public void onListenerDisconnected() {
        if (instance == this) instance = null;
        super.onListenerDisconnected();
    }

    public static boolean clearAllNow() {
        RipNotificationListener listener = instance;
        if (listener == null) return false;
        try { listener.cancelAllNotifications(); return true; }
        catch (Exception ignored) { return false; }
    }

    public static boolean dismissNow(String key) {
        RipNotificationListener listener = instance;
        if (listener == null || key == null || key.isEmpty()) return false;
        try {
            for (StatusBarNotification sbn : listener.getActiveNotifications()) {
                if (sbn != null && key.equals(sbn.getKey())) {
                    listener.cancelNotification(key);
                    return true;
                }
            }
        } catch (Exception ignored) {}
        return false;
    }

    public static boolean openNow(String key) {
        RipNotificationListener listener = instance;
        if (listener == null || key == null || key.isEmpty()) return false;
        try {
            for (StatusBarNotification sbn : listener.getActiveNotifications()) {
                if (sbn == null || !key.equals(sbn.getKey())) continue;
                Notification n = sbn.getNotification();
                if (n == null || n.contentIntent == null) return false;
                n.contentIntent.send();
                return true;
            }
        } catch (Exception ignored) {}
        return false;
    }

    public static String performCallAction(Context context, String callId, String action) throws Exception {
        String wanted = action == null ? "" : action.trim().toLowerCase(Locale.ROOT);
        if (!wanted.equals("accept") && !wanted.equals("decline")) {
            throw new Exception("Arama işlemi accept veya decline olmalı");
        }

        RipNotificationListener listener = instance;
        CallHandle handle = listener == null ? null : listener.activeCalls.get(callId == null ? "" : callId);
        PendingIntent pending = null;
        if (handle != null) pending = wanted.equals("accept") ? handle.acceptIntent : handle.declineIntent;
        if (pending != null) {
            try {
                pending.send();
                return wanted.equals("accept")
                        ? "Arama bildiriminin CEVAPLA eylemi çalıştırıldı"
                        : "Arama bildiriminin REDDET eylemi çalıştırıldı";
            } catch (PendingIntent.CanceledException ex) {
                if (listener != null) listener.activeCalls.remove(callId);
                // Continue to cellular fallback below.
            }
        }

        // Cellular-call fallback. This does not grant any privilege silently: the
        // user must have enabled ANSWER_PHONE_CALLS from RIP Mobile's permission center.
        if (Build.VERSION.SDK_INT >= 26
                && context.checkSelfPermission(Manifest.permission.ANSWER_PHONE_CALLS) == PackageManager.PERMISSION_GRANTED) {
            TelecomManager telecom = (TelecomManager) context.getSystemService(Context.TELECOM_SERVICE);
            if (telecom != null) {
                try {
                    if (wanted.equals("accept")) {
                        telecom.acceptRingingCall();
                        return "Android telefon araması kabul edildi";
                    }
                    if (Build.VERSION.SDK_INT >= 28 && telecom.endCall()) {
                        return "Android telefon araması reddedildi";
                    }
                } catch (SecurityException ex) {
                    throw new Exception("Android arama izni reddedildi: " + ex.getMessage());
                }
            }
        }

        String reason = handle == null
                ? "Aktif arama bildirimi bulunamadı"
                : "Bu arama uygulaması cevapla/reddet PendingIntent'i yayınlamıyor";
        if (Build.VERSION.SDK_INT >= 26
                && context.checkSelfPermission(Manifest.permission.ANSWER_PHONE_CALLS) != PackageManager.PERMISSION_GRANTED) {
            reason += ". Normal telefon aramaları için RIP Mobile > Yetki Merkezi > ARAMA CEVAPLA / REDDET İZNİ'ni aç";
        }
        throw new Exception(reason);
    }

    public static String replyNow(Context context, String notificationKey, String text) throws Exception {
        RipNotificationListener listener = instance;
        if (listener == null) throw new Exception("Bildirim erişimi servisi bağlı değil");
        ReplyHandle handle = listener.activeReplies.get(notificationKey == null ? "" : notificationKey);
        if (handle == null || handle.action == null || handle.action.actionIntent == null || handle.inputs == null || handle.inputs.length == 0)
            throw new Exception("Bu bildirim hızlı yanıt desteklemiyor veya artık aktif değil");
        String msg = text == null ? "" : text.trim();
        if (msg.isEmpty()) throw new Exception("Yanıt metni boş");
        Bundle results = new Bundle();
        for (RemoteInput input : handle.inputs) results.putCharSequence(input.getResultKey(), msg);
        Intent fillIn = new Intent();
        RemoteInput.addResultsToIntent(handle.inputs, fillIn, results);
        handle.action.actionIntent.send(context, 0, fillIn);
        return "Bildirim yanıtı gönderildi";
    }

    @Override public void onNotificationPosted(StatusBarNotification sbn) {
        if (sbn == null || getPackageName().equals(sbn.getPackageName())) return;
        BridgeConfig cfg = new BridgeConfig(this);
        if (!cfg.enabled()) return;
        Notification n = sbn.getNotification();
        if (n == null) return;
        Bundle e = n.extras;
        String title = text(e, Notification.EXTRA_TITLE);
        String body = text(e, Notification.EXTRA_TEXT);
        String sub = text(e, Notification.EXTRA_SUB_TEXT);
        boolean detectedCall = isCallNotification(n, title, body);
        // Aramalar uygulama filtresinden bağımsız geçer. Aksi halde normal Telefon
        // uygulaması seçili değilken ACCEPT/DECLINE kartı hiç PC'ye ulaşmıyordu.
        if (!detectedCall && !cfg.shouldForward(sbn.getPackageName())) return;
        if (title.isEmpty() && body.isEmpty() && !detectedCall) return;

        executor.execute(() -> {
            try {
                JSONObject p = new JSONObject();
                boolean call = detectedCall;
                String stableId = sbn.getPackageName() + ":" + sbn.getId();
                p.put("kind", call ? "call" : "notification");
                p.put("id", call ? stableId : stableId + ":" + sbn.getPostTime());
                p.put("notification_key", sbn.getKey());
                p.put("posted_at", sbn.getPostTime());
                p.put("package", sbn.getPackageName());
                p.put("app", appLabel(sbn.getPackageName()));
                p.put("title", title);
                p.put("text", body);
                p.put("subtext", sub);
                p.put("category", n.category == null ? "" : n.category);
                p.put("clickable", n.contentIntent != null);
                ReplyHandle reply = findReplyHandle(n);
                p.put("replyable", reply != null);
                if (reply != null) activeReplies.put(sbn.getKey(), reply);
                else activeReplies.remove(sbn.getKey());
                if (call) {
                    PendingIntent[] pair = findCallActionIntents(n);
                    p.put("call_state", "incoming");
                    p.put("can_accept", pair[0] != null || DeviceSnapshot.answerCallsAccess(this));
                    p.put("can_decline", pair[1] != null || DeviceSnapshot.answerCallsAccess(this));
                    p.put("call_action_source", pair[0] != null || pair[1] != null ? "notification" : "telecom");
                    activeCalls.put(stableId, new CallHandle(
                            appLabel(sbn.getPackageName()), title, body, sbn.getPackageName(), sbn.getKey(), pair[0], pair[1]));
                }
                new BridgeClient(this).sendEncrypted(p);
            } catch (Exception ignored) {}
        });
    }

    @Override public void onNotificationRemoved(StatusBarNotification sbn) {
        if (sbn == null) return;
        String stableId = sbn.getPackageName() + ":" + sbn.getId();
        CallHandle call = activeCalls.remove(stableId);
        activeReplies.remove(sbn.getKey());
        executor.execute(() -> {
            try {
                JSONObject p = new JSONObject();
                if (call != null) {
                    p.put("kind", "call");
                    p.put("id", stableId);
                    p.put("notification_key", sbn.getKey());
                    p.put("posted_at", System.currentTimeMillis());
                    p.put("package", call.pkg);
                    p.put("app", call.app);
                    p.put("title", call.title);
                    p.put("text", call.body);
                    p.put("call_state", "ended");
                } else {
                    p.put("kind", "notification_removed");
                    p.put("id", stableId);
                    p.put("notification_key", sbn.getKey());
                    p.put("package", sbn.getPackageName());
                    p.put("posted_at", System.currentTimeMillis());
                }
                new BridgeClient(this).sendEncrypted(p);
            } catch (Exception ignored) {}
        });
    }

    private static ReplyHandle findReplyHandle(Notification n) {
        if (n == null || n.actions == null) return null;
        for (Notification.Action a : n.actions) {
            if (a == null || a.actionIntent == null) continue;
            RemoteInput[] inputs = a.getRemoteInputs();
            if (inputs != null && inputs.length > 0) return new ReplyHandle(a, inputs);
        }
        return null;
    }

    private static PendingIntent[] findCallActionIntents(Notification n) {
        PendingIntent accept = null;
        PendingIntent decline = null;
        Notification.Action[] actions = n.actions;
        if (actions == null) return new PendingIntent[]{null, null};
        for (Notification.Action a : actions) {
            if (a == null || a.actionIntent == null) continue;
            String title = a.title == null ? "" : a.title.toString().trim().toLowerCase(Locale.ROOT);
            if (title.isEmpty()) continue;
            if (accept == null && containsAny(title,
                    "answer", "accept", "yanıtla", "yanitla", "cevapla", "kabul", "aç", "ac")) {
                accept = a.actionIntent;
                continue;
            }
            if (decline == null && containsAny(title,
                    "decline", "reject", "ignore", "hang up", "reddet", "red", "kapat", "sonlandır", "sonlandir")) {
                decline = a.actionIntent;
            }
        }
        return new PendingIntent[]{accept, decline};
    }

    private static boolean containsAny(String value, String... needles) {
        for (String n : needles) if (value.contains(n)) return true;
        return false;
    }

    private static boolean isCallNotification(Notification n, String title, String body) {
        if (Notification.CATEGORY_CALL.equals(n.category)) return true;
        String low = (title + " " + body).toLowerCase(Locale.ROOT);
        return low.contains("incoming call") || low.contains("gelen sesli arama") ||
                low.contains("gelen görüntülü arama") || low.contains("gelen goruntulu arama") ||
                low.contains("aranıyor") || low.contains("araniyor") || low.contains("calling");
    }

    @Override public void onDestroy() {
        if (instance == this) instance = null;
        executor.shutdownNow();
        super.onDestroy();
    }

    private String appLabel(String packageName) {
        try {
            android.content.pm.ApplicationInfo info = getPackageManager().getApplicationInfo(packageName, 0);
            return String.valueOf(getPackageManager().getApplicationLabel(info));
        } catch (Exception ignored) { return packageName; }
    }

    private static String text(Bundle b, String key) {
        if (b == null) return "";
        CharSequence v = b.getCharSequence(key);
        return v == null ? "" : v.toString().trim();
    }
}
