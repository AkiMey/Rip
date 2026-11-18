package com.ripperinio.mobile;

import android.util.Base64;

import org.json.JSONObject;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.SecureRandom;

import javax.crypto.Cipher;
import javax.crypto.spec.GCMParameterSpec;
import javax.crypto.spec.SecretKeySpec;

public final class CryptoBox {
    private static final byte[] AAD = "ripperinio-phone-notify-v1".getBytes(StandardCharsets.UTF_8);
    private static final byte[] KEY_CONTEXT = "|ripperinio-phone-notify-v1-key".getBytes(StandardCharsets.UTF_8);
    private static final byte[] PAIR_CONTEXT = "|ripperinio-pairing-id-v5".getBytes(StandardCharsets.UTF_8);
    private static final SecureRandom RANDOM = new SecureRandom();

    private CryptoBox() {}

    public static JSONObject encrypt(String token, JSONObject payload) throws Exception {
        byte[] nonce = new byte[12];
        RANDOM.nextBytes(nonce);
        byte[] key = digest(token, KEY_CONTEXT);
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.ENCRYPT_MODE, new SecretKeySpec(key, "AES"), new GCMParameterSpec(128, nonce));
        cipher.updateAAD(AAD);
        byte[] encrypted = cipher.doFinal(payload.toString().getBytes(StandardCharsets.UTF_8));
        JSONObject envelope = new JSONObject();
        envelope.put("key_id", keyId(token));
        envelope.put("nonce", Base64.encodeToString(nonce, Base64.NO_WRAP));
        envelope.put("ciphertext", Base64.encodeToString(encrypted, Base64.NO_WRAP));
        envelope.put("v", 5);
        return envelope;
    }

    public static String keyId(String token) throws Exception {
        byte[] digest = digest(token, PAIR_CONTEXT);
        StringBuilder hex = new StringBuilder();
        for (byte b : digest) hex.append(String.format("%02x", b & 0xff));
        return hex.substring(0, 12);
    }

    private static byte[] digest(String token, byte[] context) throws Exception {
        MessageDigest md = MessageDigest.getInstance("SHA-256");
        md.update((token == null ? "" : token).getBytes(StandardCharsets.UTF_8));
        md.update(context);
        return md.digest();
    }
}
