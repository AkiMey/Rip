package com.ripperinio.mobile;

public final class BridgeState {
    private BridgeState() {}

    public static volatile boolean connected = false;
    public static volatile String route = "OFFLINE";
    public static volatile String baseUrl = "";
    public static volatile long latencyMs = -1;
    public static volatile long lastSyncMs = 0;
    public static volatile String lastError = "";
    public static volatile String lanDiagnostic = "Henüz denenmedi";
    public static volatile String tailscaleDiagnostic = "Henüz denenmedi";
    public static volatile String computer = "PC";
    public static volatile int protocolVersion = 5;
    public static volatile boolean longPollSupported = false;

    public static synchronized void success(
            String routeName,
            String base,
            long latency,
            String computerName,
            int protocol) {
        connected = true;
        route = routeName;
        baseUrl = base;
        latencyMs = latency;
        lastSyncMs = System.currentTimeMillis();
        computer = computerName == null || computerName.isEmpty() ? "PC" : computerName;
        protocolVersion = protocol;
        // V5.2.3 fix: a successful route always clears the user-visible error.
        // LAN can fail while Tailscale succeeds; that is diagnostic information,
        // not a global connection error.
        lastError = "";
    }

    public static synchronized void failure(String message) {
        connected = false;
        route = "OFFLINE";
        baseUrl = "";
        latencyMs = -1;
        lastError = message == null ? "Bağlantı kurulamadı" : message;
    }
}
