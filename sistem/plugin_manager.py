from __future__ import annotations

import importlib.util
import json
import traceback
from pathlib import Path
from types import ModuleType
from typing import Any

from app_paths import data_path, resource_path


class PluginManager:
    """Small, fault-isolated plugin loader.

    Plugin layout:
        plugins/my_plugin/plugin.py

    Optional exports:
        PLUGIN_META = {"name": "...", "enabled": True}
        TOOL_DECLARATIONS = [{... Gemini function declaration ...}]
        def execute_tool(name: str, args: dict) -> str: ...
        def on_load(context: dict) -> None: ...

    A broken plugin is skipped and never prevents RIP from starting.
    """

    def __init__(self, plugin_dir: Path | None = None):
        self.plugin_dir = Path(plugin_dir or data_path("plugins"))
        self.plugin_dir.mkdir(parents=True, exist_ok=True)
        self.modules: list[ModuleType] = []
        self.tool_declarations: list[dict[str, Any]] = []
        self.tool_owners: dict[str, ModuleType] = {}
        self.errors: list[str] = []

    def load_all(self, context: dict | None = None) -> None:
        self.modules.clear()
        self.tool_declarations.clear()
        self.tool_owners.clear()
        self.errors.clear()

        for entry in sorted(self.plugin_dir.iterdir()):
            if entry.name.startswith("_"):
                continue
            file_path = entry / "plugin.py" if entry.is_dir() else entry
            if not file_path.is_file() or file_path.suffix.lower() != ".py":
                continue
            try:
                self._load_one(file_path, context or {})
            except Exception as exc:
                self.errors.append(f"{file_path.name}: {exc}")
                traceback.print_exc()

    def _load_one(self, file_path: Path, context: dict) -> None:
        module_name = "ripperinio_plugin_" + "_".join(file_path.parts[-2:]).replace(".", "_").replace("-", "_")
        spec = importlib.util.spec_from_file_location(module_name, str(file_path))
        if spec is None or spec.loader is None:
            raise RuntimeError("plugin spec oluşturulamadı")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        meta = getattr(module, "PLUGIN_META", {}) or {}
        if isinstance(meta, dict) and meta.get("enabled") is False:
            return

        declarations = getattr(module, "TOOL_DECLARATIONS", []) or []
        if not isinstance(declarations, list):
            raise TypeError("TOOL_DECLARATIONS liste olmalı")

        for decl in declarations:
            if not isinstance(decl, dict):
                continue
            name = str(decl.get("name") or "").strip()
            if not name or name in self.tool_owners:
                continue
            self.tool_declarations.append(decl)
            self.tool_owners[name] = module

        on_load = getattr(module, "on_load", None)
        if callable(on_load):
            try:
                on_load(dict(context))
            except Exception as exc:
                self.errors.append(f"{file_path.name} on_load: {exc}")

        self.modules.append(module)

    def can_execute(self, tool_name: str) -> bool:
        return str(tool_name or "") in self.tool_owners

    def execute(self, tool_name: str, args: dict | None = None) -> str:
        module = self.tool_owners.get(str(tool_name or ""))
        if module is None:
            return f"Plugin aracı bulunamadı: {tool_name}"
        fn = getattr(module, "execute_tool", None)
        if not callable(fn):
            return f"Plugin execute_tool tanımlamamış: {tool_name}"
        try:
            result = fn(str(tool_name), dict(args or {}))
            return str(result if result is not None else "Tamam.")
        except Exception as exc:
            traceback.print_exc()
            return f"Plugin hatası ({tool_name}): {exc}"


PLUGIN_README = """RIPPERINIO PLUGINS\n\nYeni plugin:\n  plugins/BenimPlugin/plugin.py\n\nplugin.py örneği:\n\nPLUGIN_META = {\"name\": \"Benim Plugin\", \"enabled\": True}\nTOOL_DECLARATIONS = [\n    {\n        \"name\": \"hello_plugin\",\n        \"description\": \"Test aracı\",\n        \"parameters\": {\"type\": \"OBJECT\", \"properties\": {}}\n    }\n]\n\ndef execute_tool(name, args):\n    return \"Plugin çalıştı.\"\n\nBir plugin hata verirse RIP açılmaya devam eder; hata yalnızca o plugin'e yazılır.\n"""


def ensure_plugin_readme() -> None:
    path = data_path("plugins", "README.txt")
    if not path.exists():
        try:
            path.write_text(PLUGIN_README, encoding="utf-8")
        except Exception:
            pass
