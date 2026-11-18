package com.ripperinio.mobile;

import android.content.Context;
import android.net.Uri;

import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;

public final class BridgeClient {
    private final Context context;
    private final BridgeConfig config;

    public BridgeClient(Context context) {
        this.context = context.getApplicationContext();
        this.config = new BridgeConfig(this.context);
    }

    public synchronized boolean probe() {
        if (config.token().isEmpty()) {
            BridgeState.failure("Telefon henüz RIP ile eşleştirilmedi.");
            return false;
        }
        List<String> failures = new ArrayList<>();
        for (BridgeConfig.Candidate c : config.candidates()) {
            long started = System.currentTimeMillis();
            try {
                JSONObject health = getJson(c.base + "/health", false);
                if (!health.optBoolean("ok", false)) throw new Exception("health=false");
                int protocol = health.optInt("protocol_version", 0);
                if (protocol != 5) throw new Exception("protokol " + protocol + " uyumsuz");
                String serverKid = health.optString("key_id", "");
                if (!serverKid.isEmpty() && !serverKid.equals(config.keyId())) {
                    throw new Exception("Pair ID değişmiş; yeniden eşleştir");
                }
                long latency = Math.max(0, System.currentTimeMillis() - started);
                config.setLastBase(c.base);
                // PC açıkken WoL bilgisini telefonda sakla. PC kapalıyken bu
                // endpoint doğal olarak erişilemez; son başarılı değer kullanılır.
                try {
                    JSONObject powerEnvelope = getJson(c.base + "/api/power-info", true);
                    config.updatePowerInfo(powerEnvelope.optJSONObject("power"));
                } catch (Exception ignored) {}
                if ("LAN".equals(c.route)) BridgeState.lanDiagnostic = "OK • " + latency + " ms";
                else BridgeState.tailscaleDiagnostic = "OK • " + latency + " ms";
                BridgeState.longPollSupported = health.optBoolean("long_poll", false);
                BridgeState.success(
                        c.route,
                        c.base,
                        latency,
                        health.optString("computer", "PC"),
                        protocol);
                notifyUi();
                return true;
            } catch (Exception ex) {
                String shortError = shortError(ex);
                failures.add(c.route + ": " + shortError);
                if ("LAN".equals(c.route)) BridgeState.lanDiagnostic = shortError;
                else BridgeState.tailscaleDiagnostic = shortError;
            }
        }
        BridgeState.failure(failures.isEmpty() ? "LAN/Tailscale adresi yok." : join(failures));
        notifyUi();
        return false;
    }

    public JSONObject sendEncryptedResult(JSONObject payload) throws Exception {
        try {
            if (!BridgeState.connected && !probe()) throw new Exception(BridgeState.lastError);
            JSONObject envelope = CryptoBox.encrypt(config.token(), payload);
            JSONObject result = postJson(BridgeState.baseUrl + "/api/phone-notification", envelope, false);
            BridgeState.lastSyncMs = System.currentTimeMillis();
            BridgeState.lastError = "";
            notifyUi();
            return result;
        } catch (Exception first) {
            BridgeState.connected = false;
            if (probe()) {
                try {
                    JSONObject envelope = CryptoBox.encrypt(config.token(), payload);
                    JSONObject result = postJson(BridgeState.baseUrl + "/api/phone-notification", envelope, false);
                    BridgeState.lastSyncMs = System.currentTimeMillis();
                    BridgeState.lastError = "";
                    notifyUi();
                    return result;
                } catch (Exception retry) {
                    BridgeState.failure(shortError(retry));
                    notifyUi();
                    throw retry;
                }
            }
            throw first;
        }
    }

    public boolean sendEncrypted(JSONObject payload) {
        try {
            sendEncryptedResult(payload);
            return true;
        } catch (Exception ex) {
            if (BridgeState.lastError == null || BridgeState.lastError.isEmpty()) {
                BridgeState.failure(shortError(ex));
                notifyUi();
            }
            return false;
        }
    }

    public JSONObject pollCommand() throws Exception {
        if (!BridgeState.connected && !probe()) return null;
        if (BridgeState.longPollSupported) {
            // Keep one authenticated request open. The PC answers immediately as soon
            // as a command is queued, so button presses no longer wait for a 4 s poll.
            JSONObject row = getJson(
                    BridgeState.baseUrl + "/api/phone-command?wait_ms=12000",
                    true,
                    15000);
            return row.optJSONObject("command");
        }
        // Compatibility with an older PC bridge: do not hammer its one-shot endpoint
        // every 35 ms. V5.4.1 PC advertises long_poll=true in /health.
        JSONObject row = getJson(BridgeState.baseUrl + "/api/phone-command", true);
        JSONObject command = row.optJSONObject("command");
        if (command == null) {
            try { Thread.sleep(900L); } catch (InterruptedException ignored) {
                Thread.currentThread().interrupt();
            }
        }
        return command;
    }

    public void ackCommand(String id, String status, String detail) throws Exception {
        JSONObject body = new JSONObject();
        body.put("id", id);
        body.put("status", status);
        body.put("detail", detail == null ? "" : detail);
        postJson(BridgeState.baseUrl + "/api/phone-command-ack", body, true);
    }

    public JSONObject powerAction(String action) throws Exception {
        if (!BridgeState.connected && !probe()) throw new Exception(BridgeState.lastError);
        JSONObject body = new JSONObject();
        body.put("action", action);
        return postJson(BridgeState.baseUrl + "/api/power-action", body, true);
    }

    public String smartPowerToggle() throws Exception {
        // Her dokunuşta gerçek durumu yeniden kontrol et. Eski connected değeri
        // kullanılmaz; PC kapanmış olabilir.
        boolean online = probe();
        if (online) {
            powerAction("shutdown");
            return "PC açık • kapanma komutu gönderildi.";
        }

        // PC bridge erişilemiyorsa önce sürekli açık laptop HUB'a sor. Böylece telefon
        // ev Wi-Fi'sında olmasa bile Tailscale üzerinden laptop -> LAN -> PC WoL çalışır.
        if (config.hasHub()) {
            HubClient hub = new HubClient(context);
            boolean hubOnline;
            try {
                hubOnline = hub.pcOnline();
            } catch (Exception hubError) {
                // Aynı Wi-Fi'daysak eski doğrudan WoL yolu hâlâ yedek olarak kullanılabilir.
                if (!config.hasWakeInfo()) throw hubError;
                try {
                    WakeOnLan.send(context, config.wolMac(), config.wolBroadcast(), config.wolPort());
                    BridgeState.lastError = "";
                    notifyUi();
                    return "Laptop HUB erişilemedi • aynı Wi-Fi üzerinden WoL gönderildi.";
                } catch (Exception localError) {
                    throw new Exception("Laptop HUB: " + hubError.getMessage() + " • Yerel WoL: " + localError.getMessage());
                }
            }

            if (hubOnline) {
                throw new Exception("Laptop HUB PC'yi açık görüyor ama RIP bridge erişilemiyor. Kapatma güvenlik için gönderilmedi.");
            }

            hub.wakePc();
            BridgeState.lastError = "";
            notifyUi();
            return "PC kapalı • Laptop HUB üzerinden açma komutu gönderildi.";
        }

        if (!config.hasWakeInfo()) {
            throw new Exception("WoL bilgisi yok. PC açıkken bir kez bağlantı testi yapıp tekrar dene.");
        }
        WakeOnLan.send(context, config.wolMac(), config.wolBroadcast(), config.wolPort());
        BridgeState.lastError = "";
        notifyUi();
        return "PC kapalı görünüyor • aynı Wi-Fi üzerinden Wake-on-LAN gönderildi.";
    }


    public JSONObject uploadToPc(Uri uri, String filename) throws Exception {
        if (uri == null) throw new Exception("Dosya URI yok");
        if (!BridgeState.connected && !probe()) throw new Exception(BridgeState.lastError);
        String safeName = filename == null || filename.trim().isEmpty() ? "phone-file" : filename.trim();
        String url = BridgeState.baseUrl + "/api/phone-upload?filename=" +
                URLEncoder.encode(safeName, StandardCharsets.UTF_8.name());
        HttpURLConnection c = open(url, "POST", true);
        c.setDoOutput(true);
        c.setChunkedStreamingMode(128 * 1024);
        c.setRequestProperty("Content-Type", "application/octet-stream");
        try (InputStream in = context.getContentResolver().openInputStream(uri);
             OutputStream out = c.getOutputStream()) {
            if (in == null) throw new Exception("Telefon dosyası açılamadı");
            byte[] buffer = new byte[128 * 1024];
            int n;
            while ((n = in.read(buffer)) != -1) out.write(buffer, 0, n);
            out.flush();
        }
        return readResponse(c);
    }

    public String authenticatedFileUrl(String transferId) {
        return BridgeState.baseUrl + "/api/phone-file/" + transferId + "?t=" + config.token();
    }

    private JSONObject getJson(String url, boolean auth) throws Exception {
        HttpURLConnection c = open(url, "GET", auth);
        return readResponse(c);
    }

    private JSONObject getJson(String url, boolean auth, int readTimeoutMs) throws Exception {
        HttpURLConnection c = open(url, "GET", auth);
        c.setReadTimeout(Math.max(1000, readTimeoutMs));
        return readResponse(c);
    }

    private JSONObject postJson(String url, JSONObject body, boolean auth) throws Exception {
        HttpURLConnection c = open(url, "POST", auth);
        c.setDoOutput(true);
        c.setRequestProperty("Content-Type", "application/json; charset=utf-8");
        byte[] data = body.toString().getBytes(StandardCharsets.UTF_8);
        c.setFixedLengthStreamingMode(data.length);
        try (OutputStream out = c.getOutputStream()) { out.write(data); }
        return readResponse(c);
    }

    private HttpURLConnection open(String url, String method, boolean auth) throws Exception {
        HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
        c.setRequestMethod(method);
        c.setConnectTimeout(2600);
        c.setReadTimeout(3200);
        c.setUseCaches(false);
        c.setRequestProperty("Accept", "application/json");
        if (auth) c.setRequestProperty("X-RIP-Token", config.token());
        return c;
    }

    private JSONObject readResponse(HttpURLConnection c) throws Exception {
        int code = c.getResponseCode();
        InputStream stream = code >= 200 && code < 300 ? c.getInputStream() : c.getErrorStream();
        String text = readAll(stream);
        if (code < 200 || code >= 300) {
            String detail = text;
            try { detail = new JSONObject(text).optString("detail", text); } catch (Exception ignored) {}
            throw new Exception("HTTP " + code + (detail.isEmpty() ? "" : " • " + detail));
        }
        return text.isEmpty() ? new JSONObject() : new JSONObject(text);
    }

    private static String readAll(InputStream in) throws Exception {
        if (in == null) return "";
        StringBuilder sb = new StringBuilder();
        try (BufferedReader br = new BufferedReader(new InputStreamReader(in, StandardCharsets.UTF_8))) {
            String line;
            while ((line = br.readLine()) != null) sb.append(line);
        }
        return sb.toString();
    }

    private static String shortError(Exception ex) {
        String s = ex.getMessage();
        if (s == null || s.trim().isEmpty()) s = ex.getClass().getSimpleName();
        s = s.replace('\n', ' ').replace('\r', ' ').trim();
        return s.length() > 110 ? s.substring(0, 110) + "…" : s;
    }

    private static String join(List<String> rows) {
        StringBuilder sb = new StringBuilder();
        for (String row : rows) {
            if (sb.length() > 0) sb.append("  •  ");
            sb.append(row);
        }
        return sb.toString();
    }

    private void notifyUi() {
        android.content.Intent intent = new android.content.Intent(MainActivity.ACTION_STATE);
        intent.setPackage(context.getPackageName());
        context.sendBroadcast(intent);
    }
}
