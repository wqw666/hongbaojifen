"""调用 NapCat 红包监控插件 HTTP API。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import requests

from .config_manager import AppConfig


class ApiError(Exception):
    pass


@dataclass
class StatusInfo:
    connected: bool
    self_uin: str
    record_count: int
    auto_grab: bool
    auto_pull_detail: bool
    raw: dict[str, Any]


# NapCat 4.18+ 仅允许白名单插件 ID；业务插件实际以 cleaner 名义加载
FALLBACK_PLUGIN_IDS = ("napcat-plugin-cleaner",)


def _is_unreachable(err: Exception) -> bool:
    """NapCat/WebUI 未启动时，换 plugin_id 重试无意义。"""
    if isinstance(err, (requests.ConnectionError, requests.Timeout)):
        return True
    msg = str(err).lower()
    return "10061" in msg or "connection refused" in msg or "failed to establish" in msg


def _friendly_error(err: Exception) -> str:
    if _is_unreachable(err):
        return (
            "NapCat 未运行（6099 端口无法连接）。"
            "请先双击项目目录里的「启动.vbs」或「一键启动.bat」，"
            "等 QQ 登录后再点「刷新连接」。"
        )
    msg = str(err)
    low = msg.lower()
    if "503" in low or "service unavailable" in low:
        return "NapCat 已启动但 QQ/插件尚未就绪（503）。请等待扫码登录完成，或稍后再点「刷新连接」。"
    if "404" in low:
        return "插件未加载（404）。请点「一键部署插件」后重启 NapCat，再点「刷新连接」。"
    return msg


class PluginClient:
    def __init__(self, cfg: AppConfig, timeout: float = 45.0):
        self.cfg = cfg
        self.timeout = timeout
        self.session = requests.Session()
        self._resolved_plugin_id = cfg.plugin_id or "napcat-plugin-cleaner"

    def _candidate_ids(self) -> list[str]:
        ids: list[str] = []
        preferred = self.cfg.plugin_id or "napcat-plugin-cleaner"
        if preferred == "napcat-plugin-hongbao-monitor":
            preferred = "napcat-plugin-cleaner"
        for x in (preferred, *FALLBACK_PLUGIN_IDS):
            if x and x not in ids:
                ids.append(x)
        return ids

    def _url(self, path: str, plugin_id: str | None = None) -> str:
        pid = plugin_id or self._resolved_plugin_id
        base = self.cfg.napcat_base.rstrip("/")
        p = path if path.startswith("/") else f"/{path}"
        return f"{base}/plugin/{pid}/api{p}"

    def _get(self, path: str, params: dict | None = None) -> dict[str, Any]:
        last_err: Exception | None = None
        for pid in self._candidate_ids():
            try:
                r = self.session.get(self._url(path, pid), params=params, timeout=self.timeout)
                if r.status_code == 404:
                    last_err = requests.HTTPError(f"404 for {pid}")
                    continue
                r.raise_for_status()
                data = r.json()
                if data.get("code", 0) != 0:
                    raise ApiError(data.get("message") or "请求失败")
                self._resolved_plugin_id = pid
                if self.cfg.plugin_id != pid:
                    self.cfg.plugin_id = pid
                    from .config_manager import save_config

                    save_config(self.cfg)
                return data
            except ApiError:
                raise
            except Exception as e:
                last_err = e
                if _is_unreachable(e):
                    break
        raise ApiError(_friendly_error(last_err)) if last_err else ApiError("插件未连接")

    def _post(self, path: str, body: dict | None = None) -> dict[str, Any]:
        last_err: Exception | None = None
        for pid in self._candidate_ids():
            try:
                r = self.session.post(self._url(path, pid), json=body or {}, timeout=self.timeout)
                if r.status_code == 404:
                    last_err = requests.HTTPError(f"404 for {pid}")
                    continue
                r.raise_for_status()
                data = r.json()
                if data.get("code", 0) != 0:
                    raise ApiError(data.get("message") or "请求失败")
                self._resolved_plugin_id = pid
                if self.cfg.plugin_id != pid:
                    self.cfg.plugin_id = pid
                    from .config_manager import save_config

                    save_config(self.cfg)
                return data
            except ApiError:
                raise
            except Exception as e:
                last_err = e
                if _is_unreachable(e):
                    break
        raise ApiError(_friendly_error(last_err)) if last_err else ApiError("插件未连接")

    def get_status(self) -> StatusInfo:
        try:
            data = self._get("/status")
            d = data.get("data") or {}
            return StatusInfo(
                connected=True,
                self_uin=str(d.get("selfUin") or ""),
                record_count=int(d.get("recordCount") or 0),
                auto_grab=bool(d.get("autoGrab")),
                auto_pull_detail=bool(d.get("autoPullDetail")),
                raw=d,
            )
        except Exception as e:
            return StatusInfo(
                connected=False,
                self_uin="",
                record_count=0,
                auto_grab=False,
                auto_pull_detail=False,
                raw={"error": str(e)},
            )

    def sync_plugin_config(self) -> None:
        groups = self.cfg.watch_group_list()
        self._post(
            "/config",
            {
                "enabled": True,
                "autoGrab": self.cfg.auto_grab,
                "grabSelf": self.cfg.grab_self,
                "autoPullDetail": self.cfg.auto_pull_detail,
                "watchGroups": groups,
                "delayMin": self.cfg.delay_min_ms,
                "delayMax": self.cfg.delay_max_ms,
                "handlePassword": self.cfg.handle_password,
            },
        )

    def query(
        self,
        group_id: str,
        start: str,
        end: str,
        refresh: bool = True,
    ) -> dict[str, Any]:
        return self._get(
            "/query",
            {
                "group_id": group_id,
                "start": start,
                "end": end,
                "refresh": "true" if refresh else "false",
            },
        ).get("data") or {}

    def list_records(self, group_id: str | None = None) -> list[dict[str, Any]]:
        params = {"group_id": group_id} if group_id else None
        data = self._get("/list", params=params)
        return list(data.get("data") or [])

    def pull_detail(self, bill_no: str) -> dict[str, Any]:
        data = self._post("/detail", {"billNo": bill_no})
        return data.get("data") or {}

    def clear_records(self) -> int:
        count = 0
        try:
            count = len(self.list_records())
        except Exception:
            pass

        if self._try_api_clear():
            return count

        self._clear_records_fallback(count)
        return count

    def _try_api_clear(self) -> bool:
        for pid in self._candidate_ids():
            try:
                r = self.session.post(self._url("/clear", pid), json={}, timeout=self.timeout)
                if r.status_code == 404:
                    continue
                r.raise_for_status()
                data = r.json()
                if data.get("code", 0) != 0:
                    raise ApiError(data.get("message") or "清空失败")
                self._resolved_plugin_id = pid
                return True
            except ApiError:
                raise
            except requests.HTTPError as e:
                if e.response is not None and e.response.status_code == 404:
                    continue
                raise ApiError(str(e)) from e
            except Exception:
                continue
        return False

    def _clear_records_fallback(self, count: int) -> None:
        from .napcat_paths import find_napcat_dir, redpackets_store_path
        from .napcat_webui import NapCatWebUI
        from .plugin_deploy import deploy_plugin

        napcat = find_napcat_dir(self.cfg)
        if not napcat:
            raise ApiError("无法定位 NapCat 目录。请先启动 NapCat，或检查 tools\\NapCat 是否存在。")

        deploy_plugin(self.cfg)

        store = redpackets_store_path(napcat, self._resolved_plugin_id)
        store.parent.mkdir(parents=True, exist_ok=True)
        store.write_text("[]", encoding="utf-8")

        webui = NapCatWebUI.from_napcat_dir(self.cfg.napcat_base, napcat)
        if webui.ping():
            pid = self._resolved_plugin_id or "napcat-plugin-cleaner"
            try:
                webui.reload_plugin(pid)
            except Exception as e:
                raise ApiError(
                    f"数据文件已清空，但插件重载失败：{e}\n"
                    "请在 NapCat WebUI 中禁用再启用「napcat-plugin-cleaner」插件，"
                    "或重启 NapCat。"
                ) from e
        elif count > 0:
            raise ApiError(
                "插件不支持在线清空（需更新插件）。"
                "已尝试写入空数据文件，请重启 NapCat 后生效。"
            )

    def get_group_members(self, group_id: str, no_cache: bool = True) -> dict[str, Any]:
        try:
            data = self._get(
                "/members",
                {"group_id": group_id, "no_cache": "true" if no_cache else "false"},
            )
            payload = data.get("data") or {}
            if payload.get("members") is not None:
                return payload
        except Exception:
            pass
        return self._onebot_group_members(group_id, no_cache)

    def _onebot_action(self, action: str, params: dict[str, Any]) -> Any:
        base = (self.cfg.onebot_http_base or "http://127.0.0.1:3001").rstrip("/")
        r = self.session.post(f"{base}/{action}", json=params, timeout=self.timeout)
        r.raise_for_status()
        data = r.json()
        if int(data.get("retcode", 0)) != 0:
            raise ApiError(str(data.get("message") or data.get("wording") or f"{action} 失败"))
        return data.get("data")

    def _onebot_group_members(self, group_id: str, no_cache: bool) -> dict[str, Any]:
        params: dict[str, Any] = {"group_id": group_id, "no_cache": no_cache}
        info: dict[str, Any] = {}
        try:
            raw_info = self._onebot_action("get_group_info", params)
            if isinstance(raw_info, dict):
                info = raw_info
        except Exception:
            pass

        members_raw = self._onebot_action("get_group_member_list", params)
        members: list[dict[str, Any]] = []
        if isinstance(members_raw, list):
            for m in members_raw:
                role = str(m.get("role") or "member").lower()
                uin = str(m.get("user_id") or "")
                nick = str(m.get("nickname") or "")
                card = str(m.get("card") or "")
                members.append(
                    {
                        "uin": uin,
                        "nickname": nick,
                        "card": card,
                        "display_name": card or nick or uin,
                        "role": role,
                        "role_text": {"owner": "群主", "admin": "管理"}.get(role, "成员"),
                    }
                )
        members.sort(
            key=lambda x: (
                0 if x["role"] == "owner" else 1 if x["role"] == "admin" else 2,
                x["uin"],
            )
        )

        gname = str(info.get("group_name") or info.get("groupName") or "")
        mcount = int(info.get("member_count") or info.get("memberNum") or len(members) or 0)
        max_count = int(info.get("max_member_count") or info.get("maxMemberNum") or 0)

        warning = ""
        if not members:
            if gname:
                warning = "成员列表为空：当前 QQ 可能不在该群内，或暂无查看权限"
            else:
                warning = "未找到该群，请确认群号是否正确"

        return {
            "group_id": group_id,
            "group_name": gname,
            "member_count": mcount,
            "max_member_count": max_count,
            "members": members,
            "warning": warning,
        }
