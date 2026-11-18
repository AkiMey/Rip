from __future__ import annotations

import threading


class GlobalPTTHotkey:
    """Best-effort global F8 press/release hook on Windows.

    Uses the `keyboard` package when available. If a machine/security policy
    blocks global hooks, RIP keeps the normal Tk F8 binding instead.
    """
    def __init__(self, root, on_press, on_release):
        self.root = root
        self.on_press = on_press
        self.on_release = on_release
        self._keyboard = None
        self._hooks = []
        self.available = False
        self.error = ""

    def start(self):
        try:
            import keyboard
            self._keyboard = keyboard
            self._hooks.append(keyboard.on_press_key("f8", lambda e: self._dispatch(self.on_press)))
            self._hooks.append(keyboard.on_release_key("f8", lambda e: self._dispatch(self.on_release)))
            self.available = True
        except Exception as exc:
            self.error = str(exc)
            self.available = False
        return self.available

    def _dispatch(self, callback):
        try:
            self.root.after(0, callback)
        except Exception:
            pass

    def stop(self):
        kb = self._keyboard
        if kb is None:
            return
        for hook in self._hooks:
            try:
                kb.unhook(hook)
            except Exception:
                pass
        self._hooks.clear()
