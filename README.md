# RIPPERINIO V6.2.1 — HOME HUB + AUTO START SELF-HEAL

Bu paket kullanicinin V6.1.4 PC kaynaklari ile RIP Mobile V6.2.0 HOME HUB kaynaklarini tek temiz kaynak agacinda birlestirir ve Windows otomatik baslangic sorununu duzeltir.

## V6.2.1 otomatik baslangic duzeltmesi

- `scripts/AUTOSTART.ps1` Windows kullanici oturumunda RIP HUD'u otomatik baslatan `Ripperinio V6 Auto Start` gorevini kurar.
- Task Scheduler kullanilamazsa ayni is icin `HKCU\\...\\Run` fallback kaydi olusturulur.
- `BASLAT.bat` her elle calistirildiginda otomatik baslangic kayitlarini sessizce self-heal eder. RIP yeni bir klasore tasinmis/guncellenmis ise gorev artik eski klasoru acmaz.
- Telefon bridge (8765/8766) ve Mobile Remote (8770) gorevleri de her kaynak degisiminde yeni klasore yoneltilir.
- `PHONE_SERVICES.ps1` artik idempotenttir: dogru klasore bakan saglam gorevi her BASLAT'ta gereksiz yere oldurup yeniden yaratmaz.
- RIP HUD hangi yolla acilirsa acilsin `main.py` arka planda telefon bridge + Mobile Remote servislerini self-heal eder. Yani Windows baslangic gorevlerinden biri gecikse bile RIP acilinca telefon baglantisi manuel buton gerektirmeden gelir.
- Otomatik baslangic 8 saniyelik logon gecikmesi kullanir; bu, Windows masaustu ve ag/Tailscale servislerinin oturmasina zaman verir.

> Not: HUD kullanici oturumu acildiktan sonra baslar. Windows parola ekraninda bekliyorsa masaustu uygulamasi kullanici oturumu acilmadan gosterilemez.

## HOME HUB

Android kaynaklari RIP Mobile V6.2.1 HOME HUB surumudur. Telefon, Tailscale uzerinden evde acik kalan laptop HUB'a ulasip PC'ye WoL komutu yollayabilir. HUB tokeni kaynak koda gomulmez; telefonda kullanici tarafindan kaydedilir.

## Kurulum

PC'de `BASLAT.bat` dosyasini bir kez ac. Venv zaten varsa yeniden paket kurulumu yapmaz; fakat V6.2.1 otomatik baslangic kayitlarini yine gunceller.

Kontrol icin Windows Gorev Zamanlayici'da su gorevler gorunebilir:

- `Ripperinio V6 Auto Start`
- `Ripperinio V5 Persistent Bridge`
- `Ripperinio V5 Mobile Remote`

Android Studio icin `android/RIP-Mobile-V6` klasorunu ac, Gradle JDK olarak JDK 21 kullan ve mevcut uygulamanin ustune Run yap.

## Kaynak temizligi

Dagitim ZIP'ine `.venv`, `.gradle`, `.idea`, Android `build`, `local.properties`, `__pycache__`, `.pyc`, loglar, keystore veya yerel token/anahtar dosyalari dahil edilmez.


## V6.2.1b — telefon bağlantı göstergesi + konum bulma hotfix

- Alt soldaki **RIPPERINIO TELEFON** göstergesi artık yalnız Bridge process durumuna değil, Android'den gelen canlı heartbeat'e bakar. Telefon gerçekten bağlıysa yeşil **TELEFON BAĞLI** görünür.
- Phone Control Center > GENEL bölümüne **KONUMU BUL**, **HARİTADA AÇ** ve ayrı **ZİL ÇALDIR** kontrolleri eklendi.
- RIP Mobile'a konum izni ve `get_location` komutu eklendi. Konum isteği yalnız eşleştirilmiş RIP Bridge komutuyla çalışır; sonuç PC'de `persistent_bridge/phone_location.json` içinde son kayıt olarak tutulur.
- Bu paket V6.2.1 tabanıdır; GitHub self-update/bootstrap sistemi eklenmemiştir.


## V6.2.1c — referans HUD + tam telefon özellikleri

- Kullanıcının verdiği RIPPERINIO V7 HYBRID referans yerleşimi, statik görsel kullanmadan kodla yeniden oluşturulan `sistem/reference_hud.py` kabuğu olarak eklendi.
- Eski V6 çalışma zamanı kaldırılmadı; Phone Control Center, Phone Drawer, Drop Zone, telefon bul/zil, konum, dosya aktarımı, ekran kontrolü, bildirimler, pano, medya ve ses çekirdeği aynı backend üzerinde çalışmaya devam eder.
- GitHub self-update/bootstrap sistemi eklenmez ve bu varyant PC'ye GitHub'dan otomatik sürüm çekmez. Kaynak değişiklikleri ayrı `v6-reference-hud-no-autoupdate` dalında arşivlenir.
