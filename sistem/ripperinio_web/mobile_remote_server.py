from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import traceback
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent
SYSTEM_DIR = WEB_DIR.parent
if str(SYSTEM_DIR) not in sys.path:
    sys.path.insert(0, str(SYSTEM_DIR))

from bridge_shared import token, data_file
from version import PROTOCOL_VERSION, VERSION
from core.logging_utils import append_log, configure_utf8_stdio

LOG_FILE = data_file("mobile_remote_server.log")
PORT = 8770


def _force_utf8_stdio() -> None:
    configure_utf8_stdio()


def log(text: str) -> None:
    append_log(LOG_FILE, text)


def short_error(exc: BaseException, limit: int = 260) -> str:
    value = f"{type(exc).__name__}: {exc}".replace("\r", " ").replace("\n", " ").strip()
    if len(value) > limit:
        value = value[: limit - 1] + "…"
    return value or type(exc).__name__


def main() -> int:
    _force_utf8_stdio()
    if sys.platform == "win32" and hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
        try:
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        except Exception:
            pass
    access_token = token()
    if not access_token:
        log("[FATAL] Persistent Bridge token bulunamadi.")
        return 2

    # server.py reads the token when imported.
    os.environ["RIPPERINIO_WEB_TOKEN"] = access_token
    os.environ.pop("RIPPERINIO_PUBLIC", None)

    try:
        import uvicorn
        from fastapi import Request, HTTPException
        from fastapi.websockets import WebSocketDisconnect
        import ripperinio_web.server as core
    except Exception:
        log("[IMPORT FATAL]\n" + traceback.format_exc())
        return 3

    class MobileLiveBridge(core.LiveBridge):
        """RIP Mobile için kopmayan Live bridge.

        Eski server.py Gemini Live bağlantısı hata verdiğinde exception'ı tekrar
        yükseltiyordu. FastAPI endpoint kapanıyor, istemciye WebSocket close-frame
        gitmeden TCP kesilebiliyor ve Android OkHttp bunu EOFException olarak
        raporluyordu.

        Burada telefon WebSocket'i açık tutulur. Gemini oturumu kendi içinde
        yeniden kurulur. Böylece PC'nin online/offline durumu ile Gemini session
        hatası birbirine karışmaz.
        """

        async def _mobile_send(self, payload: dict) -> bool:
            try:
                await self.ws.send_text(json.dumps(payload, ensure_ascii=False))
                return True
            except Exception:
                return False

        async def _run_session_tasks(self) -> str:
            from_browser = asyncio.create_task(
                self._from_browser(), name="rip-mobile-from-phone"
            )
            from_gemini = asyncio.create_task(
                self._from_gemini(), name="rip-mobile-from-gemini"
            )
            tasks = {from_browser, from_gemini}

            try:
                done, pending = await asyncio.wait(
                    tasks, return_when=asyncio.FIRST_COMPLETED
                )

                client_disconnected = False
                session_error = None

                for task in done:
                    try:
                        task.result()
                    except (WebSocketDisconnect, asyncio.CancelledError):
                        client_disconnected = True
                    except Exception as exc:
                        session_error = exc

                for task in pending:
                    task.cancel()
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)

                if client_disconnected:
                    return "client_disconnected"
                if session_error is not None:
                    raise session_error
                return "ended"
            finally:
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

        async def run(self):
            if core.PUBLIC_MODE:
                # Mobile Remote is private/Tailscale only in our setup.
                return await super().run()

            attempt = 0
            last_visible_error = ""

            while True:
                api_key = str(core.get_api_key() or "").strip()

                if not api_key:
                    msg = (
                        "PC bağlı fakat Gemini API anahtarı bulunamadı. "
                        "Masaüstü RIP'te API anahtarını kaydet."
                    )
                    if msg != last_visible_error:
                        if not await self._mobile_send({
                            "type": "error",
                            "code": "gemini_api_key_missing",
                            "text": msg,
                        }):
                            return
                        last_visible_error = msg

                    await self._mobile_send({
                        "type": "remote_status",
                        "state": "waiting_api_key",
                        "text": "PC bağlı · Gemini anahtarı bekleniyor",
                    })
                    await asyncio.sleep(5.0)
                    continue

                try:
                    await self._mobile_send({
                        "type": "remote_status",
                        "state": "gemini_connecting",
                        "text": "PC bağlı · RIP oturumu hazırlanıyor",
                    })

                    client = core.genai.Client(
                        api_key=api_key,
                        http_options={"api_version": "v1alpha"},
                    )

                    async with client.aio.live.connect(
                        model=core.LIVE_MODEL,
                        config=self._build_config(),
                    ) as session:
                        self.session = session
                        attempt = 0
                        last_visible_error = ""

                        log("[LIVE] Gemini session baglandi.")
                        if not await self._mobile_send({"type": "ready"}):
                            return
                        if not await self._mobile_send({
                            "type": "agent_status",
                            "connected": core.agent_hub.connected,
                        }):
                            return

                        result = await self._run_session_tasks()
                        if result == "client_disconnected":
                            log("[CLIENT] Telefon WebSocket ayrildi.")
                            return

                        # A normal-looking session end still gets rebuilt while the
                        # phone stays connected.
                        raise RuntimeError("Gemini Live session beklenmedik sekilde sonlandi")

                except (WebSocketDisconnect, asyncio.CancelledError):
                    return
                except Exception as exc:
                    attempt += 1
                    detail = short_error(exc)
                    log(f"[GEMINI ERROR #{attempt}] {detail}")
                    log(traceback.format_exc())

                    # Keep API/auth errors understandable; otherwise include the
                    # exception class so diagnostics are useful instead of EOF.
                    low = detail.casefold()
                    if any(x in low for x in (
                        "api key", "apikey", "permission", "unauth", "forbidden",
                        "invalid argument", "authentication",
                    )):
                        visible = (
                            "Gemini/API bağlantısı reddedildi · "
                            + detail
                        )
                    else:
                        visible = "RIP oturum bağlantısı düştü · " + detail

                    if visible != last_visible_error:
                        if not await self._mobile_send({
                            "type": "error",
                            "code": "gemini_session_error",
                            "text": visible,
                        }):
                            return
                        last_visible_error = visible

                    delay = min(12.0, 2.0 + attempt * 1.5)
                    if not await self._mobile_send({
                        "type": "remote_status",
                        "state": "retrying",
                        "retry_in": delay,
                        "text": f"PC bağlı · RIP {delay:.0f} sn sonra yeniden deneyecek",
                    }):
                        return
                    await asyncio.sleep(delay)

    # Existing FastAPI websocket route resolves core.LiveBridge at request time.
    core.LiveBridge = MobileLiveBridge

    # Small authenticated diagnostic endpoint for future troubleshooting.
    @core.app.get("/api/mobile-diagnostic")
    async def mobile_diagnostic(request: Request):
        supplied = str(
            request.headers.get("X-RIP-Token")
            or request.query_params.get("t")
            or ""
        )
        if supplied != access_token:
            raise HTTPException(status_code=403, detail="Unauthorized")
        return {
            "ok": True,
            "server": "RIP Mobile Remote",
            "version": VERSION,
            "protocol_version": PROTOCOL_VERSION,
            "api_key_present": bool(str(core.get_api_key() or "").strip()),
            "model": core.LIVE_MODEL,
            "agent_connected": bool(core.agent_hub.connected),
        }

    log(f"[START] Mobile Remote V{VERSION} 0.0.0.0:{PORT}")
    try:
        uvicorn.run(
            core.app,
            host="0.0.0.0",
            port=PORT,
            log_level="warning",
            access_log=False,
        )
    except Exception:
        log("[SERVER FATAL]\n" + traceback.format_exc())
        return 4

    log("[STOP] Mobile Remote server exited.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
