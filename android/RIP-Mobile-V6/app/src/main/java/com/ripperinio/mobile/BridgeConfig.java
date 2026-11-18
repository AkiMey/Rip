package com.ripperinio.mobile;

import android.content.Context;
import android.content.SharedPreferences;
import android.net.Uri;

import org.json.JSONObject;

import java.net.URI;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Set;

public final class BridgeConfig {
    private static final String PREF = "rip_mobile_v5";
    private static final String K_TOKEN = "token";
    private static final String K_LAN = "lan";
    private static final String K_TS = "tailscale";
    private static final String K_KID = "kid";
    private static final String K_PROTOCOL = "protocol";
    private static final String K_ENABLED = "enabled";
    private static final String K_ALL = "all_notifications";
    private static final String K_PACKAGES = "packages";
    private static final String K_LAST_BASE = "last_base";
    private static final String K_WOL_MAC = "wol_mac";
    private static final String K_WOL_BROADCAST = "wol_broadcast";
    private static final String K_WOL_PORT = "wol_port";
    private static final String K_HUB_BASE = "hub_base";
    private static final String K_HUB_TOKEN = "hub_token";

    public final SharedPreferences prefs;

    public BridgeConfig(Context context) {
        prefs = context.getSharedPreferences(PREF, Context.MODE_PRIVATE);
    }

    public String token() { return prefs.getString(K_TOKEN, ""); }
    public String lan() { return cleanBase(prefs.getString(K_LAN, "")); }
    public String tailscale() { return cleanBase(prefs.getString(K_TS, "")); }
    public String keyId() { return prefs.getString(K_KID, ""); }
    public int protocol() { return prefs.getInt(K_PROTOCOL, 5); }
    public boolean enabled() { return prefs.getBoolean(K_ENABLED, true); }
    public boolean allNotifications() { return prefs.getBoolean(K_ALL, false); }
    public String lastBase() { return cleanBase(prefs.getString(K_LAST_BASE, "")); }
    public String wolMac() { return prefs.getString(K_WOL_MAC, ""); }
    public String wolBroadcast() { return prefs.getString(K_WOL_BROADCAST, ""); }
    public int wolPort() { return prefs.getInt(K_WOL_PORT, 9); }
    public String hubBase() { return cleanBase(prefs.getString(K_HUB_BASE, "http://100.76.127.47:8787")); }
    public String hubToken() { return prefs.getString(K_HUB_TOKEN, ""); }
    public boolean hasHub() { return !hubBase().isEmpty() && hubToken().length() >= 24; }
    public boolean hasWakeInfo() {
        return !wolMac().isEmpty() && !wolBroadcast().isEmpty();
    }

    public void setEnabled(boolean value) { prefs.edit().putBoolean(K_ENABLED, value).apply(); }
    public void setAllNotifications(boolean value) { prefs.edit().putBoolean(K_ALL, value).apply(); }
    public void setLastBase(String value) { prefs.edit().putString(K_LAST_BASE, cleanBase(value)).apply(); }

    public synchronized void setHubFromInput(String raw) throws Exception {
        String input = safe(raw);
        if (input.isEmpty()) throw new IllegalArgumentException("Laptop HUB adresini veya tokenını gir.");

        String base = hubBase();
        String token = "";
        if (input.startsWith("http://") || input.startsWith("https://")) {
            Uri uri = Uri.parse(input);
            token = safe(uri.getQueryParameter("token"));
            String parsedBase = origin(input);
            if (!parsedBase.isEmpty()) base = parsedBase;
        } else {
            token = input;
        }

        if (base.isEmpty()) throw new IllegalArgumentException("Laptop HUB adresi bulunamadı.");
        if (token.length() < 24) throw new IllegalArgumentException("HUB tokenı eksik veya geçersiz.");
        prefs.edit().putString(K_HUB_BASE, cleanBase(base)).putString(K_HUB_TOKEN, token).apply();
    }

    public String hubSummary() {
        if (!hasHub()) return "Laptop HUB henüz bağlanmadı";
        try {
            URI u = new URI(hubBase());
            String host = u.getHost() == null ? hubBase() : u.getHost();
            int port = u.getPort();
            return "HUB hazır • " + host + (port > 0 ? ":" + port : "");
        } catch (Exception ignored) {
            return "HUB hazır";
        }
    }

    public void updatePowerInfo(JSONObject power) {
        if (power == null) return;
        String mac = safe(power.optString("mac", ""));
        String broadcast = safe(power.optString("broadcast", ""));
        int port = power.optInt("wol_port", 9);
        SharedPreferences.Editor edit = prefs.edit();
        if (!mac.isEmpty()) edit.putString(K_WOL_MAC, mac);
        if (!broadcast.isEmpty()) edit.putString(K_WOL_BROADCAST, broadcast);
        edit.putInt(K_WOL_PORT, port > 0 && port <= 65535 ? port : 9);
        edit.apply();
    }

    public Set<String> packages() {
        Set<String> defaults = new LinkedHashSet<>();
        defaults.add("com.whatsapp");
        defaults.add("com.instagram.android");
        Set<String> stored = prefs.getStringSet(K_PACKAGES, null);
        return stored == null ? defaults : new LinkedHashSet<>(stored);
    }

    public void setPackageEnabled(String packageName, boolean enabled) {
        Set<String> rows = packages();
        if (enabled) rows.add(packageName); else rows.remove(packageName);
        prefs.edit().putStringSet(K_PACKAGES, rows).apply();
    }

    public boolean shouldForward(String packageName) {
        return allNotifications() || packages().contains(packageName);
    }

    public synchronized PairResult pair(String raw) throws Exception {
        String input = raw == null ? "" : raw.trim();
        if (input.isEmpty()) throw new IllegalArgumentException("PC'deki eşleştirme adresini yapıştır.");
        Uri uri = Uri.parse(input);
        String token = safe(uri.getQueryParameter("t"));
        String lan = cleanBase(safe(uri.getQueryParameter("lan")));
        String ts = cleanBase(safe(uri.getQueryParameter("ts")));
        String kid = safe(uri.getQueryParameter("kid"));
        int protocol = 5;
        try { protocol = Integer.parseInt(safe(uri.getQueryParameter("v"))); } catch (Exception ignored) {}

        if (token.length() < 24) throw new IllegalArgumentException("Eşleştirme token'ı eksik veya geçersiz.");
        if (protocol != 5) throw new IllegalArgumentException("Bu RIP Mobile yalnızca protokol V5 ile çalışır.");

        String primary = origin(input);
        if (ts.isEmpty() && primary.startsWith("http://100.")) ts = primary;
        if (lan.isEmpty() && !primary.isEmpty() && !primary.startsWith("http://100.")) lan = primary;
        if (lan.isEmpty() && ts.isEmpty()) throw new IllegalArgumentException("LAN/Tailscale adresi bulunamadı.");

        String computedKid = CryptoBox.keyId(token);
        if (!kid.isEmpty() && !kid.equals(computedKid)) {
            throw new IllegalArgumentException("Pair ID uyuşmuyor; PC'den yeni adres kopyala.");
        }

        prefs.edit()
                .putString(K_TOKEN, token)
                .putString(K_LAN, lan)
                .putString(K_TS, ts)
                .putString(K_KID, computedKid)
                .putInt(K_PROTOCOL, protocol)
                .putBoolean(K_ENABLED, true)
                .remove(K_LAST_BASE)
                .apply();
        return new PairResult(lan, ts, computedKid, protocol);
    }

    public List<Candidate> candidates() {
        LinkedHashSet<String> ordered = new LinkedHashSet<>();
        String last = lastBase();
        if (!last.isEmpty()) ordered.add(last);
        if (!lan().isEmpty()) ordered.add(lan());
        if (!tailscale().isEmpty()) ordered.add(tailscale());
        List<Candidate> out = new ArrayList<>();
        for (String base : ordered) {
            String route = base.equals(tailscale()) ? "TAILSCALE" : "LAN";
            out.add(new Candidate(route, base));
        }
        return out;
    }

    public String pairSummary() {
        if (token().isEmpty()) return "Henüz eşleştirilmedi";
        StringBuilder sb = new StringBuilder("PAIR ").append(keyId().isEmpty() ? "—" : keyId());
        if (!lan().isEmpty()) sb.append("  •  LAN hazır");
        if (!tailscale().isEmpty()) sb.append("  •  Tailscale hazır");
        return sb.toString();
    }

    private static String safe(String value) { return value == null ? "" : value.trim(); }

    static String cleanBase(String value) {
        String s = safe(value);
        while (s.endsWith("/")) s = s.substring(0, s.length() - 1);
        return s;
    }

    private static String origin(String url) {
        try {
            URI u = new URI(url);
            if (u.getScheme() == null || u.getHost() == null) return "";
            int port = u.getPort();
            return u.getScheme() + "://" + u.getHost() + (port > 0 ? ":" + port : "");
        } catch (Exception ignored) {
            return "";
        }
    }

    public static final class Candidate {
        public final String route;
        public final String base;
        Candidate(String route, String base) { this.route = route; this.base = base; }
    }

    public static final class PairResult {
        public final String lan;
        public final String tailscale;
        public final String keyId;
        public final int protocol;
        PairResult(String lan, String tailscale, String keyId, int protocol) {
            this.lan = lan; this.tailscale = tailscale; this.keyId = keyId; this.protocol = protocol;
        }
    }
}
