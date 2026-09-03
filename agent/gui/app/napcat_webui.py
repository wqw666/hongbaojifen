"""NapCat WebUI：扫码登录、登录状态查询。"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

import requests

from .napcat_paths import read_webui_token


@dataclass
class QQLoginStatus:
    is_login: bool
    is_offline: bool
    qrcode_url: str
    login_error: str
    raw: dict[str, Any]


class NapCatWebUI:
    def __init__(self, base_url: str, token: str, timeout: float = 12.0):
        self.base = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.session = requests.Session()
        self._credential: str | None = None

    @classmethod
    def from_napcat_dir(cls, base_url: str, napcat_dir) -> "NapCatWebUI":
        return cls(base_url, read_webui_token(napcat_dir))

    def _ensure_auth(self) -> dict[str, str]:
        if not self._credential:
            h = hashlib.sha256((self.token + ".napcat").encode()).hexdigest()
            r = self.session.post(
                f"{self.base}/api/auth/login",
                json={"hash": h},
                timeout=self.timeout,
            )
            r.raise_for_status()
            body = r.json()
            cred = (body.get("data") or {}).get("Credential") or body.get("Credential")
            if not cred:
                raise RuntimeError("WebUI 登录失败：未返回 Credential")
            self._credential = cred
        return {"Authorization": f"Bearer {self._credential}"}

    def _post(self, path: str, body: dict | None = None) -> dict[str, Any]:
        r = self.session.post(
            f"{self.base}{path}",
            json=body or {},
            headers=self._ensure_auth(),
            timeout=self.timeout,
        )
        r.raise_for_status()
        return r.json()

    def ping(self) -> bool:
        try:
            r = self.session.get(f"{self.base}/api/Base/GetNapCatVersion", timeout=3)
            return r.status_code == 200
        except Exception:
            return False

    def check_login_status(self) -> QQLoginStatus:
        data = self._post("/api/QQLogin/CheckLoginStatus").get("data") or {}
        return QQLoginStatus(
            is_login=bool(data.get("isLogin")),
            is_offline=bool(data.get("isOffline")),
            qrcode_url=str(data.get("qrcodeurl") or ""),
            login_error=str(data.get("loginError") or ""),
            raw=data,
        )

    def get_qrcode_url(self) -> str:
        body = self._post("/api/QQLogin/GetQQLoginQrcode")
        if body.get("code", 0) != 0:
            return ""
        return str((body.get("data") or {}).get("qrcode") or "")

    def refresh_qrcode(self) -> None:
        body = self._post("/api/QQLogin/RefreshQRcode")
        if body.get("code", 0) != 0:
            msg = str(body.get("message") or "刷新二维码失败")
            if "Logined" in msg:
                return
            raise RuntimeError(msg)

    def get_login_info(self) -> dict[str, Any]:
        return self._post("/api/QQLogin/GetQQLoginInfo").get("data") or {}

    def get_quick_login_list(self) -> list[str]:
        for path in ("/api/QQLogin/GetQuickLoginList", "/api/QQLogin/GetQuickLoginListNew"):
            try:
                r = self.session.get(
                    f"{self.base}{path}",
                    headers=self._ensure_auth(),
                    timeout=self.timeout,
                )
                if r.status_code != 200:
                    continue
                data = r.json().get("data")
                if isinstance(data, list):
                    return [str(x) for x in data]
            except Exception:
                continue
        return []

    def quick_login(self, uin: str) -> None:
        body = self._post("/api/QQLogin/SetQuickLogin", {"uin": uin})
        if body.get("code", 0) != 0:
            raise RuntimeError(str(body.get("message") or "快速登录失败"))

    def reload_plugin(self, plugin_id: str) -> None:
        """禁用再启用插件，使磁盘上的新代码/数据生效。"""
        self._post("/api/Plugin/SetStatus", {"id": plugin_id, "enable": False})
        self._post("/api/Plugin/SetStatus", {"id": plugin_id, "enable": True})
