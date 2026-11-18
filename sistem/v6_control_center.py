from __future__ import annotations

import threading
import time
import tkinter as tk
from tkinter import ttk, simpledialog, messagebox, filedialog

from hud_window_chrome import HudWindowShell, hud_button

from app_paths import data_dir
from app_config import load_app_config, save_app_config
from actions.obs_control import obs_control
from v6_core import (
    AUTOMATION_ENGINE,
    add_automation,
    apply_profile,
    audit,
    clear_automations,
    doctor_report,
    list_automations,
    network_report,
    read_audit,
    safe_backup,
    load_macros, save_macro, delete_macro, parse_macro_script, run_macro,
    sync_folder_to_phone,
    clipboard_auto_enabled, set_clipboard_auto, CLIPBOARD_ENGINE,
)

BG="#02090d"; CARD="#06141b"; CARD2="#071820"; LINE="#14323d"
TEXT="#f3fbfd"; DIM="#73878e"; MINT="#57e59a"; CYAN="#00d9f5"; PURPLE="#8a7dff"; RED="#ff566c"; GOLD="#ffc86b"


class V6ControlCenter:
    def __init__(self, root: tk.Misc, log_callback=None, phone_center=None):
        self.root=root
        self.log_callback=log_callback or (lambda _m:None)
        self.phone_center=phone_center
        self.win=None
        self.pages={}
        self.doctor_var=tk.StringVar(value="RIP Doctor hazır")
        self.net_var=tk.StringVar(value="—")
        self.auto_var=tk.StringVar(value="0 otomasyon")
        self.backup_var=tk.StringVar(value="Henüz yedek yok")
        AUTOMATION_ENGINE.start()
        if clipboard_auto_enabled():
            CLIPBOARD_ENGINE.start()

    def open(self, tab: str|None=None):
        if self.win is not None and self.win.winfo_exists():
            self.win.deiconify(); self.win.lift(); self.win.focus_force(); self.refresh()
            if tab and tab in self.pages: self.nb.select(self.pages[tab])
            return

        w=tk.Toplevel(self.root); self.win=w
        w.title("RIP // V6 CONTROL CENTER")
        w.protocol("WM_DELETE_WINDOW", self.close)
        self._hud_shell = HudWindowShell(
            w,
            title="V6 CONTROL CENTER",
            subtitle="DOCTOR // AUTOMATION // RECOVERY // PROFILES // SYNC",
            on_close=self.close,
            width=1120,
            height=760,
            min_width=940,
            min_height=620,
        )
        body=self._hud_shell.content

        # In-window module banner: richer than the old stock Toplevel header.
        head=tk.Frame(body,bg=BG); head.pack(fill="x",padx=20,pady=(14,7))
        left=tk.Frame(head,bg=BG); left.pack(side="left")
        tk.Label(left,text="RIP // V6 CONTROL CENTER",bg=BG,fg=TEXT,font=("Segoe UI",18,"bold")).pack(anchor="w")
        tk.Label(left,text="LEGACY CORE · TÜM ARAÇLAR TEK PANELDE",bg=BG,fg=DIM,font=("Segoe UI",7,"bold")).pack(anchor="w",pady=(2,0))
        badge=tk.Frame(head,bg="#05212a",highlightbackground="#0f6473",highlightthickness=1)
        badge.pack(side="right",pady=4)
        tk.Label(badge,text="●  CORE LINKED",bg="#05212a",fg=MINT,font=("Segoe UI",8,"bold"),padx=12,pady=7).pack()

        style=ttk.Style(w)
        try: style.theme_use("clam")
        except Exception: pass
        style.configure("V6.TNotebook",background=BG,borderwidth=0)
        style.configure("V6.TNotebook.Tab",background=CARD,foreground=DIM,padding=(15,10),borderwidth=0,font=("Segoe UI",9,"bold"))
        style.map("V6.TNotebook.Tab",background=[("selected","#08232c"),("active",CARD2)],foreground=[("selected",CYAN),("active",TEXT)])
        style.configure("V6.Treeview",background=CARD,fieldbackground=CARD,foreground=TEXT,rowheight=31,borderwidth=0,font=("Segoe UI",9))
        style.configure("V6.Treeview.Heading",background=CARD2,foreground=CYAN,borderwidth=0,font=("Segoe UI",9,"bold"))
        style.map("V6.Treeview",background=[("selected","#0a3340")],foreground=[("selected",TEXT)])

        self.nb=ttk.Notebook(body,style="V6.TNotebook"); self.nb.pack(fill="both",expand=True,padx=18,pady=(4,14))
        for key,title in [("doctor","DOCTOR"),("profiles","PROFİLLER"),("automation","OTOMASYON"),("macros","MAKROLAR"),("sync","SYNC"),("obs","OBS"),("network","AĞ"),("backup","YEDEK"),("audit","KAYIT")]:
            p=tk.Frame(self.nb,bg=BG); self.nb.add(p,text=title); self.pages[key]=p
        self._build_doctor(self.pages["doctor"]); self._build_profiles(self.pages["profiles"]); self._build_automation(self.pages["automation"])
        self._build_macros(self.pages["macros"]); self._build_sync(self.pages["sync"]); self._build_obs(self.pages["obs"])
        self._build_network(self.pages["network"]); self._build_backup(self.pages["backup"]); self._build_audit(self.pages["audit"])
        if tab and tab in self.pages: self.nb.select(self.pages[tab])
        self.refresh()

    def close(self):
        if self.win:
            try:self.win.destroy()
            except Exception:pass
        self.win=None

    def _geo(self,w,h):
        return f"{w}x{h}+{max(0,(self.root.winfo_screenwidth()-w)//2)}+{max(0,(self.root.winfo_screenheight()-h)//2)}"

    def _card(self,parent):
        return tk.Frame(parent,bg=CARD,highlightbackground=LINE,highlightcolor=CYAN,highlightthickness=1)

    def _btn(self,parent,text,cmd,col=CYAN):
        return hud_button(parent,text,cmd,color=col)

    def _title(self,parent,title,sub=""):
        box=tk.Frame(parent,bg=BG)
        row=tk.Frame(box,bg=BG); row.pack(fill="x")
        tk.Frame(row,bg=CYAN,width=3,height=22).pack(side="left",padx=(0,9))
        tk.Label(row,text=title,bg=BG,fg=TEXT,font=("Segoe UI",11,"bold")).pack(side="left")
        if sub:
            tk.Label(box,text=sub,bg=BG,fg=DIM,font=("Segoe UI",8)).pack(anchor="w",padx=(12,0),pady=(3,0))
        return box

    def _build_doctor(self,p):
        self._title(p,"RIP DOCTOR + SELF-HEALING","Bridge, telefon, Tailscale, mikrofon ve Gemini'yi tek kontrolde tara.").pack(fill="x",padx=14,pady=(18,8))
        c=self._card(p); c.pack(fill="both",expand=True,padx=14,pady=6)
        tk.Label(c,textvariable=self.doctor_var,bg=CARD,fg=MINT,font=("Segoe UI",16,"bold")).pack(anchor="w",padx=16,pady=(16,8))
        self.doctor_text=tk.Text(c,bg=CARD2,fg=TEXT,insertbackground=CYAN,relief="flat",bd=0,highlightthickness=1,highlightbackground=LINE,font=("Segoe UI",10),wrap="word",height=18); self.doctor_text.pack(fill="both",expand=True,padx=16,pady=8)
        r=tk.Frame(c,bg=CARD); r.pack(fill="x",padx=16,pady=(0,16))
        self._btn(r,"TARA",lambda:self._doctor(False),CYAN).pack(side="left")
        self._btn(r,"TARA + ONAR",lambda:self._doctor(True),MINT).pack(side="left",padx=8)
        if self.phone_center: self._btn(r,"PHONE CENTER",self.phone_center.open,PURPLE).pack(side="right")

    def _doctor(self,repair):
        self.doctor_var.set("Taranıyor…")
        def worker():
            rep=doctor_report(repair=repair)
            lines=[f"RIP HEALTH: {rep['score']}/100",""]+[f"{'✓' if x['ok'] else '✕'} {x['name']}: {x['detail']}" for x in rep["checks"]]
            def done():
                if not self.win:return
                self.doctor_var.set(f"RIP HEALTH · {rep['score']}/100")
                self.doctor_text.delete("1.0","end"); self.doctor_text.insert("1.0","\n".join(lines))
            self.root.after(0,done)
        threading.Thread(target=worker,daemon=True,name="rip-v6-doctor").start()

    def _build_profiles(self,p):
        self._title(p,"WORKSPACE PROFİLLERİ","PC + telefon ayarlarını tek tuşta düzenle.").pack(fill="x",padx=14,pady=(18,8))
        c=self._card(p); c.pack(fill="x",padx=14,pady=6)
        tk.Label(c,text="OYUN · DERS · MEDYA · YAZILIM",bg=CARD,fg=TEXT,font=("Segoe UI",12,"bold")).pack(anchor="w",padx=16,pady=(16,10))
        r=tk.Frame(c,bg=CARD); r.pack(fill="x",padx=16,pady=(0,16))
        for name,col in [("OYUN",MINT),("DERS",CYAN),("MEDYA",GOLD),("YAZILIM",PURPLE)]:
            self._btn(r,name,lambda n=name:self._apply_profile(n),col).pack(side="left",fill="x",expand=True,padx=4)
        self.profile_result=tk.StringVar(value="Profil seçildiğinde sonuç burada görünür.")
        tk.Label(p,textvariable=self.profile_result,bg=BG,fg=DIM,font=("Segoe UI",9),wraplength=900,justify="left").pack(anchor="w",padx=14,pady=10)

    def _apply_profile(self,name):
        self.profile_result.set(name+" uygulanıyor…")
        def worker():
            try: result=apply_profile(name)
            except Exception as exc: result=str(exc)
            self.root.after(0,lambda:self.profile_result.set(result))
        threading.Thread(target=worker,daemon=True).start()

    def _build_automation(self,p):
        self._title(p,"ROUTINE BUILDER","Hazır kuralları ekle; V6 arka planda koşulları izler.").pack(fill="x",padx=14,pady=(18,8))
        c=self._card(p); c.pack(fill="x",padx=14,pady=6)
        tk.Label(c,textvariable=self.auto_var,bg=CARD,fg=MINT,font=("Segoe UI",10,"bold")).pack(anchor="w",padx=16,pady=(14,8))
        row=tk.Frame(c,bg=CARD); row.pack(fill="x",padx=16,pady=6)
        self._btn(row,"TELEFON %20 → TİTREŞTİR",lambda:self._preset("battery"),GOLD).pack(side="left",padx=4)
        self._btn(row,"STEAM AÇILIRSA → OYUN",lambda:self._preset("steam"),MINT).pack(side="left",padx=4)
        self._btn(row,"TELEFON BAĞLANIRSA → DND AÇ",lambda:self._preset("connected"),PURPLE).pack(side="left",padx=4)
        row2=tk.Frame(c,bg=CARD); row2.pack(fill="x",padx=16,pady=(6,14))
        self._btn(row2,"SAATE GÖRE PROFİL",self._add_time_rule,CYAN).pack(side="left",padx=4)
        self._btn(row2,"TÜM KURALLARI TEMİZLE",self._clear_rules,RED).pack(side="right",padx=4)
        self.auto_text=tk.Text(p,bg=CARD,fg=TEXT,relief="flat",font=("Cascadia Mono",9),height=18); self.auto_text.pack(fill="both",expand=True,padx=14,pady=10)

    def _preset(self,key):
        if key=="battery": add_automation({"trigger":"phone_battery_below","value":20,"action":"phone_vibrate","action_value":800,"cooldown_sec":1800})
        elif key=="steam": add_automation({"trigger":"process_running","value":"steam.exe","action":"profile","action_value":"OYUN","cooldown_sec":3600})
        else: add_automation({"trigger":"phone_connected","value":True,"action":"phone_dnd_on","cooldown_sec":3600})
        self.refresh()

    def _add_time_rule(self):
        value=simpledialog.askstring("Saat","HH:MM (örn 19:30)",parent=self.win)
        if not value:return
        profile=simpledialog.askstring("Profil","OYUN / DERS / MEDYA / YAZILIM",parent=self.win) or "DERS"
        add_automation({"trigger":"time","value":value.strip(),"action":"profile","action_value":profile.strip().upper(),"cooldown_sec":3600})
        self.refresh()

    def _clear_rules(self):
        if messagebox.askyesno("Otomasyon","Tüm V6 otomasyonlarını temizleyeyim mi?",parent=self.win): clear_automations(); self.refresh()


    def _build_macros(self,p):
        self._title(p,"MACRO STUDIO","Kod yazmadan güvenli RIP makroları oluştur. Her satır bir yüksek seviye işlem.").pack(fill="x",padx=14,pady=(18,8))
        outer=self._card(p); outer.pack(fill="both",expand=True,padx=14,pady=6)
        left=tk.Frame(outer,bg=CARD); left.pack(side="left",fill="y",padx=(14,8),pady=14)
        tk.Label(left,text="KAYITLI MAKROLAR",bg=CARD,fg=MINT,font=("Segoe UI",10,"bold")).pack(anchor="w",pady=(0,8))
        self.macro_list=tk.Listbox(left,bg=CARD2,fg=TEXT,selectbackground="#203A5B",relief="flat",width=24,height=18,font=("Cascadia Mono",10))
        self.macro_list.pack(fill="y",expand=True)
        self.macro_list.bind("<<ListboxSelect>>",lambda _e:self._macro_load_selected())
        right=tk.Frame(outer,bg=CARD); right.pack(side="left",fill="both",expand=True,padx=(8,14),pady=14)
        self.macro_name=tk.StringVar(value="YENİ_MAKRO")
        name_row=tk.Frame(right,bg=CARD); name_row.pack(fill="x")
        tk.Label(name_row,text="AD",bg=CARD,fg=DIM,font=("Segoe UI",9,"bold")).pack(side="left")
        tk.Entry(name_row,textvariable=self.macro_name,bg=CARD2,fg=TEXT,insertbackground=TEXT,relief="flat",font=("Cascadia Mono",10)).pack(side="left",fill="x",expand=True,padx=8,ipady=6)
        self.macro_editor=tk.Text(right,bg=CARD2,fg=TEXT,insertbackground=TEXT,relief="flat",font=("Cascadia Mono",10),height=18,wrap="none")
        self.macro_editor.pack(fill="both",expand=True,pady=10)
        self.macro_editor.insert("1.0","# Örnek\nOPEN steam\nWAIT 1\nPROFILE OYUN\nPHONE vibrate 700\n")
        help_text="Komutlar: OPEN <uygulama> · PROFILE OYUN/DERS/MEDYA/YAZILIM · VOLUME 0-100 · WAIT saniye · PHONE home/back/recents/vibrate/find_phone/media_play_pause/..."
        tk.Label(right,text=help_text,bg=CARD,fg=DIM,wraplength=650,justify="left",font=("Segoe UI",8)).pack(anchor="w")
        row=tk.Frame(right,bg=CARD); row.pack(fill="x",pady=(10,0))
        self._btn(row,"KAYDET",self._macro_save,MINT).pack(side="left",padx=4)
        self._btn(row,"▶ ÇALIŞTIR",self._macro_run,CYAN).pack(side="left",padx=4)
        self._btn(row,"SİL",self._macro_delete,RED).pack(side="right",padx=4)
        self.macro_status=tk.StringVar(value="Hazır")
        tk.Label(right,textvariable=self.macro_status,bg=CARD,fg=MINT,font=("Segoe UI",9,"bold"),wraplength=650,justify="left").pack(anchor="w",pady=(8,0))

    def _refresh_macros(self):
        if not hasattr(self,"macro_list"): return
        selected=self.macro_name.get().strip().upper() if hasattr(self,"macro_name") else ""
        self.macro_list.delete(0,"end")
        names=sorted(load_macros())
        for n in names:self.macro_list.insert("end",n)
        if selected in names:
            idx=names.index(selected); self.macro_list.selection_set(idx); self.macro_list.see(idx)

    def _macro_load_selected(self):
        if not self.macro_list.curselection(): return
        name=self.macro_list.get(self.macro_list.curselection()[0])
        self.macro_name.set(name)
        rows=load_macros().get(name,[])
        lines=[]
        for step in rows:
            kind=step.get("type")
            if kind=="open_app": lines.append("OPEN "+str(step.get("value") or ""))
            elif kind=="profile": lines.append("PROFILE "+str(step.get("value") or ""))
            elif kind=="pc_volume": lines.append("VOLUME "+str(step.get("value") or 50))
            elif kind=="wait": lines.append("WAIT "+str(step.get("value") or 1))
            elif kind=="phone":
                extra="" if step.get("value") is None else " "+str(step.get("value"))
                lines.append("PHONE "+str(step.get("action") or "")+extra)
        self.macro_editor.delete("1.0","end"); self.macro_editor.insert("1.0","\n".join(lines)+"\n")

    def _macro_save(self):
        try:
            name=self.macro_name.get().strip().upper(); steps=parse_macro_script(self.macro_editor.get("1.0","end")); save_macro(name,steps)
            self.macro_status.set(f"{name} kaydedildi · {len(steps)} adım"); self._refresh_macros()
        except Exception as exc:self.macro_status.set("Hata: "+str(exc))

    def _macro_run(self):
        name=self.macro_name.get().strip().upper()
        if not name:return
        self.macro_status.set(name+" çalışıyor…")
        def worker():
            try: result=run_macro(name)
            except Exception as exc: result="Hata: "+str(exc)
            self.root.after(0,lambda:self.macro_status.set(result))
        threading.Thread(target=worker,daemon=True,name="rip-v6-macro").start()

    def _macro_delete(self):
        name=self.macro_name.get().strip().upper()
        if not name:return
        if messagebox.askyesno("Makro",f"{name} makrosunu sileyim mi?",parent=self.win):
            delete_macro(name); self.macro_status.set("Silindi: "+name); self._refresh_macros()

    def _build_sync(self,p):
        self._title(p,"RIP SYNC","PC klasöründeki değişen dosyaları tek tıkla telefonun Downloads/RIP klasörüne gönder.").pack(fill="x",padx=14,pady=(18,8))
        c=self._card(p); c.pack(fill="x",padx=14,pady=6)
        self.sync_path=tk.StringVar(value="")
        row=tk.Frame(c,bg=CARD); row.pack(fill="x",padx=16,pady=(16,8))
        tk.Entry(row,textvariable=self.sync_path,bg=CARD2,fg=TEXT,insertbackground=TEXT,relief="flat",font=("Cascadia Mono",10)).pack(side="left",fill="x",expand=True,ipady=7)
        self._btn(row,"KLASÖR SEÇ",self._sync_pick,CYAN).pack(side="left",padx=(8,0))
        self._btn(c,"⇄ ŞİMDİ SENKRONLA",self._sync_now,MINT).pack(anchor="w",padx=16,pady=(0,12))
        self.sync_status=tk.StringVar(value="İlk senkron değişen dosyaları telefona gönderir. Sonraki turda aynı dosyalar atlanır.")
        tk.Label(c,textvariable=self.sync_status,bg=CARD,fg=DIM,font=("Segoe UI",9),wraplength=850,justify="left").pack(anchor="w",padx=16,pady=(0,16))
        tk.Label(p,text="Güvenlik: otomatik arka plan klasör izleme yerine manuel tek-tık incremental sync kullanır. Dosyalar telefonda SHA-256 ile doğrulanır.",bg=BG,fg=DIM,font=("Segoe UI",9),wraplength=900,justify="left").pack(anchor="w",padx=14,pady=10)
        clip=self._card(p); clip.pack(fill="x",padx=14,pady=6)
        self.clip_auto_var=tk.BooleanVar(value=clipboard_auto_enabled())
        tk.Checkbutton(clip,text="PC PANOSUNU TELEFONA OTOMATİK SENKRONLA",variable=self.clip_auto_var,command=self._toggle_clip_auto,bg=CARD,fg=MINT,selectcolor=CARD2,activebackground=CARD,activeforeground=TEXT,font=("Segoe UI",9,"bold")).pack(anchor="w",padx=16,pady=(14,6))
        tk.Label(clip,text="Telefon → PC tarafı Android gizlilik kısıtları nedeniyle RIP Mobile'daki 'PANOMU PC'YE GÖNDER' ile kontrollü kalır.",bg=CARD,fg=DIM,font=("Segoe UI",8),wraplength=850,justify="left").pack(anchor="w",padx=16,pady=(0,14))

    def _toggle_clip_auto(self):
        try:
            set_clipboard_auto(bool(self.clip_auto_var.get()))
            self.sync_status.set("PC → telefon otomatik pano " + ("açık" if self.clip_auto_var.get() else "kapalı"))
        except Exception as exc:
            self.sync_status.set("Pano sync hatası: "+str(exc))

    def _sync_pick(self):
        path=filedialog.askdirectory(parent=self.win,title="Telefona senkronlanacak PC klasörü")
        if path:self.sync_path.set(path)

    def _sync_now(self):
        path=self.sync_path.get().strip()
        if not path:self.sync_status.set("Önce klasör seç."); return
        self.sync_status.set("Senkronlanıyor…")
        def worker():
            try:
                r=sync_folder_to_phone(path); text=f"{r['count']} dosya gönderildi"
                if r.get('errors'): text += " · Hatalar: " + " | ".join(r['errors'][:4])
            except Exception as exc:text="Sync hatası: "+str(exc)
            self.root.after(0,lambda:self.sync_status.set(text))
        threading.Thread(target=worker,daemon=True,name="rip-v6-sync").start()

    def _build_obs(self,p):
        self._title(p,"OBS CONTROL","OBS Studio 28+ içindeki WebSocket sunucusuna bağlanır. OBS > Tools > WebSocket Server Settings açık olmalı.").pack(fill="x",padx=14,pady=(18,8))
        c=self._card(p); c.pack(fill="x",padx=14,pady=6)
        cfg=load_app_config()
        self.obs_host=tk.StringVar(value=str(cfg.get("obs_host") or "127.0.0.1"))
        self.obs_port=tk.StringVar(value=str(cfg.get("obs_port") or 4455))
        self.obs_password=tk.StringVar(value=str(cfg.get("obs_password") or ""))
        self.obs_scene=tk.StringVar(value="")
        grid=tk.Frame(c,bg=CARD); grid.pack(fill="x",padx=16,pady=14)
        for i,(label,var,show) in enumerate([("HOST",self.obs_host,""),("PORT",self.obs_port,""),("PAROLA",self.obs_password,"*")]):
            tk.Label(grid,text=label,bg=CARD,fg=DIM,font=("Segoe UI",8,"bold")).grid(row=i,column=0,sticky="w",pady=5)
            tk.Entry(grid,textvariable=var,show=show,bg=CARD2,fg=TEXT,insertbackground=TEXT,relief="flat",font=("Cascadia Mono",10)).grid(row=i,column=1,sticky="ew",padx=8,pady=5,ipady=6)
        grid.columnconfigure(1,weight=1)
        self._btn(grid,"AYARLARI KAYDET",self._obs_save,MINT).grid(row=0,column=2,rowspan=3,padx=(8,0),sticky="ns")
        row=tk.Frame(p,bg=BG); row.pack(fill="x",padx=14,pady=8)
        self._btn(row,"DURUM",lambda:self._obs_run("status"),CYAN).pack(side="left",padx=4)
        self._btn(row,"● KAYIT AÇ/KAPAT",lambda:self._obs_run("record_toggle"),RED).pack(side="left",padx=4)
        self._btn(row,"◉ YAYIN AÇ/KAPAT",lambda:self._obs_run("stream_toggle"),PURPLE).pack(side="left",padx=4)
        scene=tk.Frame(p,bg=BG); scene.pack(fill="x",padx=14,pady=8)
        tk.Entry(scene,textvariable=self.obs_scene,bg=CARD2,fg=TEXT,insertbackground=TEXT,relief="flat",font=("Cascadia Mono",10)).pack(side="left",fill="x",expand=True,ipady=7)
        self._btn(scene,"SAHNEYE GEÇ",lambda:self._obs_run("scene"),GOLD).pack(side="left",padx=(8,0))
        self.obs_status=tk.StringVar(value="OBS bağlantısı henüz test edilmedi.")
        tk.Label(p,textvariable=self.obs_status,bg=BG,fg=MINT,font=("Segoe UI",10,"bold"),wraplength=900,justify="left").pack(anchor="w",padx=14,pady=12)

    def _obs_save(self):
        try:
            port=max(1,min(65535,int(self.obs_port.get().strip() or 4455)))
            save_app_config({"obs_host":self.obs_host.get().strip() or "127.0.0.1","obs_port":port,"obs_password":self.obs_password.get()})
            self.obs_status.set("OBS bağlantı ayarları kaydedildi.")
        except Exception as exc:self.obs_status.set("OBS ayar hatası: "+str(exc))

    def _obs_run(self,action):
        self._obs_save(); self.obs_status.set("OBS komutu gönderiliyor…")
        target=self.obs_scene.get().strip() if action=="scene" else ""
        def worker():
            try: result=obs_control(action,target)
            except Exception as exc: result="OBS hatası: "+str(exc)
            self.root.after(0,lambda:self.obs_status.set(result))
        threading.Thread(target=worker,daemon=True,name="rip-v6-obs").start()

    def _build_network(self,p):
        self._title(p,"NETWORK CENTER","Bridge ve Tailscale rotasını tek ekranda gör.").pack(fill="x",padx=14,pady=(18,8))
        c=self._card(p); c.pack(fill="both",expand=True,padx=14,pady=6)
        self.net_text=tk.Text(c,bg=CARD2,fg=TEXT,relief="flat",font=("Cascadia Mono",10),wrap="none"); self.net_text.pack(fill="both",expand=True,padx=16,pady=16)
        self._btn(p,"↻ AĞI YENİLE",self._refresh_network,MINT).pack(anchor="w",padx=14,pady=10)

    def _refresh_network(self):
        def worker():
            text=network_report()
            def done():
                if not self.win:return
                self.net_text.delete("1.0","end");self.net_text.insert("1.0",text)
            self.root.after(0,done)
        threading.Thread(target=worker,daemon=True).start()

    def _build_backup(self,p):
        self._title(p,"CONFIG BACKUP + CRASH RECOVERY","API anahtarlarını ve pairing tokenını kopyalamadan güvenli ayar yedeği oluştur.").pack(fill="x",padx=14,pady=(18,8))
        c=self._card(p); c.pack(fill="x",padx=14,pady=6)
        tk.Label(c,textvariable=self.backup_var,bg=CARD,fg=MINT,font=("Segoe UI",10,"bold"),wraplength=850,justify="left").pack(anchor="w",padx=16,pady=(16,10))
        self._btn(c,"GÜVENLİ YEDEK OLUŞTUR",self._backup,MINT).pack(anchor="w",padx=16,pady=(0,14))
        tk.Label(c,text="Veri klasörü: "+str(data_dir()),bg=CARD,fg=DIM,font=("Cascadia Mono",9),wraplength=850,justify="left").pack(anchor="w",padx=16,pady=(0,16))

    def _backup(self):
        def worker():
            try: out=safe_backup(); text="Yedek hazır: "+str(out)
            except Exception as exc: text="Yedek hatası: "+str(exc)
            self.root.after(0,lambda:self.backup_var.set(text))
        threading.Thread(target=worker,daemon=True).start()

    def _build_audit(self,p):
        self._title(p,"AUDIT LOG","V6 profilleri, otomasyonları, Doctor ve bakım işlemleri.").pack(fill="x",padx=14,pady=(18,8))
        self.audit_text=tk.Text(p,bg=CARD,fg=TEXT,relief="flat",font=("Cascadia Mono",9)); self.audit_text.pack(fill="both",expand=True,padx=14,pady=6)
        self._btn(p,"↻ KAYITLARI YENİLE",self._refresh_audit,CYAN).pack(anchor="w",padx=14,pady=10)

    def _refresh_audit(self):
        rows=read_audit(250); lines=[]
        for r in rows:
            stamp=time.strftime("%d.%m %H:%M:%S",time.localtime(int(r.get("ts",0) or 0)/1000)) if r.get("ts") else "—"
            lines.append(f"[{stamp}] {str(r.get('status','')).upper():5} {r.get('action','')} · {r.get('detail','')}")
        self.audit_text.delete("1.0","end");self.audit_text.insert("1.0","\n".join(lines[-250:]))

    def refresh(self):
        rows=list_automations(); self.auto_var.set(f"{len(rows)} otomasyon · engine aktif")
        if hasattr(self,"auto_text"):
            self.auto_text.delete("1.0","end")
            for r in rows:
                self.auto_text.insert("end",f"{'ON' if r.get('enabled',True) else 'OFF'}  {r.get('trigger')}={r.get('value')}  →  {r.get('action')} {r.get('action_value','')}\n")
        if hasattr(self,"audit_text"): self._refresh_audit()
        if hasattr(self,"net_text"): self._refresh_network()
        if hasattr(self,"macro_list"): self._refresh_macros()
