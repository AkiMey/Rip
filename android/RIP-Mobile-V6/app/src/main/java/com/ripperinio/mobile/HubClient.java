package com.ripperinio.mobile;

import android.content.Context;

import java.io.BufferedReader;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;

/** Lightweight client for the always-on RIP HUB Lite running on the laptop. */
public final class HubClient {
    private final BridgeConfig config;

    public HubClient(Context context) {
        this.config = new BridgeConfig(context.getApplicationContext());
    }

    public boolean configured() { return config.hasHub(); }

    public String health() throws Exception {
        ensureConfigured();
        return get(config.hubBase() + "/health", false);
    }

    public String wakePc() throws Exception {
        ensureConfigured();
        return get(config.hubBase() + "/wake", true);
    }

    public String pcStatus() throws Exception {
        ensureConfigured();
        return get(config.hubBase() + "/pc-status", true);
    }

    public boolean pcOnline() throws Exception {
        return pcStatus().toUpperCase(java.util.Locale.ROOT).contains("ONLINE");
    }

    private void ensureConfigured() throws Exception {
        if (!config.hasHub()) throw new Exception("Laptop HUB bağlı değil. Önce HUB bağlantısını kaydet.");
    }

    private String get(String baseUrl, boolean auth) throws Exception {
        String url = baseUrl;
        if (auth) {
            String token = URLEncoder.encode(config.hubToken(), StandardCharsets.UTF_8.name());
            url += (url.contains("?") ? "&" : "?") + "token=" + token;
        }

        HttpURLConnection c = (HttpURLConnection) new URL(url).openConnection();
        c.setRequestMethod("GET");
        c.setConnectTimeout(2600);
        c.setReadTimeout(5000);
        c.setUseCaches(false);
        c.setRequestProperty("Cache-Control", "no-store");
        try {
            int code = c.getResponseCode();
            InputStream in = code >= 200 && code < 300 ? c.getInputStream() : c.getErrorStream();
            String body = readAll(in).trim();
            if (code < 200 || code >= 300) {
                if (code == 401) throw new Exception("HUB tokenı yanlış.");
                throw new Exception("Laptop HUB HTTP " + code + (body.isEmpty() ? "" : " • " + body));
            }
            return body.isEmpty() ? "OK" : body;
        } finally {
            c.disconnect();
        }
    }

    private static String readAll(InputStream in) throws Exception {
        if (in == null) return "";
        StringBuilder out = new StringBuilder();
        try (BufferedReader r = new BufferedReader(new InputStreamReader(in, StandardCharsets.UTF_8))) {
            String line;
            while ((line = r.readLine()) != null) {
                if (out.length() > 0) out.append('\n');
                out.append(line);
            }
        }
        return out.toString();
    }
}
