"""
RIPPERINIO — Gemini Live araç (tool) tanımları
Masaüstü (main.py) ve web sunucusu (ripperinio_web/server.py) ortak kullanır.
"""

TOOL_DECLARATIONS = [
    {
        "name": "open_app",
        "description": "Windows'ta uygulama açar. Steam, Spotify, Discord, VS Code, Ayarlar vb.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "app_name": {
                    "type": "STRING",
                    "description": "Uygulama adı (örn. 'Spotify', 'Safari', 'Terminal')"
                }
            },
            "required": ["app_name"]
        }
    },
    {
        "name": "sys_info",
        "description": "Sistem bilgisi alır: pil durumu, CPU, RAM, disk, saat, tarih, ağ bağlantısı.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {
                    "type": "STRING",
                    "description": "battery | cpu | ram | disk | time | date | network | all"
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "get_health_data",
        "description": (
            "Telefon/iPhone saglik export verisini okur ve ozetler/analiz eder. "
            "Kullanici adim, nabiz, uyku, egzersiz veya saglik verisini sordugunda kullan."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {
                    "type": "STRING",
                    "description": "all | bugun | dun | adim | nabiz | uyku | egzersiz veya dogal dilde analiz sorusu"
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "get_weather",
        "description": (
            "Anlik hava durumunu ozetler. Konum verilmezse kullanicinin "
            "BULUNDUGU sehir otomatik tespit edilir. "
            "Kullanici hava durumunu, sicakligi veya yagmur durumunu sordugunda kullan."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "location": {
                    "type": "STRING",
                    "description": "Sehir veya konum. Bos birakilirsa Istanbul kullanilir."
                }
            }
        }
    },
    {
        "name": "get_calendar_events",
        "description": (
            "Windows takvim arka ucunu (Outlook açıksa Outlook, aksi halde RIP yerel takvimi) okur. "
            "Bugun, yarin, siradaki etkinlik veya yaklasan ajandayi ozetler. "
            "Kullanici toplanti, takvim, ajanda, etkinlik veya gunluk programini sordugunda kullan."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {
                    "type": "STRING",
                    "description": (
                        "today | tomorrow | next | agenda | week veya dogal dilde "
                        "'onumuzdeki 30 gun', '2 hafta', 'bu ay', 'gelecek ay'"
                    )
                },
                "limit": {
                    "type": "NUMBER",
                    "description": "Maksimum etkinlik sayisi"
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "add_calendar_event",
        "description": (
            "Windows takvim arka ucuna yeni etkinlik ekler. "
            "Kullanici toplanti, randevu, takvime ekleme veya etkinlik olusturma isterse kullan. "
            "Baslangic tarihini gercek tarih/saat olarak ver; bitis verilmezse varsayilan sure kullanilir."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "title": {
                    "type": "STRING",
                    "description": "Etkinlik basligi. Ornek: 'Disci Randevusu'"
                },
                "start_iso": {
                    "type": "STRING",
                    "description": "Baslangic tarih/saat. ISO veya yyyy-MM-dd HH:mm formatinda."
                },
                "end_iso": {
                    "type": "STRING",
                    "description": "Bitis tarih/saat. Opsiyonel."
                },
                "location": {
                    "type": "STRING",
                    "description": "Etkinlik konumu. Opsiyonel."
                },
                "notes": {
                    "type": "STRING",
                    "description": "Etkinlik notlari. Opsiyonel."
                },
                "calendar_name": {
                    "type": "STRING",
                    "description": "Eklenecek takvim adi. Opsiyonel."
                },
                "all_day": {
                    "type": "BOOLEAN",
                    "description": "true ise tum gun etkinligi olusturur."
                }
            },
            "required": ["title", "start_iso"]
        }
    },
    {
        "name": "delete_calendar_event",
        "description": (
            "Windows takvim arka ucundan etkinlik siler. "
            "Kullanici bir toplantiyi, randevuyu veya takvim kaydini silmek istediginde kullan. "
            "Ayni ada birden fazla etkinlik varsa dogru kaydi bulmak icin baslangic tarihini gercek tarih/saat olarak ver."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "title": {
                    "type": "STRING",
                    "description": "Silinecek etkinlik basligi. Ornek: 'Disci Randevusu'"
                },
                "start_iso": {
                    "type": "STRING",
                    "description": "Opsiyonel tarih/saat. Ayni isimli birden fazla etkinligi ayirt etmek icin kullan."
                },
                "calendar_name": {
                    "type": "STRING",
                    "description": "Opsiyonel takvim adi"
                },
                "delete_all_matches": {
                    "type": "BOOLEAN",
                    "description": "true ise eslesen tum etkinlikleri siler"
                }
            },
            "required": ["title"]
        }
    },
    {
        "name": "get_reminders",
        "description": (
            "Windows/RIP anımsatıcı listesini okur. "
            "Bugunku, yaklasan, geciken veya tum acik animsaticilari ozetler. "
            "Kullanici hatirlatma, animsatici, reminder veya yapilacaklar listesini sordugunda kullan."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {
                    "type": "STRING",
                    "description": "today | upcoming | overdue | all | next"
                },
                "limit": {
                    "type": "NUMBER",
                    "description": "Maksimum animsatici sayisi"
                },
                "list_name": {
                    "type": "STRING",
                    "description": "Istenirse belirli bir animsatici listesi adi"
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "add_reminder",
        "description": (
            "Windows/RIP anımsatıcı listesine yeni bir anımsatıcı ekler. "
            "Kullanici 'hatirlat', 'animsatici ekle', 'reminder kur' dediginde kullan. "
            "Goreli zaman ifadelerini bugunku tarih baglamina gore due_iso alanina ISO formatinda cevir."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "title": {
                    "type": "STRING",
                    "description": "Animsatici basligi"
                },
                "due_iso": {
                    "type": "STRING",
                    "description": "Opsiyonel tarih/saat. Ornek: 2026-04-13T09:00 veya tum gun icin 2026-04-13"
                },
                "notes": {
                    "type": "STRING",
                    "description": "Opsiyonel not"
                },
                "list_name": {
                    "type": "STRING",
                    "description": "Opsiyonel animsatici listesi"
                },
                "priority": {
                    "type": "STRING",
                    "description": "low | medium | high"
                },
                "all_day": {
                    "type": "BOOLEAN",
                    "description": "Tum gun animsatici ise true"
                }
            },
            "required": ["title"]
        }
    },
    {
        "name": "browser_control",
        "description": "Tarayıcıda URL açar, Google'da arama yapar veya YouTube'da ilk sonucu doğrudan oynatır.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "open_url | search | play_youtube"},
                "url":    {"type": "STRING", "description": "Açılacak URL (open_url için)"},
                "query":  {"type": "STRING", "description": "Arama sorgusu (search veya play_youtube için)"}
            },
            "required": ["action"]
        }
    },
    {
        "name": "shell_run",
        "description": "Windows PowerShell komutu çalıştırır. Geniş/riskli sistem değişikliklerinde shell_control tercih et.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "command": {
                    "type": "STRING",
                    "description": "Çalıştırılacak bash komutu"
                }
            },
            "required": ["command"]
        }
    },
    {
        "name": "toggle_webcam",
        "description": (
            "Gerçek zamanlı webcam akışını başlatır veya durdurur. "
            "Akış aktifken model sürekli kamera görüntüsü alır — 'bak', 'gör', 'göster', "
            "'kameraya bak', 'önümdekileri anlat', 'ne görüyorsun' gibi komutlarda 'start' kullan. "
            "'kamerayı kapat', 'artık bakma' gibi durumlarda 'stop' kullan."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "start — akışı başlat  |  stop — akışı durdur"
                }
            },
            "required": ["action"]
        }
    },
    {
        "name": "play_media",
        "description": (
            "YouTube, Spotify veya Apple Music/Music uygulamasında şarkı, müzik veya video açar. "
            "Kullanıcı belirli bir platform söylerse onu kullan. "
            "Belirtmezse uygun olanı dene. "
            "Kullanıcı 'çal', 'oynat', 'aç' diyorsa autoplay=true kullan."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {
                    "type": "STRING",
                    "description": "Şarkı, sanatçı, albüm veya video arama ifadesi"
                },
                "provider": {
                    "type": "STRING",
                    "description": "auto | youtube | spotify | apple_music"
                },
                "autoplay": {
                    "type": "BOOLEAN",
                    "description": "true ise mümkünse doğrudan oynatır"
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "control_media",
        "description": (
            "Halihazirda calan medyayi kontrol eder: durdurur, devam ettirir, "
            "sonraki/onceki parcaya gecer. Spotify, YouTube veya hangi oynatici "
            "caliyorsa ona gider. "
            "Kullanici 'durdur', 'duraklat', 'sustur', 'muzigi kapat', 'devam et', "
            "'sonraki sarki', 'gec', 'onceki' gibi bir sey soyledigINDE bunu kullan. "
            "Yeni bir sarki BASLATMAK icin bu araci degil play_media'yi kullan."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": (
                        "pause (durdur/duraklat) | resume (devam et) | stop (tamamen durdur) | "
                        "next (sonraki parca) | previous (onceki parca)"
                    )
                }
            },
            "required": ["action"]
        }
    },
    {
        "name": "control_volume",
        "description": (
            "Windows ana ses seviyesini okur, belirli bir yüzdeye ayarlar, artırır, "
            "kısar veya sessize alır/açar. Kullanıcı 'sesi yüzde 35 yap', "
            "'sesi 10 artır', 'sesi kıs', 'sessize al', 'sesi aç' veya "
            "'ses kaçta' dediğinde bu aracı kullan."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": "get | set | change | up | down | mute | unmute | toggle_mute"
                },
                "value": {
                    "type": "NUMBER",
                    "description": "set için hedef yüzde; change/up/down için değişim miktarı"
                }
            },
            "required": ["action"]
        }
    },
    {
        "name": "get_youtube_channel_report",
        "description": (
            "YouTube kanalinin public istatistiklerini ve son videolarin performansini raporlar. "
            "Kullanici kanal istatistiklerini, abone sayisini, son videolarini, buyume hizini "
            "veya YouTube analizini sordugunda kullan. Bu arac Studio yerine public YouTube Data API verisini kullanir."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {
                    "type": "STRING",
                    "description": (
                        "Dogal dilde analiz istegi. Ornek: "
                        "'YouTube istatistiklerim nasil', 'son videolarimi analiz et', "
                        "'kanal buyumemi ozetle'"
                    )
                },
                "handle": {
                    "type": "STRING",
                    "description": (
                        "Opsiyonel kanal handle'i, kanal linki veya kanal ID'si. "
                        "Bos birakilirsa ayarlardaki youtube_channel_handle kullanilir."
                    )
                },
                "video_limit": {
                    "type": "NUMBER",
                    "description": "Analize dahil edilecek son video sayisi. Varsayilan 6."
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "analyze_screen",
        "description": (
            "Aktif pencerenin ekran goruntusunu alip Gemini vision ile analiz eder. "
            "Kullanici ekranda ne oldugunu, bir hatayi, gorunen metni, butonlari veya pencere icerigini sordugunda kullan. "
            "Bu surum yalnizca aktif pencereyi destekler."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {
                    "type": "STRING",
                    "description": "Kullanicinin ekranla ilgili sorusu. Ornek: 'Bu hatayi oku', 'Ekranda ne var?'"
                },
                "target": {
                    "type": "STRING",
                    "description": "Su an sadece active_window desteklenir."
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "screen_target",
        "description": (
            "Aktif pencerede kullanicinin tarif ettigi buton/ayar/alan gibi bir UI ogesini Gemini vision ile bulur "
            "ve RIP HUD uzerinde neon kutu/ok ile gosterir. Kullanici 'su ayar nerede', 'bunu ekranda goster', "
            "'hangi butona tiklayacagim' dediginde kullan."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {
                    "type": "STRING",
                    "description": "Ekranda bulunacak oge. Ornek: 'FreeSync ayari', 'Kaydet butonu', 'Bildirimler sekmesi'"
                },
                "target": {
                    "type": "STRING",
                    "description": "Su an active_window desteklenir."
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "history_control",
        "description": (
            "RIP komut/arac gecmisini ve kullanicinin Ctrl+C, Ctrl+V, Alt+Tab, Win+E gibi Windows kisayollarini "
            "gosterir. action=undo yalnizca guvenli sekilde tersine cevrilebildigi kayitli son RIP islemini geri alir. "
            "V5; ses, pencere konumu/boyutu, pano, HUD tema, dosya tasima ve Drop Zone kopyalarini guvenli kosullarda geri alabilir."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "latest | undo"},
                "limit": {"type": "INTEGER", "description": "latest icin kayit sayisi, varsayilan 12"}
            }
        }
    },
    {
        "name": "clipboard_sync",
        "description": (
            "PC ve Android RIP Phone Bridge arasinda komutla pano senkronu yapar. Surekli gizli pano izlemez. "
            "send/to_phone PC panosunu veya verilen text'i Always-On Bridge ile telefona yollar ve Android ACK bekler; "
            "pull/from_phone telefonda RIP Bridge icinden kullanicinin son paylastigi panoyu PC'ye getirir."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "send | pull | set_pc"},
                "text": {"type": "STRING", "description": "Opsiyonel metin; bos ise send PC panosunu kullanir."}
            }
        }
    },
    {
        "name": "save_memory",
        "description": "Kullanıcı hakkında önemli bilgiyi kalıcı belleğe kaydeder. İsim, tercihler, projeler vb. duyunca sessizce çağır.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "category": {
                    "type": "STRING",
                    "description": "identity | preferences | projects | notes"
                },
                "key":   {"type": "STRING", "description": "Kısa anahtar (örn. 'name')"},
                "value": {"type": "STRING", "description": "Değer (İngilizce)"}
            },
            "required": ["category", "key", "value"]
        }
    },
    {
        "name": "delete_memory",
        "description": (
            "Kalici hafizadaki bir kaydi siler. "
            "Kullanici 'bunu hafizandan kaldir', 'unut', 'sil' gibi bir sey derse kullan. "
            "Mumkunse category ve key ile sil; emin degilsen match_text ile ilgili kaydi bulup kaldir."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "category": {
                    "type": "STRING",
                    "description": "Kaydin kategorisi. Ornek: notes | identity | preferences | projects"
                },
                "key": {
                    "type": "STRING",
                    "description": "Silinecek anahtar. Ornek: claude_limit_refresh"
                },
                "match_text": {
                    "type": "STRING",
                    "description": "Kaydi bulmak icin kullanilacak dogal dil parcasi. Ornek: 'claude ai limit yenilenmesi'"
                }
            }
        }
    },
    {
        "name": "send_whatsapp_message",
        "description": (
            "WhatsApp Desktop veya WhatsApp Web üzerinden mesaj taslağı açar veya mesajı gönderir. "
            "Kişi adı veya telefon numarasıyla çalışabilir. "
            "Telefon numarası verilmemişse kişi adını önce kayıtlı WhatsApp kişileri ve içe aktarılan telefon rehberinde ara. "
            "Kullanıcı 'gönder', 'yolla', 'ile', 'hemen gönder' gibi açık bir gönderme niyeti söylüyorsa "
            "ekstra onay istemeden send_now=true kullan. "
            "Yalnızca 'hazırla', 'taslak aç', 'yaz ama gönderme' diyorsa send_now=false kullan."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "recipient_name": {
                    "type": "STRING",
                    "description": "Kişi adı. Örn: 'Anne', 'Ahmet', 'Ece'"
                },
                "phone_number": {
                    "type": "STRING",
                    "description": "Uluslararası telefon numarası. Örn: +905551112233"
                },
                "message": {
                    "type": "STRING",
                    "description": "Gönderilecek mesaj içeriği"
                },
                "app_target": {
                    "type": "STRING",
                    "description": "desktop | web | auto. Varsayılan auto, tercihen desktop."
                },
                "send_now": {
                    "type": "BOOLEAN",
                    "description": "true ise sohbet açıldıktan sonra mesajı otomatik gönderir"
                }
            },
            "required": ["message"]
        }
    },
    {
        "name": "save_whatsapp_contact",
        "description": (
            "Sık kullanılan bir WhatsApp kişisini adı ve telefon numarasıyla kalıcı belleğe kaydeder. "
            "Kullanıcı bir kişiyi 'annem', 'Ahmet', 'iş ortağım' gibi tekrar kullanılacak şekilde tanımladığında kullan."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "display_name": {
                    "type": "STRING",
                    "description": "Kaydedilecek kişi adı. Örn: 'Annem', 'Ahmet'"
                },
                "phone_number": {
                    "type": "STRING",
                    "description": "Uluslararası telefon numarası. Örn: +905551112233"
                },
                "aliases": {
                    "type": "STRING",
                    "description": "Virgülle ayrılmış alternatif hitaplar. Örn: 'anne, annem, mom'"
                }
            },
            "required": ["display_name", "phone_number"]
        }
    }
,
    {
        "name": "ui_control",
        "description": (
            "Windows masaüstünde doğrudan mouse/klavye kontrolü yapar. Koordinata tıklama, fare taşıma, metin yazma, "
            "tuş/kısayol gönderme ve kaydırma için kullan. Ödeme/silme/çıkış gibi riskli hedeflerde araç ONAY GEREKLI döndürür. "
            "Ekrandaki öğeyi ada göre bulmak için ui_navigate daha uygundur."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type":"STRING", "description":"click | double_click | move | type | press | hotkey | scroll"},
                "x": {"type":"NUMBER", "description":"X koordinatı veya scroll miktarı"},
                "y": {"type":"NUMBER", "description":"Y koordinatı veya scroll miktarı"},
                "text": {"type":"STRING", "description":"Yazılacak metin"},
                "keys": {"type":"STRING", "description":"ctrl+l, enter, alt+tab gibi tuşlar"},
                "button": {"type":"STRING", "description":"left | right | middle"},
                "clicks": {"type":"NUMBER", "description":"Tıklama sayısı"},
                "confirmed": {"type":"BOOLEAN", "description":"Riskli işlem kullanıcı tarafından açıkça onaylandıysa true"}
            },
            "required": ["action"]
        }
    },
    {
        "name": "ui_navigate",
        "description": (
            "V3 App Navigation: aktif veya adı verilen Windows uygulamasında UI Automation ile öğe bulup tıklar/yazar, tuş yollar, "
            "scroll yapar veya öğeleri inceler. UIA bulamazsa screen-vision fallback deneyebilir."
        ),
        "parameters": {
            "type":"OBJECT",
            "properties": {
                "action":{"type":"STRING","description":"click | double_click | type | hotkey | scroll | inspect"},
                "window_title":{"type":"STRING","description":"Pencere başlığı veya boşsa aktif pencere"},
                "target":{"type":"STRING","description":"Buton/sekme/alan adı"},
                "text":{"type":"STRING","description":"Yazılacak metin"},
                "keys":{"type":"STRING","description":"Gönderilecek kısayol"},
                "direction":{"type":"STRING","description":"up | down | left | right"},
                "amount":{"type":"NUMBER","description":"Kaydırma miktarı"},
                "method":{"type":"STRING","description":"auto | uia | vision"},
                "clear_first":{"type":"BOOLEAN","description":"Yazmadan önce alanı temizle"},
                "admin":{"type":"BOOLEAN","description":"Yönetici pencere hedefi"},
                "confirmed":{"type":"BOOLEAN","description":"Riskli öğe açıkça onaylandıysa true"}
            },
            "required":["action"]
        }
    },
    {
        "name":"app_control",
        "description":"Windows uygulamasını açar veya kapatır. admin/force işlemleri açık onay ve normal UAC akışı kullanır.",
        "parameters":{"type":"OBJECT","properties":{
            "action":{"type":"STRING","description":"open | close"},
            "app_name":{"type":"STRING"},"admin":{"type":"BOOLEAN"},"force":{"type":"BOOLEAN"},
            "arguments":{"type":"STRING"},"confirmed":{"type":"BOOLEAN"}
        },"required":["action","app_name"]}
    },
    {
        "name":"window_control",
        "description":"Görünür Windows pencerelerini listeler, odaklar, küçültür, büyütür, geri getirir, kapatır veya taşır/boyutlandırır.",
        "parameters":{"type":"OBJECT","properties":{
            "action":{"type":"STRING","description":"list | focus | minimize | maximize | restore | close | move_resize"},
            "title":{"type":"STRING"},"x":{"type":"NUMBER"},"y":{"type":"NUMBER"},
            "width":{"type":"NUMBER"},"height":{"type":"NUMBER"}
        },"required":["action"]}
    },
    {
        "name":"file_control",
        "description":"Dosya/klasör oluşturur, taşır veya yeniden adlandırır. Silme yapmaz; korumalı/overwrite/admin işlemleri açık onay ister.",
        "parameters":{"type":"OBJECT","properties":{
            "action":{"type":"STRING","description":"create_file | create_folder | move | rename"},
            "source":{"type":"STRING"},"target":{"type":"STRING"},"content":{"type":"STRING"},
            "overwrite":{"type":"BOOLEAN"},"admin":{"type":"BOOLEAN"},"confirmed":{"type":"BOOLEAN"}
        },"required":["action"]}
    },
    {
        "name":"process_control",
        "description":"Süreçleri listeler veya normal/zorla sonlandırır. Kritik Windows süreçleri korunur; force/admin açık onay ister.",
        "parameters":{"type":"OBJECT","properties":{
            "action":{"type":"STRING","description":"list | terminate | kill"},"query":{"type":"STRING"},
            "pid":{"type":"NUMBER"},"force":{"type":"BOOLEAN"},"admin":{"type":"BOOLEAN"},
            "confirmed":{"type":"BOOLEAN"},"limit":{"type":"NUMBER"}
        },"required":["action"]}
    },
    {
        "name":"service_control",
        "description":"Windows hizmetlerini listeler/durumunu okur; normal hizmetleri açık onay + UAC ile başlatır/durdurur/restart eder. Kritik hizmetler korunur.",
        "parameters":{"type":"OBJECT","properties":{
            "action":{"type":"STRING","description":"list | status | start | stop | restart"},
            "service_name":{"type":"STRING"},"confirmed":{"type":"BOOLEAN"},"limit":{"type":"NUMBER"}
        },"required":["action"]}
    },
    {
        "name":"windows_control",
        "description":"Windows Ayarları, Görev Yöneticisi, ağ adaptörü/DNS/IP ve desteklenen cihazlarda parlaklık işlemleri. Ağı etkileyen değişiklikler açık onay ister.",
        "parameters":{"type":"OBJECT","properties":{
            "action":{"type":"STRING"},"page":{"type":"STRING"},"adapter":{"type":"STRING"},
            "value":{"type":"NUMBER"},"confirmed":{"type":"BOOLEAN"}
        },"required":["action"]}
    },
    {
        "name":"shell_control",
        "description":"Windows CMD/PowerShell komutunu güvenlik katmanıyla çalıştırır. Disk/boot/güvenlik devre dışı bırakma, hesap/persistence ve güç bypass komutları engellidir.",
        "parameters":{"type":"OBJECT","properties":{
            "shell":{"type":"STRING","description":"powershell | cmd"},"command":{"type":"STRING"},
            "admin":{"type":"BOOLEAN"},"confirmed":{"type":"BOOLEAN"},"timeout":{"type":"NUMBER"}
        },"required":["shell","command"]}
    },
    {
        "name":"phone_messages",
        "description":"Kendi Android RIP Phone Bridge uygulamasından gelen mesaj/bildirim veya arama geçmişini okur.",
        "parameters":{"type":"OBJECT","properties":{
            "action":{"type":"STRING","description":"latest | list | status | calls"},"limit":{"type":"NUMBER"},
            "app_filter":{"type":"STRING","description":"WhatsApp, Instagram, Telegram, Messages vb."}
        },"required":["action"]}
    },
    {
        "name":"phone_status",
        "description":"Bağlı Android telefonun pil, şarj, sıcaklık, ses, depolama, LAN/Tailscale Bridge, gecikme ve bildirim/Accessibility/overlay/pil muafiyeti izin durumunu okur.",
        "parameters":{"type":"OBJECT","properties":{
            "query":{"type":"STRING","description":"all | battery | ringer | storage"}
        },"required":["query"]}
    },
    {
        "name":"phone_control",
        "description":"Eşleştirilmiş kişisel Android telefonda kontrolleri çalıştırır. Accessibility sistem gezinmesi için gerekir. Uygulama kaldırma Android onay ekranını açar ve confirmed=true ister. Ses kanalları, parlaklık, ekran süresi, otomatik döndürme, DND, fener ve Android ayar panelleri desteklenir.",
        "parameters":{"type":"OBJECT","properties":{
            "action":{"type":"STRING","description":"home | back | recents | notifications | quick_settings | lock_screen | open_app | open_camera | uninstall_app | volume_set | brightness_set | screen_timeout_set | autorotate_set | dnd_toggle | flashlight_toggle | open_system_panel | media_play_pause | media_next | media_previous | vibrate | find_phone | get_location | clear_notifications | refresh_inventory | open_permission | screen_stream_start | screen_stream_stop"},
            "target":{"type":"STRING","description":"Uygulama adı/paket adı; volume_set için music/ring/alarm/notification; open_system_panel için wifi/bluetooth/display/sound/apps; open_permission için notification/accessibility/overlay/battery/usage/write_settings/install_unknown/dnd/camera/answer_calls/location"},
            "value":{"type":"NUMBER","description":"volume/brightness için 0-100; screen_timeout_set için saniye; autorotate_set için 0/1; vibrate için ms; screen_stream_start için hedef FPS (10-20)"},
            "confirmed":{"type":"BOOLEAN","description":"uninstall_app gibi geri dönüşü zor işlemler için açık kullanıcı onayı"}
        },"required":["action"]}
    },
    {
        "name":"obs_control",
        "description":"Kullanıcının OBS Studio WebSocket bağlantısını kontrol eder. Kayıt/yayın/sahne işlemlerini çalıştırır; bağlantı bilgileri V6 Control Center > OBS bölümünden ayarlanır.",
        "parameters":{"type":"OBJECT","properties":{
            "action":{"type":"STRING","description":"status | record_start | record_stop | record_toggle | stream_start | stream_stop | stream_toggle | scene"},
            "target":{"type":"STRING","description":"scene işleminde sahne adı"}
        },"required":["action"]}
    },
    {
        "name":"rip_system",
        "description":"RIP V6 sistem merkezini yönetir: Doctor/self-healing çalıştırır, workspace profili uygular, ağ raporu verir veya güvenli yapılandırma yedeği oluşturur.",
        "parameters":{"type":"OBJECT","properties":{
            "action":{"type":"STRING","description":"doctor | repair | profile | network | backup | macro | macro_list"},
            "target":{"type":"STRING","description":"profile için OYUN | DERS | MEDYA | YAZILIM; macro için kayıtlı makro adı"}
        },"required":["action"]}
    },
    {
        "name":"hud_theme",
        "description":"RIP HUD temasını canlı değiştirir.",
        "parameters":{"type":"OBJECT","properties":{
            "theme":{"type":"STRING","description":"classic | cyan | pink | purple | blue | green veya Türkçe adları"}
        },"required":["theme"]}
    }

]
