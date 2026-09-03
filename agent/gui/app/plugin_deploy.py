"""一键部署 NapCat 插件到客户本机。"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from .config_manager import AppConfig, DEFAULT_PLUGIN_ID, plugin_payload_dir


def deploy_plugin(cfg: AppConfig, dest: Path | None = None) -> tuple[bool, str]:
    # NapCat 4.18+ 白名单：必须用官方允许的插件目录名
    plugin_id = DEFAULT_PLUGIN_ID  # napcat-plugin-cleaner
    cfg.plugin_id = plugin_id

    src_dir = plugin_payload_dir()
    index = src_dir / "index.mjs"
    if not index.exists():
        return False, f"未找到插件 index.mjs\n路径: {index}\n请运行 build.bat 重新打包。"

    if dest is None:
        from .napcat_paths import find_napcat_dir, plugins_root

        napcat = find_napcat_dir(cfg)
        if napcat:
            target = plugins_root(napcat) / plugin_id
        elif cfg.napcat_plugins_dir:
            target = Path(cfg.napcat_plugins_dir) / plugin_id
        else:
            repo = Path(__file__).resolve().parents[2] / "tools" / "NapCat" / "plugins" / plugin_id
            target = repo
        # 运行中的 NapCat 常在 release\tools\NapCat；同步写一份避免热重载仍用旧包
        # （主 target 仍按上面逻辑；额外副本在 deploy 末尾处理）
    else:
        # 若传入的是 plugins 根目录，拼上白名单 ID
        dest = Path(dest)
        target = dest / plugin_id if dest.name != plugin_id else dest

    target.mkdir(parents=True, exist_ok=True)
    shutil.copy2(index, target / "index.mjs")

    pkg = {
        "name": plugin_id,
        "plugin": "HB Monitor",
        "version": "1.0.0",
        "type": "module",
        "main": "index.mjs",
        "description": "Group packet monitor helper",
        "author": "local",
    }
    (target / "package.json").write_text(json.dumps(pkg, ensure_ascii=False, indent=2), encoding="utf-8")

    # 写入启用配置
    plugins_json = target.parent / "plugins.json"
    try:
        data = {}
        if plugins_json.exists():
            data = json.loads(plugins_json.read_text(encoding="utf-8") or "{}")
        data[plugin_id] = True
        # 清理旧名，避免误部署
        data.pop("napcat-plugin-hongbao-monitor", None)
        plugins_json.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

    # 删掉旧目录（若存在）
    old = target.parent / "napcat-plugin-hongbao-monitor"
    if old.exists() and old != target:
        try:
            shutil.rmtree(old)
        except Exception:
            pass

    # 运行中的 NapCat 常在 release\tools\NapCat；同步一份避免热重载仍用旧包
    extra = ""
    try:
        repo_root = Path(__file__).resolve().parents[2]
        release_plugin = repo_root / "release" / "tools" / "NapCat" / "plugins" / plugin_id
        if release_plugin.parent.exists() and release_plugin.resolve() != target.resolve():
            release_plugin.mkdir(parents=True, exist_ok=True)
            shutil.copy2(index, release_plugin / "index.mjs")
            shutil.copy2(target / "package.json", release_plugin / "package.json")
            extra = f"\n\n已同步到运行目录:\n{release_plugin}"
    except Exception as e:
        extra = f"\n\n同步 release 插件失败: {e}"

    from .config_manager import save_config

    save_config(cfg)

    return (
        True,
        f"插件已部署到:\n{target}\n\n"
        f"插件 ID 已自动设为: {plugin_id}\n"
        f"请在软件「设置」确认后点「刷新连接」。\n"
        f"若仍未连接，请重启 NapCat 后再试。"
        f"{extra}",
    )
