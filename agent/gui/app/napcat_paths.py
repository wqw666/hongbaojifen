"""定位本机 NapCat 目录与二维码缓存路径。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .config_manager import AppConfig


def app_roots() -> list[Path]:
    roots: list[Path] = []
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        roots.extend([exe_dir, exe_dir.parent])
    else:
        roots.append(Path(__file__).resolve().parents[2])
    return roots


def find_napcat_dir(cfg: AppConfig | None = None) -> Path | None:
    if cfg and cfg.napcat_dir:
        p = Path(cfg.napcat_dir)
        if (p / "napcat.mjs").exists():
            return p
    for root in app_roots():
        cand = root / "tools" / "NapCat"
        if (cand / "napcat.mjs").exists():
            return cand
        cand2 = root / "NapCat"
        if (cand2 / "napcat.mjs").exists():
            return cand2
    return None


def read_webui_token(napcat_dir: Path) -> str:
    cfg_path = napcat_dir / "config" / "webui.json"
    if not cfg_path.exists():
        return "c8e8f6cfa7b0"
    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
        return str(data.get("token") or "c8e8f6cfa7b0")
    except Exception:
        return "c8e8f6cfa7b0"


def plugins_root(napcat_dir: Path) -> Path:
    return napcat_dir / "plugins"


def plugin_data_dir(napcat_dir: Path, plugin_id: str = "napcat-plugin-cleaner") -> Path:
    return napcat_dir / "config" / "plugins" / plugin_id


def redpackets_store_path(napcat_dir: Path, plugin_id: str = "napcat-plugin-cleaner") -> Path:
    return plugin_data_dir(napcat_dir, plugin_id) / "redpackets.json"
