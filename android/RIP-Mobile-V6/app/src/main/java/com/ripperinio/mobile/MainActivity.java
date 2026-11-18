package com.ripperinio.mobile;

import android.Manifest;
import android.app.Activity;
import android.media.projection.MediaProjectionManager;
import android.content.BroadcastReceiver;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.net.Uri;
import android.content.pm.PackageManager;
import android.content.res.ColorStateList;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.Build;
import android.os.Bundle;
import android.provider.Settings;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.HorizontalScrollView;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.Switch;
import android.widget.TextView;
import android.widget.Toast;

import java.util.LinkedHashMap;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public class MainActivity extends Activity {
    public static final String ACTION_STATE = "com.ripperinio.mobile.STATE";
    private static final int REQUEST_SCREEN_CAPTURE = 611;
    private int pendingScreenFps = 12;
    private int pendingScreenWidth = 720;
    private int pendingScreenQuality = 50;

    private static final int BG = Color.rgb(7, 11, 18);
    private static final int CARD = Color.rgb(12, 20, 33);
    private static final int CARD2 = Color.rgb(16, 29, 48);
    private static final int TEXT = Color.rgb(243, 247, 252);
    private static final int DIM = Color.rgb(148, 164, 186);
    private static final int MINT = Color.rgb(110, 242, 208);
    private static final int PURPLE = Color.rgb(153, 126, 255);
    private static final int ORANGE = Color.rgb(255, 177, 102);
    private static final int RED = Color.rgb(255, 107, 132);

    private BridgeConfig config;
    private final ExecutorService io = Executors.newSingleThreadExecutor();
    private TextView statusTitle, statusMeta, pcValue, syncValue, pairSummary, errorText, diagText, notifAccess, permissionSummary, hubSummary;
    private EditText pairInput, hubInput;
    private Switch bridgeSwitch, allSwitch;
    private final Map<String, CheckBox> appChecks = new LinkedHashMap<>();

    private final BroadcastReceiver stateReceiver = new BroadcastReceiver() {
        @Override public void onReceive(Context context, Intent intent) { renderState(); }
    };

    @Override protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        config = new BridgeConfig(this);
        getWindow().setStatusBarColor(BG);
        getWindow().setNavigationBarColor(BG);
        setContentView(buildUi());
        requestNotificationPermission();
        if (config.enabled() && !config.token().isEmpty()) BridgeService.start(this);
        renderState();
        try {
            String requested = getIntent().getStringExtra("request_permission");
            if ("answer_calls".equals(requested)
                    && Build.VERSION.SDK_INT >= 26
                    && checkSelfPermission(Manifest.permission.ANSWER_PHONE_CALLS) != PackageManager.PERMISSION_GRANTED) {
                requestPermissions(new String[]{Manifest.permission.ANSWER_PHONE_CALLS, Manifest.permission.READ_PHONE_STATE}, 600);
            } else if ("location".equals(requested)) {
                requestLocationPermission();
            }
        } catch (Exception ignored) {}
        maybeRequestScreenCapture(getIntent());
    }

    @Override protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        try {
            String requested = intent == null ? "" : intent.getStringExtra("request_permission");
            if ("location".equals(requested)) requestLocationPermission();
            else if ("answer_calls".equals(requested)
                    && Build.VERSION.SDK_INT >= 26
                    && checkSelfPermission(Manifest.permission.ANSWER_PHONE_CALLS) != PackageManager.PERMISSION_GRANTED) {
                requestPermissions(new String[]{Manifest.permission.ANSWER_PHONE_CALLS, Manifest.permission.READ_PHONE_STATE}, 600);
            }
        } catch (Exception ignored) {}
        maybeRequestScreenCapture(intent);
    }

    private void maybeRequestScreenCapture(Intent intent) {
        if (intent == null || !intent.getBooleanExtra("request_screen_capture", false)) return;
        pendingScreenFps = Math.max(4, Math.min(30, intent.getIntExtra("screen_fps", 12)));
        pendingScreenWidth = Math.max(420, Math.min(1080, intent.getIntExtra("screen_width", 720)));
        pendingScreenQuality = Math.max(30, Math.min(75, intent.getIntExtra("screen_quality", 50)));
        try {
            MediaProjectionManager manager = (MediaProjectionManager) getSystemService(Context.MEDIA_PROJECTION_SERVICE);
            if (manager == null) { toast("Ekran paylaşım servisi bulunamadı"); return; }
            startActivityForResult(manager.createScreenCaptureIntent(), REQUEST_SCREEN_CAPTURE);
        } catch (Exception ex) {
            toast("Ekran paylaşım izni açılamadı: " + ex.getMessage());
        }
    }

    @Override protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode != REQUEST_SCREEN_CAPTURE) return;
        if (resultCode != RESULT_OK || data == null) {
            toast("Canlı ekran paylaşımı iptal edildi");
            return;
        }
        try {
            ScreenStreamService.start(this, resultCode, data, pendingScreenFps, pendingScreenWidth, pendingScreenQuality);
            toast("Akıcı ekran yayını başlatıldı • hedef " + pendingScreenFps + " FPS");
        } catch (Exception ex) {
            toast("Canlı ekran başlatılamadı: " + ex.getMessage());
        }
    }

    @Override protected void onResume() {
        super.onResume();
        try { renderState(); } catch (Exception ignored) {}
    }

    @Override protected void onStart() {
        super.onStart();
        IntentFilter f = new IntentFilter(ACTION_STATE);
        if (Build.VERSION.SDK_INT >= 33) registerReceiver(stateReceiver, f, Context.RECEIVER_NOT_EXPORTED);
        else registerReceiver(stateReceiver, f);
        renderState();
    }

    @Override protected void onStop() {
        try { unregisterReceiver(stateReceiver); } catch (Exception ignored) {}
        super.onStop();
    }

    @Override protected void onDestroy() {
        io.shutdownNow();
        super.onDestroy();
    }

    private View buildUi() {
        ScrollView scroll = new ScrollView(this);
        scroll.setFillViewport(true);
        scroll.setBackgroundColor(BG);
        LinearLayout root = column(0);
        root.setPadding(dp(18), dp(20), dp(18), dp(34));
        scroll.addView(root, matchWrap());

        LinearLayout top = row(Gravity.CENTER_VERTICAL);
        TextView logo = text("R", 20, Color.rgb(5, 14, 21), true);
        logo.setGravity(Gravity.CENTER);
        logo.setBackground(round(MINT, 16));
        top.addView(logo, lp(dp(46), dp(46), 0, 0, dp(12), 0));
        LinearLayout names = column(0);
        names.addView(text("RIP MOBILE", 24, TEXT, true));
        names.addView(text("PHONE AGENT • V6.2.1b", 11, MINT, true));
        top.addView(names, lp(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f, 0, 0, 0));
        TextView v = pill("PROTOCOL 5", PURPLE);
        top.addView(v);
        root.addView(top);

        root.addView(space(18));
        LinearLayout connection = cardGradient();
        LinearLayout statusRow = row(Gravity.CENTER_VERTICAL);
        TextView dot = text("●", 20, MINT, true);
        statusRow.addView(dot, lp(dp(24), ViewGroup.LayoutParams.WRAP_CONTENT, 0, 0, dp(8), 0));
        LinearLayout st = column(0);
        statusTitle = text("BAĞLANTI BEKLENİYOR", 18, TEXT, true);
        statusMeta = text("LAN / Tailscale otomatik seçim", 12, DIM, false);
        st.addView(statusTitle);
        st.addView(statusMeta);
        statusRow.addView(st, lp(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f, 0, 0, 0));
        connection.addView(statusRow);
        connection.addView(space(16));
        LinearLayout stats = row(Gravity.CENTER_VERTICAL);
        LinearLayout pcStat = miniStat("PC", "—", true);
        LinearLayout syncStat = miniStat("SON SENKRON", "—", false);
        stats.addView(pcStat, lp(0, dp(58), 1f, 0, dp(8), 0));
        stats.addView(syncStat, lp(0, dp(58), 1f, 0, 0, 0));
        connection.addView(stats);
        errorText = text("", 12, RED, false);
        errorText.setPadding(0, dp(12), 0, 0);
        connection.addView(errorText);
        root.addView(connection);

        root.addView(section("EŞLEŞTİRME", "PC'deki RIPPERINIO TELEFON ADRESİ'nı bir kez yapıştır."));
        LinearLayout pairing = card();
        pairSummary = text(config.pairSummary(), 12, MINT, true);
        pairing.addView(pairSummary);
        pairInput = new EditText(this);
        pairInput.setHint("http://100.x.x.x:8765/?t=…");
        pairInput.setHintTextColor(Color.rgb(93, 109, 132));
        pairInput.setTextColor(TEXT);
        pairInput.setSingleLine(true);
        pairInput.setTextSize(14);
        pairInput.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        pairInput.setBackground(round(Color.rgb(8, 15, 26), 14, Color.rgb(42, 61, 83)));
        pairInput.setPadding(dp(14), 0, dp(14), 0);
        pairing.addView(pairInput, lp(ViewGroup.LayoutParams.MATCH_PARENT, dp(52), 0, dp(12), 0, 0));
        LinearLayout pairButtons = row(Gravity.CENTER_VERTICAL);
        Button pair = button("EŞLEŞTİR", MINT, Color.rgb(6, 20, 20));
        pair.setOnClickListener(vw -> doPair());
        Button test = button("BAĞLANTIYI TEST ET", CARD2, TEXT);
        test.setOnClickListener(vw -> testConnection());
        pairButtons.addView(pair, lp(0, dp(48), 1f, 0, dp(8), 0));
        pairButtons.addView(test, lp(0, dp(48), 1f, 0, 0, 0));
        pairing.addView(pairButtons, lp(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT, 0, dp(10), 0, 0));
        root.addView(pairing);

        root.addView(section("KÖPRÜ", "Arka plan bağlantısı ve bildirim aktarımı."));
        LinearLayout bridgeCard = card();
        bridgeSwitch = toggle("Köprü servisi", "PC bağlantısını arka planda canlı tut", config.enabled());
        bridgeSwitch.setOnCheckedChangeListener((b, checked) -> {
            config.setEnabled(checked);
            if (checked) BridgeService.start(this); else stopService(new Intent(this, BridgeService.class));
        });
        bridgeCard.addView(wrapToggle("Köprü servisi", "PC bağlantısını arka planda canlı tut", bridgeSwitch));
        bridgeCard.addView(divider());
        allSwitch = toggle("Tüm bildirimler", "Seçili uygulama filtresini devre dışı bırak", config.allNotifications());
        allSwitch.setOnCheckedChangeListener((b, checked) -> config.setAllNotifications(checked));
        bridgeCard.addView(wrapToggle("Tüm bildirimleri aktar", "Kapalıysa yalnızca seçtiğin uygulamalar gider", allSwitch));
        bridgeCard.addView(divider());
        notifAccess = text("Bildirim erişimi kontrol ediliyor…", 13, DIM, true);
        bridgeCard.addView(notifAccess);
        Button permission = button("BİLDİRİM ERİŞİMİNİ AÇ", CARD2, MINT);
        permission.setOnClickListener(vw -> startActivity(new Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS)));
        bridgeCard.addView(permission, lp(ViewGroup.LayoutParams.MATCH_PARENT, dp(46), 0, dp(10), 0, 0));
        root.addView(bridgeCard);

        root.addView(section("YETKİ MERKEZİ", "PC Phone Control Center için gerekli Android izinlerini buradan ver."));
        LinearLayout permissionCard = card();
        permissionSummary = text("İzinler kontrol ediliyor…", 12, DIM, true);
        permissionSummary.setLineSpacing(0, 1.25f);
        permissionCard.addView(permissionSummary);
        LinearLayout perm1 = row(Gravity.CENTER_VERTICAL);
        Button accessBtn = button("ACCESSIBILITY", CARD2, MINT);
        accessBtn.setOnClickListener(vw -> startActivity(new Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)));
        Button overlayBtn = button("OVERLAY", CARD2, PURPLE);
        overlayBtn.setOnClickListener(vw -> startActivity(new Intent(Settings.ACTION_MANAGE_OVERLAY_PERMISSION, Uri.parse("package:" + getPackageName()))));
        perm1.addView(accessBtn, lp(0, dp(46), 1f, dp(10), dp(8), 0));
        perm1.addView(overlayBtn, lp(0, dp(46), 1f, dp(10), 0, 0));
        permissionCard.addView(perm1);
        Button batteryBtn = button("PİL OPTİMİZASYONUNDAN ÇIKAR", CARD2, ORANGE);
        batteryBtn.setOnClickListener(vw -> {
            try { startActivity(new Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:" + getPackageName()))); }
            catch (Exception ex) { startActivity(new Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS)); }
        });
        permissionCard.addView(batteryBtn, lp(ViewGroup.LayoutParams.MATCH_PARENT, dp(46), 0, dp(8), 0, 0));

        LinearLayout perm2 = row(Gravity.CENTER_VERTICAL);
        Button usageBtn = button("KULLANIM ERİŞİMİ", CARD2, MINT);
        usageBtn.setOnClickListener(vw -> startActivity(new Intent(Settings.ACTION_USAGE_ACCESS_SETTINGS)));
        Button writeBtn = button("SİSTEM AYARLARI", CARD2, PURPLE);
        writeBtn.setOnClickListener(vw -> startActivity(new Intent(Settings.ACTION_MANAGE_WRITE_SETTINGS, Uri.parse("package:" + getPackageName()))));
        perm2.addView(usageBtn, lp(0, dp(46), 1f, dp(8), dp(8), 0));
        perm2.addView(writeBtn, lp(0, dp(46), 1f, dp(8), 0, 0));
        permissionCard.addView(perm2);

        LinearLayout perm3 = row(Gravity.CENTER_VERTICAL);
        Button dndBtn = button("RAHATSIZ ETMEYİN", CARD2, ORANGE);
        dndBtn.setOnClickListener(vw -> startActivity(new Intent(Settings.ACTION_NOTIFICATION_POLICY_ACCESS_SETTINGS)));
        Button installBtn = button("APK YÜKLEME", CARD2, MINT);
        installBtn.setOnClickListener(vw -> startActivity(new Intent(Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES, Uri.parse("package:" + getPackageName()))));
        perm3.addView(dndBtn, lp(0, dp(46), 1f, dp(8), dp(8), 0));
        perm3.addView(installBtn, lp(0, dp(46), 1f, dp(8), 0, 0));
        permissionCard.addView(perm3);

        Button cameraBtn = button("FENER İÇİN KAMERA İZNİ", CARD2, MINT);
        cameraBtn.setOnClickListener(vw -> {
            if (Build.VERSION.SDK_INT >= 23 && checkSelfPermission(Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED)
                requestPermissions(new String[]{Manifest.permission.CAMERA}, 540);
            else toast("Kamera izni zaten açık.");
        });
        permissionCard.addView(cameraBtn, lp(ViewGroup.LayoutParams.MATCH_PARENT, dp(46), 0, dp(8), 0, 0));

        Button locationBtn = button("KONUM İZNİ", CARD2, MINT);
        locationBtn.setOnClickListener(vw -> requestLocationPermission());
        permissionCard.addView(locationBtn, lp(ViewGroup.LayoutParams.MATCH_PARENT, dp(46), 0, dp(8), 0, 0));

        Button callBtn = button("ARAMA CEVAPLA / REDDET İZNİ", CARD2, PURPLE);
        callBtn.setOnClickListener(vw -> {
            if (Build.VERSION.SDK_INT >= 26 && checkSelfPermission(Manifest.permission.ANSWER_PHONE_CALLS) != PackageManager.PERMISSION_GRANTED) {
                requestPermissions(new String[]{Manifest.permission.ANSWER_PHONE_CALLS, Manifest.permission.READ_PHONE_STATE}, 600);
            } else {
                toast("Arama kontrol izni zaten açık. WhatsApp gibi uygulamalarda bildirim aksiyonları da otomatik kullanılır.");
            }
        });
        permissionCard.addView(callBtn, lp(ViewGroup.LayoutParams.MATCH_PARENT, dp(46), 0, dp(8), 0, 0));
        root.addView(permissionCard);

        root.addView(section("AKTARILACAK UYGULAMALAR", "İstediğin bildirimleri PC HUD'una gönder."));
        LinearLayout apps = card();
        addAppToggle(apps, "WhatsApp", "com.whatsapp");
        addAppToggle(apps, "WhatsApp Business", "com.whatsapp.w4b");
        addAppToggle(apps, "Instagram", "com.instagram.android");
        addAppToggle(apps, "Telegram", "org.telegram.messenger");
        addAppToggle(apps, "Discord", "com.discord");
        addAppToggle(apps, "Samsung Mesajlar", "com.samsung.android.messaging");
        addAppToggle(apps, "Google Mesajlar", "com.google.android.apps.messaging");
        addAppToggle(apps, "Messenger", "com.facebook.orca");
        root.addView(apps);

        root.addView(section("HIZLI AKTARIM", "Telefon panosunu tek dokunuşla Windows panosuna aktar."));
        LinearLayout clipboardCard = card();
        Button clipboardButton = button("PANOMU PC'YE GÖNDER", CARD2, MINT);
        clipboardButton.setOnClickListener(vw -> sendClipboardToPc());
        clipboardCard.addView(clipboardButton, lp(ViewGroup.LayoutParams.MATCH_PARENT, dp(50), 0, 0, 0, 0));
        root.addView(clipboardCard);

        root.addView(section("RIP HOME HUB", "Telefon dışarıdayken laptop üzerinden PC'yi aç. Bir kez HUB adresini/tokenını kaydet."));
        LinearLayout hubCard = card();
        hubSummary = text(config.hubSummary(), 12, config.hasHub() ? MINT : ORANGE, true);
        hubCard.addView(hubSummary);
        hubInput = new EditText(this);
        hubInput.setHint("http://100.76.127.47:8787/wake?token=…  veya sadece token");
        hubInput.setHintTextColor(Color.rgb(93, 109, 132));
        hubInput.setTextColor(TEXT);
        hubInput.setSingleLine(true);
        hubInput.setTextSize(13);
        hubInput.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        hubInput.setBackground(round(Color.rgb(8, 15, 26), 14, Color.rgb(42, 61, 83)));
        hubInput.setPadding(dp(14), 0, dp(14), 0);
        hubCard.addView(hubInput, lp(ViewGroup.LayoutParams.MATCH_PARENT, dp(52), 0, dp(12), 0, 0));
        LinearLayout hubButtons = row(Gravity.CENTER_VERTICAL);
        Button saveHub = button("HUB'I KAYDET", MINT, Color.rgb(6, 20, 20));
        saveHub.setOnClickListener(vw -> saveHub());
        Button testHub = button("HUB TEST", CARD2, TEXT);
        testHub.setOnClickListener(vw -> testHub());
        hubButtons.addView(saveHub, lp(0, dp(48), 1f, 0, dp(8), 0));
        hubButtons.addView(testHub, lp(0, dp(48), 1f, 0, 0, 0));
        hubCard.addView(hubButtons, lp(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT, 0, dp(10), 0, 0));
        root.addView(hubCard);

        root.addView(section("PC KONTROLLERİ", "PC AÇ laptop HUB üzerinden çalışır; diğer güç komutları PC açıksa RIP bridge üzerinden gider."));
        LinearLayout controls = card();

        LinearLayout powerRow = row(Gravity.CENTER_VERTICAL);
        Button wakePc = button("PC AÇ", Color.rgb(11, 20, 33), MINT);
        wakePc.setBackground(round(Color.rgb(11, 20, 33), 14,
                Color.argb(155, Color.red(MINT), Color.green(MINT), Color.blue(MINT))));
        wakePc.setOnClickListener(vw -> hubWakePc());
        powerRow.addView(wakePc, lp(0, dp(52), 1f, 0, dp(8), 0));
        powerRow.addView(controlButton("PC KAPAT", "shutdown", RED), lp(0, dp(52), 1f, 0, 0, 0));
        controls.addView(powerRow);

        LinearLayout r1 = row(Gravity.CENTER_VERTICAL);
        r1.setPadding(0, dp(8), 0, 0);
        r1.addView(controlButton("KİLİTLE", "lock", MINT), lp(0, dp(52), 1f, 0, dp(8), 0));
        r1.addView(controlButton("UYKU", "sleep", PURPLE), lp(0, dp(52), 1f, 0, 0, 0));
        controls.addView(r1);

        LinearLayout r2 = row(Gravity.CENTER_VERTICAL);
        r2.setPadding(0, dp(8), 0, dp(2));
        r2.addView(controlButton("YENİDEN BAŞLAT", "restart", ORANGE), lp(0, dp(52), 1f, 0, dp(8), 0));
        Button pcStatusButton = button("PC DURUMU", Color.rgb(11, 20, 33), MINT);
        pcStatusButton.setBackground(round(Color.rgb(11, 20, 33), 14,
                Color.argb(150, Color.red(MINT), Color.green(MINT), Color.blue(MINT))));
        pcStatusButton.setOnClickListener(vw -> hubPcStatus());
        r2.addView(pcStatusButton, lp(0, dp(52), 1f, 0, 0, 0));
        controls.addView(r2, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, dp(62)));
        root.addView(controls);

        root.addView(section("BAĞLANTI DETAYI", "LAN başarısız olsa bile Tailscale başarılıysa hata sayılmaz."));
        LinearLayout detail = card();
        diagText = text("LAN: —\nTAILSCALE: —", 12, DIM, false);
        diagText.setLineSpacing(0, 1.25f);
        detail.addView(diagText);
        root.addView(detail);

        root.addView(space(18));
        root.addView(text("RIP Mobile V6.2.1b • HOME HUB • HIGH-FPS SCREEN • Phone Control Suite • AES-GCM • WoL", 10, Color.rgb(84, 101, 124), false));
        return scroll;
    }

    private void doPair() {
        try {
            config.pair(pairInput.getText().toString());
            pairInput.setText("");
            pairSummary.setText(config.pairSummary());
            bridgeSwitch.setChecked(true);
            BridgeService.start(this);
            toast("Eşleştirildi. Bağlantı test ediliyor…");
            testConnection();
        } catch (Exception ex) {
            toast(ex.getMessage());
        }
    }

    private void testConnection() {
        statusTitle.setText("BAĞLANTI TEST EDİLİYOR…");
        io.execute(() -> {
            new BridgeClient(this).probe();
            runOnUiThread(this::renderState);
        });
    }

    private void sendClipboardToPc() {
        ClipboardManager cb = (ClipboardManager) getSystemService(Context.CLIPBOARD_SERVICE);
        if (cb == null || !cb.hasPrimaryClip()) { toast("Telefon panosu boş."); return; }
        ClipData clip = cb.getPrimaryClip();
        if (clip == null || clip.getItemCount() == 0) { toast("Telefon panosu boş."); return; }
        CharSequence value = clip.getItemAt(0).coerceToText(this);
        String text = value == null ? "" : value.toString();
        if (text.trim().isEmpty()) { toast("Telefon panosu boş."); return; }
        io.execute(() -> {
            try {
                org.json.JSONObject p = new org.json.JSONObject();
                p.put("kind", "clipboard");
                p.put("text", text);
                p.put("device", android.os.Build.MANUFACTURER + " " + android.os.Build.MODEL);
                org.json.JSONObject result = new BridgeClient(this).sendEncryptedResult(p);
                boolean direct = result.optBoolean("pc_clipboard", false);
                runOnUiThread(() -> toast(
                        direct
                                ? "Telefon panosu PC'ye kopyalandı ✓"
                                : "Telefon panosu PC HUD'una ulaştı ✓"));
            } catch (Exception ex) {
                runOnUiThread(() -> toast("Pano gönderilemedi: " + ex.getMessage()));
            }
        });
    }

    private void saveHub() {
        try {
            config.setHubFromInput(hubInput.getText().toString());
            hubInput.setText("");
            hubSummary.setText(config.hubSummary());
            hubSummary.setTextColor(MINT);
            toast("Laptop HUB kaydedildi. Test ediliyor…");
            testHub();
        } catch (Exception ex) {
            toast(ex.getMessage());
        }
    }

    private void testHub() {
        if (!config.hasHub()) { toast("Önce laptop HUB tokenını/adresini kaydet."); return; }
        hubSummary.setText("HUB test ediliyor…");
        hubSummary.setTextColor(ORANGE);
        io.execute(() -> {
            try {
                HubClient hub = new HubClient(this);
                String health = hub.health();
                String pcStatus = hub.pcStatus(); // authenticated: tokenı da gerçekten doğrular
                runOnUiThread(() -> {
                    hubSummary.setText(config.hubSummary() + " • " + health + " • " + pcStatus);
                    hubSummary.setTextColor(MINT);
                    toast("Telefon → laptop HUB bağlantısı tamam ✓");
                });
            } catch (Exception ex) {
                runOnUiThread(() -> {
                    hubSummary.setText("HUB erişilemiyor • " + ex.getMessage());
                    hubSummary.setTextColor(RED);
                    toast("HUB testi başarısız: " + ex.getMessage());
                });
            }
        });
    }

    private void hubWakePc() {
        if (!config.hasHub()) { toast("Önce laptop HUB'ı bağla."); return; }
        toast("Laptop PC'ye açma paketi gönderiyor…");
        io.execute(() -> {
            try {
                String result = new HubClient(this).wakePc();
                runOnUiThread(() -> toast(result));
            } catch (Exception ex) {
                runOnUiThread(() -> toast("PC açılamadı: " + ex.getMessage()));
            }
        });
    }

    private void hubPcStatus() {
        if (!config.hasHub()) { toast("Önce laptop HUB'ı bağla."); return; }
        toast("Laptop PC durumunu kontrol ediyor…");
        io.execute(() -> {
            try {
                String status = new HubClient(this).pcStatus();
                runOnUiThread(() -> toast(status));
            } catch (Exception ex) {
                runOnUiThread(() -> toast("PC durumu alınamadı: " + ex.getMessage()));
            }
        });
    }

    private void power(String action) {
        io.execute(() -> {
            try {
                new BridgeClient(this).powerAction(action);
                runOnUiThread(() -> toast("PC komutu gönderildi: " + action.toUpperCase(Locale.ROOT)));
            } catch (Exception ex) {
                runOnUiThread(() -> toast("Komut gönderilemedi: " + ex.getMessage()));
            }
        });
    }

    private void smartPowerToggle() {
        toast("PC durumu kontrol ediliyor…");
        io.execute(() -> {
            try {
                String result = new BridgeClient(this).smartPowerToggle();
                runOnUiThread(() -> {
                    toast(result);
                    renderState();
                });
            } catch (Exception ex) {
                runOnUiThread(() -> toast("Güç işlemi başarısız: " + ex.getMessage()));
            }
        });
    }

    private void renderState() {
        boolean on = BridgeState.connected;
        statusTitle.setText(on ? "BAĞLI • " + BridgeState.route : "OFFLINE");
        statusTitle.setTextColor(on ? MINT : RED);
        statusMeta.setText(on
                ? BridgeState.latencyMs + " ms • Protocol " + BridgeState.protocolVersion
                : "LAN / Tailscale otomatik seçim");
        if (pcValue != null) pcValue.setText(BridgeState.computer);
        long age = BridgeState.lastSyncMs <= 0 ? -1 : (System.currentTimeMillis() - BridgeState.lastSyncMs) / 1000;
        if (syncValue != null) syncValue.setText(age < 0 ? "—" : age + " sn önce");
        errorText.setText(BridgeState.lastError.isEmpty() ? "" : "GERÇEK HATA • " + BridgeState.lastError);
        errorText.setVisibility(BridgeState.lastError.isEmpty() ? View.GONE : View.VISIBLE);
        diagText.setText("LAN          " + BridgeState.lanDiagnostic + "\nTAILSCALE    " + BridgeState.tailscaleDiagnostic);
        boolean access = DeviceSnapshot.notificationAccess(this);
        notifAccess.setText(access ? "● Bildirim erişimi AÇIK" : "● Bildirim erişimi KAPALI");
        notifAccess.setTextColor(access ? MINT : ORANGE);
        if (permissionSummary != null) {
            boolean accessibility = DeviceSnapshot.accessibilityAccess(this);
            boolean overlay = Settings.canDrawOverlays(this);
            boolean battery = DeviceSnapshot.batteryOptimizationIgnored(this);
            boolean usage = DeviceSnapshot.usageAccess(this);
            boolean write = Settings.System.canWrite(this);
            boolean dnd = DeviceSnapshot.dndAccess(this);
            boolean install = DeviceSnapshot.installUnknownAccess(this);
            boolean camera = DeviceSnapshot.cameraAccess(this);
            boolean answerCalls = DeviceSnapshot.answerCallsAccess(this);
            boolean location = DeviceSnapshot.locationAccess(this);
            permissionSummary.setText(
                    "BİLDİRİM        " + (access ? "✓ AÇIK" : "○ KAPALI") + "\n" +
                    "ACCESSIBILITY   " + (accessibility ? "✓ AÇIK" : "○ KAPALI") + "\n" +
                    "OVERLAY         " + (overlay ? "✓ AÇIK" : "○ KAPALI") + "\n" +
                    "PİL MUAFİYETİ   " + (battery ? "✓ AÇIK" : "○ KAPALI") + "\n" +
                    "KULLANIM        " + (usage ? "✓ AÇIK" : "○ KAPALI") + "\n" +
                    "SİSTEM AYARLARI " + (write ? "✓ AÇIK" : "○ KAPALI") + "\n" +
                    "DND             " + (dnd ? "✓ AÇIK" : "○ KAPALI") + "\n" +
                    "APK YÜKLEME     " + (install ? "✓ AÇIK" : "○ KAPALI") + "\n" +
                    "FENER/KAMERA     " + (camera ? "✓ AÇIK" : "○ KAPALI") + "\n" +
                    "KONUM            " + (location ? "✓ AÇIK" : "○ KAPALI") + "\n" +
                    "ARAMA KONTROLÜ   " + (answerCalls ? "✓ AÇIK" : "○ KAPALI"));
            permissionSummary.setTextColor(access && accessibility ? MINT : ORANGE);
        }
        pairSummary.setText(config.pairSummary());
        if (hubSummary != null && config.hasHub() && !hubSummary.getText().toString().contains("erişilemiyor")) {
            hubSummary.setText(config.hubSummary());
            hubSummary.setTextColor(MINT);
        }
    }

    private void addAppToggle(LinearLayout parent, String name, String packageName) {
        CheckBox cb = new CheckBox(this);
        cb.setText(name);
        cb.setTextColor(TEXT);
        cb.setTextSize(15);
        cb.setGravity(Gravity.CENTER_VERTICAL);
        cb.setPadding(0, dp(7), 0, dp(7));
        cb.setButtonTintList(new ColorStateList(
                new int[][]{new int[]{android.R.attr.state_checked}, new int[]{}},
                new int[]{MINT, Color.rgb(68, 82, 103)}));
        cb.setChecked(config.packages().contains(packageName));
        cb.setOnCheckedChangeListener((b, checked) -> config.setPackageEnabled(packageName, checked));
        parent.addView(cb, matchWrap());
        appChecks.put(packageName, cb);
    }

    private Switch toggle(String title, String subtitle, boolean checked) {
        Switch sw = new Switch(this);
        sw.setChecked(checked);
        if (Build.VERSION.SDK_INT >= 23) {
            sw.setThumbTintList(new ColorStateList(
                    new int[][]{new int[]{android.R.attr.state_checked}, new int[]{}},
                    new int[]{MINT, Color.rgb(110, 120, 137)}));
            sw.setTrackTintList(new ColorStateList(
                    new int[][]{new int[]{android.R.attr.state_checked}, new int[]{}},
                    new int[]{Color.rgb(44, 102, 91), Color.rgb(42, 50, 65)}));
        }
        return sw;
    }

    private View wrapToggle(String title, String subtitle, Switch sw) {
        LinearLayout row = row(Gravity.CENTER_VERTICAL);
        LinearLayout tx = column(0);
        tx.addView(text(title, 15, TEXT, true));
        tx.addView(text(subtitle, 11, DIM, false));
        row.addView(tx, lp(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f, 0, dp(8), 0));
        row.addView(sw);
        return row;
    }

    private Button controlButton(String label, String action, int accent) {
        Button b = button(label, Color.rgb(11, 20, 33), accent);
        b.setMinHeight(0);
        b.setMinimumHeight(0);
        b.setPadding(0, 0, 0, 0);
        b.setBackground(round(Color.rgb(11, 20, 33), 14, Color.argb(150, Color.red(accent), Color.green(accent), Color.blue(accent))));
        b.setOnClickListener(v -> power(action));
        return b;
    }

    private Button smartPowerButton() {
        Button b = button("AÇ / KAPAT", Color.rgb(11, 20, 33), RED);
        b.setMinHeight(0);
        b.setMinimumHeight(0);
        b.setPadding(0, 0, 0, 0);
        b.setBackground(round(Color.rgb(11, 20, 33), 14,
                Color.argb(155, Color.red(RED), Color.green(RED), Color.blue(RED))));
        b.setOnClickListener(v -> smartPowerToggle());
        return b;
    }

    private LinearLayout cardGradient() {
        LinearLayout v = column(0);
        GradientDrawable g = new GradientDrawable(GradientDrawable.Orientation.TL_BR,
                new int[]{Color.rgb(19, 37, 61), Color.rgb(11, 20, 34), Color.rgb(15, 28, 45)});
        g.setCornerRadius(dp(22));
        g.setStroke(dp(1), Color.rgb(37, 64, 84));
        v.setBackground(g);
        v.setPadding(dp(18), dp(18), dp(18), dp(18));
        return v;
    }

    private LinearLayout card() {
        LinearLayout v = column(0);
        v.setBackground(round(CARD, 20, Color.rgb(30, 44, 63)));
        v.setPadding(dp(16), dp(16), dp(16), dp(16));
        return v;
    }

    private View section(String title, String subtitle) {
        LinearLayout v = column(0);
        v.setPadding(0, dp(22), 0, dp(9));
        v.addView(text(title, 12, MINT, true));
        v.addView(text(subtitle, 11, DIM, false));
        return v;
    }

    private LinearLayout miniStat(String label, String value, boolean pc) {
        LinearLayout box = column(Gravity.CENTER_VERTICAL);
        box.setPadding(dp(12), dp(8), dp(12), dp(8));
        box.setBackground(round(Color.rgb(8, 16, 27), 14, Color.rgb(36, 54, 74)));
        TextView l = text(label, 9, DIM, true);
        TextView val = text(value, 13, TEXT, true);
        if (pc) pcValue = val; else syncValue = val;
        box.addView(l);
        box.addView(val);
        return box;
    }

    private TextView pill(String label, int accent) {
        TextView t = text(label, 10, accent, true);
        t.setGravity(Gravity.CENTER);
        t.setPadding(dp(10), dp(7), dp(10), dp(7));
        t.setBackground(round(Color.rgb(12, 20, 33), 20, Color.argb(120, Color.red(accent), Color.green(accent), Color.blue(accent))));
        return t;
    }

    private Button button(String label, int bg, int fg) {
        Button b = new Button(this);
        b.setText(label);
        b.setTextSize(12);
        b.setTextColor(fg);
        b.setTypeface(Typeface.create("sans-serif-medium", Typeface.BOLD));
        b.setAllCaps(false);
        b.setGravity(Gravity.CENTER);
        b.setBackground(round(bg, 14));
        b.setStateListAnimator(null);
        return b;
    }

    private TextView text(String value, float sp, int color, boolean bold) {
        TextView t = new TextView(this);
        t.setText(value);
        t.setTextSize(sp);
        t.setTextColor(color);
        t.setTypeface(Typeface.create(bold ? "sans-serif-medium" : "sans-serif", bold ? Typeface.BOLD : Typeface.NORMAL));
        return t;
    }

    private LinearLayout row(int gravity) {
        LinearLayout l = new LinearLayout(this);
        l.setOrientation(LinearLayout.HORIZONTAL);
        l.setGravity(gravity);
        return l;
    }

    private LinearLayout column(int gravity) {
        LinearLayout l = new LinearLayout(this);
        l.setOrientation(LinearLayout.VERTICAL);
        if (gravity != 0) l.setGravity(gravity);
        return l;
    }

    private View divider() {
        View v = new View(this);
        v.setBackgroundColor(Color.rgb(30, 44, 63));
        v.setLayoutParams(lp(ViewGroup.LayoutParams.MATCH_PARENT, dp(1), 0, dp(12), 0, dp(12)));
        return v;
    }

    private View space(int dp) {
        View v = new View(this);
        v.setLayoutParams(new LinearLayout.LayoutParams(1, dp(dp)));
        return v;
    }

    private GradientDrawable round(int color, float radiusDp) { return round(color, radiusDp, 0); }
    private GradientDrawable round(int color, float radiusDp, int stroke) {
        GradientDrawable g = new GradientDrawable();
        g.setColor(color);
        g.setCornerRadius(dp((int) radiusDp));
        if (stroke != 0) g.setStroke(dp(1), stroke);
        return g;
    }

    private LinearLayout.LayoutParams matchWrap() {
        return new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
    }

    private LinearLayout.LayoutParams lp(int w, int h, float weight, int top, int right, int bottom) {
        LinearLayout.LayoutParams p = new LinearLayout.LayoutParams(w, h, weight);
        p.setMargins(0, top, right, bottom);
        return p;
    }

    private int dp(int value) { return Math.round(value * getResources().getDisplayMetrics().density); }
    private void toast(String text) { Toast.makeText(this, text == null ? "" : text, Toast.LENGTH_LONG).show(); }

    private void requestLocationPermission() {
        if (Build.VERSION.SDK_INT >= 23
                && checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) != PackageManager.PERMISSION_GRANTED
                && checkSelfPermission(Manifest.permission.ACCESS_COARSE_LOCATION) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.ACCESS_FINE_LOCATION, Manifest.permission.ACCESS_COARSE_LOCATION}, 620);
        } else {
            toast("Konum izni zaten açık.");
        }
    }

    private void requestNotificationPermission() {
        if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, 520);
        }
    }
}
