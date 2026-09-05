"""应用配置：保存在用户目录，打包成 exe 后仍可用。"""

from __future__ import annotations

import json
import os
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


APP_NAME = "QQHongbaoMonitor"
DEFAULT_PLUGIN_ID = "napcat-plugin-cleaner"


@dataclass
class AppConfig:
    napcat_base: str = "http://127.0.0.1:6099"
    plugin_id: str = DEFAULT_PLUGIN_ID
    napcat_dir: str = ""  # NapCat 根目录，留空自动探测
    watch_groups: str = ""  # 逗号分隔群号
    napcat_plugins_dir: str = ""
    auto_grab: bool = True
    grab_self: bool = True  # 拼手气：允许领自己发的包
    auto_pull_detail: bool = True
    delay_min_ms: int = 800
    delay_max_ms: int = 2000
    handle_password: bool = True
    poll_interval_sec: float = 2.0
    first_run_done: bool = False
    onebot_http_base: str = "http://127.0.0.1:3001"

    # ---- 总后台对接（执行器功能 v1）----
    backend_base: str = "http://localhost:8892"  # 总后台地址
    backend_api_key: str = "hbjf-open-2026"      # X-Api-Key 对接密钥
    executor_token: str = ""                     # 执行器 token（总后台新增执行器生成）
    executor_name: str = ""                      # 执行器名称（仅展示）
    heartbeat_interval_sec: int = 10             # 心跳/命令轮询间隔（秒，默认 10，最小 5）
    member_group_id: str = ""                    # 会员群号（成员自动注册为该群会员）
    auto_sync_members: bool = False              # 周期任务里自动同步会员（≥5 分钟一次）
    hidden_groups: str = ""                      # 群管理里已删除的群号（逗号分隔，刷新列表不再显示）

    # ---- 游戏玩法（v2：多游戏群 + 结算上报）----
    play_rule_id: int = 0                        # 启用中的玩法 id（0=未启用）
    play_rule_name: str = ""                     # 玩法名@版本（仅展示）
    play_group_id: str = ""                      # 游戏群号（逗号分隔多个）
    play_enabled: bool = False                   # 玩法运行开关
    play_callback_port: int = 6101               # agent 本地玩法回调端口（插件转发目标）
    game_round_seq: int = 0                      # 游戏局号计数器（开始本局递增，生成 hongbaojifen_XXXXXXXX）
    game_fee_rate: int = 20                      # 游戏费率（千分比，20=2%）：修改即上报总后台执行器
    play_auto_register_members: bool = False     # 启用玩法后自动把游戏群成员注册为会员（幂等）

    def plays_dir(self) -> Path:
        """玩法规则文件本地缓存目录（%APPDATA%/QQHongbaoMonitor/plays）。"""
        d = config_dir() / "plays"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def watch_group_list(self) -> list[str]:
        return [x.strip() for x in self.watch_groups.replace("，", ",").split(",") if x.strip()]

    def play_group_list(self) -> list[str]:
        """游戏群号列表（play_group_id 逗号分隔，容忍中文逗号）。"""
        return [x.strip() for x in self.play_group_id.replace("，", ",").split(",") if x.strip()]

    def plugin_api(self, path: str) -> str:
        base = self.napcat_base.rstrip("/")
        p = path if path.startswith("/") else f"/{path}"
        return f"{base}/plugin/{self.plugin_id}/api{p}"


def config_dir() -> Path:
    root = os.environ.get("APPDATA") or os.path.expanduser("~")
    d = Path(root) / APP_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def config_path() -> Path:
    return config_dir() / "config.json"


def load_config() -> AppConfig:
    p = config_path()
    if not p.exists():
        return AppConfig()
    try:
        raw: dict[str, Any] = json.loads(p.read_text(encoding="utf-8"))
        cfg = AppConfig()
        for k, v in raw.items():
            if hasattr(cfg, k):
                setattr(cfg, k, v)
        if cfg.plugin_id == "napcat-plugin-hongbao-monitor":
            cfg.plugin_id = DEFAULT_PLUGIN_ID
        return cfg
    except Exception:
        return AppConfig()


def save_config(cfg: AppConfig) -> None:
    data = asdict(cfg)
    config_path().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def plugin_payload_dir() -> Path:
    """打包 exe 内嵌插件资源目录；开发模式用 plugin/dist 或 plugin_bundle。"""
    import sys

    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "plugin_bundle"
    root = Path(__file__).resolve().parents[2]
    bundle = root / "plugin_bundle"
    if (bundle / "index.mjs").exists():
        return bundle
    return root / "plugin" / "dist"


def find_napcat_launcher() -> Path | None:
    """查找 启动.vbs / 一键启动.bat（exe 在 dist 时向上找项目根）。"""
    import sys

    roots: list[Path] = []
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        roots.extend([exe_dir, exe_dir.parent])
    else:
        roots.append(Path(__file__).resolve().parents[2])
    for root in roots:
        for name in ("启动.vbs", "一键启动.bat", "start_silent.bat"):
            p = root / name
            if p.is_file():
                return p
    return None


def default_napcat_plugin_dest(cfg: AppConfig) -> Path:
    from .napcat_paths import find_napcat_dir, plugins_root

    napcat = find_napcat_dir(cfg)
    if napcat:
        return plugins_root(napcat) / cfg.plugin_id
    if cfg.napcat_plugins_dir:
        return Path(cfg.napcat_plugins_dir) / cfg.plugin_id
    home = Path.home()
    candidates = [
        home / "NapCat" / "plugins" / cfg.plugin_id,
        home / "Documents" / "NapCat" / "plugins" / cfg.plugin_id,
        Path("C:/NapCat/plugins") / cfg.plugin_id,
    ]
    for c in candidates:
        if c.parent.parent.exists() or c.parent.exists():
            return c
    return candidates[0]
