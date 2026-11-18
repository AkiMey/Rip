from __future__ import annotations

import asyncio
import json
import time


def safe_text(value, limit=300):
    return " ".join(str(value or "").replace("\x00", " ").split())[:limit]


async def read_notifications():
    from winrt.windows.ui.notifications.management import UserNotificationListener
    from winrt.windows.ui.notifications import NotificationKinds

    listener = getattr(UserNotificationListener, "current", None)
    if callable(listener):
        listener = listener()
    if listener is None:
        listener = getattr(UserNotificationListener, "get_current", lambda: None)()
    if listener is None:
        return []

    try:
        req = getattr(listener, "request_access_async", None)
        if callable(req):
            await req()
    except Exception:
        pass

    rows = await listener.get_notifications_async(NotificationKinds.TOAST)
    out = []
    now = int(time.time() * 1000)
    for item in list(rows or [])[-80:]:
        app = "Windows"
        try:
            app = str(item.app_info.display_info.display_name or app)
        except Exception:
            pass
        texts = []
        try:
            visual = item.notification.visual
            for binding in list(visual.bindings or []):
                try:
                    for element in list(binding.get_text_elements() or []):
                        text = safe_text(getattr(element, "text", ""))
                        if text:
                            texts.append(text)
                except Exception:
                    continue
        except Exception:
            pass
        title = texts[0] if texts else app
        body = " — ".join(texts[1:3]) if len(texts) > 1 else ""
        out.append({
            "id": f"win:{getattr(item, 'id', len(out))}",
            "kind": "notification",
            "app": safe_text(app, 120),
            "title": safe_text(title, 200),
            "text": safe_text(body, 400),
            "received_at": now,
            "source": "windows",
        })
    return out


def main():
    try:
        rows = asyncio.run(read_notifications())
        print(json.dumps({"ok": True, "rows": rows}, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
