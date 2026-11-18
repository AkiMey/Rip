package com.ripperinio.mobile;

import android.content.Context;
import android.net.ConnectivityManager;
import android.net.Network;
import android.net.NetworkCapabilities;

import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetAddress;

/** Same-LAN Wake-on-LAN sender used by the single smart power button. */
public final class WakeOnLan {
    private WakeOnLan() {}

    public static void send(Context context, String mac, String broadcast, int port) throws Exception {
        if (!wifiConnected(context)) {
            throw new Exception("PC kapalı. Açmak için telefon aynı Wi-Fi ağına bağlı olmalı.");
        }
        byte[] macBytes = parseMac(mac);
        byte[] magic = new byte[6 + 16 * macBytes.length];
        for (int i = 0; i < 6; i++) magic[i] = (byte) 0xFF;
        for (int i = 6; i < magic.length; i += macBytes.length) {
            System.arraycopy(macBytes, 0, magic, i, macBytes.length);
        }

        int safePort = port > 0 && port <= 65535 ? port : 9;
        String target = broadcast == null || broadcast.trim().isEmpty()
                ? "255.255.255.255" : broadcast.trim();

        try (DatagramSocket socket = new DatagramSocket()) {
            socket.setBroadcast(true);
            InetAddress address = InetAddress.getByName(target);
            // Birkaç kez yollamak WoL'u özellikle telefon Wi-Fi güç tasarrufunda
            // daha güvenilir yapar; paket idempotenttir.
            for (int i = 0; i < 3; i++) {
                socket.send(new DatagramPacket(magic, magic.length, address, safePort));
            }
            if (!"255.255.255.255".equals(target)) {
                InetAddress fallback = InetAddress.getByName("255.255.255.255");
                socket.send(new DatagramPacket(magic, magic.length, fallback, safePort));
            }
        }
    }

    private static boolean wifiConnected(Context context) {
        ConnectivityManager cm = (ConnectivityManager)
                context.getSystemService(Context.CONNECTIVITY_SERVICE);
        if (cm == null) return false;
        Network network = cm.getActiveNetwork();
        if (network == null) return false;
        NetworkCapabilities caps = cm.getNetworkCapabilities(network);
        return caps != null && caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI);
    }

    private static byte[] parseMac(String value) throws Exception {
        String raw = value == null ? "" : value.replaceAll("[^0-9A-Fa-f]", "");
        if (raw.length() != 12) throw new Exception("Kayıtlı PC MAC adresi geçersiz.");
        byte[] out = new byte[6];
        for (int i = 0; i < 6; i++) {
            out[i] = (byte) Integer.parseInt(raw.substring(i * 2, i * 2 + 2), 16);
        }
        return out;
    }
}
