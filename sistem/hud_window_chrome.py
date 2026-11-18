from __future__ import annotations

import tkinter as tk

BG = "#02090d"
HEADER = "#020b10"
PANEL = "#06141b"
PANEL_2 = "#071820"
LINE = "#14323d"
LINE_BRIGHT = "#0f6473"
CYAN = "#00d9f5"
CYAN_2 = "#00b9d1"
TEXT = "#f3fbfd"
TEXT_2 = "#b8c8cd"
DIM = "#73878e"
GREEN = "#57e59a"
RED = "#ff566c"


class HudWindowShell:
    """Borderless RIPPERINIO chrome for legacy Tk Toplevel windows.

    Keeps the original page widgets untouched, but replaces the stock Windows
    title bar and gives secondary tools the same dark/cyan visual language as
    the main reference HUD.
    """

    def __init__(
        self,
        win: tk.Toplevel,
        *,
        title: str,
        subtitle: str = "RIPPERINIO // LOCAL MODULE",
        on_close=None,
        width: int = 1080,
        height: int = 740,
        min_width: int = 900,
        min_height: int = 620,
    ):
        self.win = win
        self.on_close = on_close or win.destroy
        self._drag = None
        self._resize = None
        self._normal_geometry = ""
        self._maximized = False

        sw = max(1024, int(win.winfo_screenwidth() or 1920))
        sh = max(700, int(win.winfo_screenheight() or 1080))
        width = min(width, sw - 40)
        height = min(height, sh - 80)
        x = max(0, (sw - width) // 2)
        y = max(0, (sh - height) // 2)

        win.configure(bg=BG, bd=0, highlightthickness=0)
        win.geometry(f"{width}x{height}+{x}+{y}")
        win.minsize(min_width, min_height)
        win.overrideredirect(True)
        try:
            win.attributes("-topmost", False)
        except Exception:
            pass

        self.outer = tk.Frame(
            win,
            bg=BG,
            bd=0,
            highlightbackground=LINE_BRIGHT,
            highlightcolor=CYAN,
            highlightthickness=1,
        )
        self.outer.pack(fill="both", expand=True)

        # Thin cyan highlight across the top, matching the main HUD.
        tk.Frame(self.outer, bg=CYAN, height=2).pack(fill="x", side="top")

        self.titlebar = tk.Frame(self.outer, bg=HEADER, height=54, bd=0)
        self.titlebar.pack(fill="x")
        self.titlebar.pack_propagate(False)

        brand = tk.Frame(self.titlebar, bg=HEADER)
        brand.pack(side="left", padx=(18, 12), fill="y")
        tk.Label(
            brand,
            text="RIPPERINIO",
            bg=HEADER,
            fg=CYAN,
            font=("Segoe UI", 12, "bold"),
        ).pack(anchor="w", pady=(9, 0))
        tk.Label(
            brand,
            text="V6 LEGACY // LINKED CORE",
            bg=HEADER,
            fg=CYAN_2,
            font=("Segoe UI", 6, "bold"),
        ).pack(anchor="w")

        tk.Frame(self.titlebar, bg=LINE, width=1).pack(side="left", fill="y", pady=10)

        titles = tk.Frame(self.titlebar, bg=HEADER)
        titles.pack(side="left", padx=14, fill="both", expand=True)
        self.title_label = tk.Label(
            titles,
            text=title,
            bg=HEADER,
            fg=TEXT,
            font=("Segoe UI", 12, "bold"),
        )
        self.title_label.pack(anchor="w", pady=(8, 0))
        self.subtitle_label = tk.Label(
            titles,
            text=subtitle,
            bg=HEADER,
            fg=DIM,
            font=("Segoe UI", 7),
        )
        self.subtitle_label.pack(anchor="w", pady=(1, 0))

        self.header_right = tk.Frame(self.titlebar, bg=HEADER)
        self.header_right.pack(side="right", fill="y")

        self._window_button("−", self._minimize, hover=CYAN_2)
        self._window_button("□", self._toggle_maximize, hover=CYAN_2)
        self._window_button("×", self._close, hover=RED)

        tk.Frame(self.outer, bg=LINE, height=1).pack(fill="x")

        self.content = tk.Frame(self.outer, bg=BG, bd=0)
        self.content.pack(fill="both", expand=True)

        self.footer = tk.Frame(self.outer, bg=HEADER, height=24)
        self.footer.pack(fill="x", side="bottom")
        self.footer.pack_propagate(False)
        tk.Label(
            self.footer,
            text="R I P P E R I N I O   //   L O C A L   C O N T R O L   S U R F A C E",
            bg=HEADER,
            fg="#496068",
            font=("Segoe UI", 5),
        ).pack(side="left", padx=14, pady=6)
        tk.Label(
            self.footer,
            text="● READY",
            bg=HEADER,
            fg=GREEN,
            font=("Segoe UI", 6, "bold"),
        ).pack(side="right", padx=(8, 24), pady=5)

        self._resize_grip = tk.Canvas(
            self.footer,
            width=18,
            height=18,
            bg=HEADER,
            highlightthickness=0,
            bd=0,
        )
        self._resize_grip.pack(side="right", padx=(0, 4), pady=2)
        # Tk cursor names are platform-dependent.  The old X11 cursor
        # "size_nw_se" crashes some Windows/Tk builds before the window body
        # can be constructed, leaving a blank secondary window.
        for _cursor in ("sizing", "bottom_right_corner", "arrow"):
            try:
                self._resize_grip.configure(cursor=_cursor)
                break
            except tk.TclError:
                continue
        for i in (5, 9, 13):
            self._resize_grip.create_line(i, 16, 16, i, fill=LINE_BRIGHT, width=1)

        for widget in (self.titlebar, brand, titles, self.title_label, self.subtitle_label):
            widget.bind("<ButtonPress-1>", self._drag_start, add="+")
            widget.bind("<B1-Motion>", self._drag_move, add="+")
            widget.bind("<Double-Button-1>", lambda _e: self._toggle_maximize(), add="+")

        self._resize_grip.bind("<ButtonPress-1>", self._resize_start)
        self._resize_grip.bind("<B1-Motion>", self._resize_move)

        # Escape never closes the app by accident; it only returns focus.
        win.bind("<Escape>", lambda _e: self.content.focus_set(), add="+")

    def _window_button(self, text, command, *, hover):
        label = tk.Label(
            self.header_right,
            text=text,
            bg=HEADER,
            fg=TEXT_2,
            width=4,
            font=("Segoe UI", 11, "bold"),
            cursor="hand2",
        )
        label.pack(side="left", fill="y")
        label.bind("<Button-1>", lambda _e: command())
        label.bind("<Enter>", lambda _e, w=label: w.configure(bg=PANEL_2, fg=hover))
        label.bind("<Leave>", lambda _e, w=label: w.configure(bg=HEADER, fg=TEXT_2))
        return label

    def _drag_start(self, event):
        if self._maximized:
            return
        self._drag = (event.x_root - self.win.winfo_x(), event.y_root - self.win.winfo_y())

    def _drag_move(self, event):
        if self._maximized or not self._drag:
            return
        dx, dy = self._drag
        x = event.x_root - dx
        y = event.y_root - dy
        self.win.geometry(f"+{x}+{y}")

    def _resize_start(self, event):
        if self._maximized:
            return
        self._resize = (
            event.x_root,
            event.y_root,
            self.win.winfo_width(),
            self.win.winfo_height(),
        )

    def _resize_move(self, event):
        if self._maximized or not self._resize:
            return
        sx, sy, sw, sh = self._resize
        nw = max(self.win.winfo_reqwidth(), sw + (event.x_root - sx))
        nh = max(self.win.winfo_reqheight(), sh + (event.y_root - sy))
        nw = max(780, int(nw))
        nh = max(540, int(nh))
        self.win.geometry(f"{nw}x{nh}")

    def _minimize(self):
        # overrideredirect windows need a brief native transition to iconify.
        try:
            self.win.overrideredirect(False)
            self.win.iconify()
            self.win.after(120, lambda: self.win.overrideredirect(True))
        except Exception:
            pass

    def _toggle_maximize(self):
        try:
            if not self._maximized:
                self._normal_geometry = self.win.geometry()
                sw = int(self.win.winfo_screenwidth() or 1920)
                sh = int(self.win.winfo_screenheight() or 1080)
                self.win.geometry(f"{sw}x{max(620, sh - 40)}+0+0")
                self._maximized = True
            else:
                if self._normal_geometry:
                    self.win.geometry(self._normal_geometry)
                self._maximized = False
        except Exception:
            pass

    def _close(self):
        try:
            self.on_close()
        except Exception:
            try:
                self.win.destroy()
            except Exception:
                pass


def hud_button(parent, text, command, *, color=CYAN, width=None, compact=False):
    """Consistent button used by secondary control surfaces."""
    btn = tk.Button(
        parent,
        text=text,
        command=command,
        bg=PANEL_2,
        fg=color,
        activebackground="#0a2630",
        activeforeground=TEXT,
        relief="flat",
        bd=0,
        highlightthickness=1,
        highlightbackground=LINE,
        highlightcolor=CYAN,
        padx=10 if compact else 14,
        pady=6 if compact else 9,
        cursor="hand2",
        font=("Segoe UI", 8 if compact else 9, "bold"),
    )
    if width:
        btn.configure(width=width)
    return btn
