"""NapCat WebUI：扫码登录、登录状态查询。"""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass
from typing import Any

import requests

from .napcat_paths import read_webui_token

# WebUI 登录有频率限制（webui.json loginRate，默认 10 次/60 秒/IP），
# 且 Credential 是服务端签发的全局票据、不绑定请求方。GUI 各线程/各处会新建
# 多个 WebUI 实例，若按实例各自 auth，瞬时并发就会打满限额并把自己锁死。
# 故 credential 用进程级缓存：全进程同一服务只 auth 一次，其余实例共享；
# 失效（QQ 重启后旧票据 401）时清缓存重试一次。
_CRED_CACHE: dict[str, str] = {}
_CRED_LOCK = threading.Lock()


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

    @classmethod
    def from_napcat_dir(cls, base_url: str, napcat_dir) -> "NapCatWebUI":
        return cls(base_url, read_webui_token(napcat_dir))

    @staticmethod
    def _clear_cred(base_url: str) -> None:
        _CRED_CACHE.pop(base_url, None)

    def _ensure_auth(self) -> dict[str, str]:
        cred = _CRED_CACHE.get(self.base)
        if cred:
            return {"Authorization": f"Bearer {cred}"}
        with _CRED_LOCK:
            cred = _CRED_CACHE.get(self.base)
            if not cred:
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
                _CRED_CACHE[self.base] = cred
        return {"Authorization": f"Bearer {cred}"}

    def _post(self, path: str, body: dict | None = None) -> dict[str, Any]:
        headers = self._ensure_auth()
        try:
            r = self.session.post(
                f"{self.base}{path}",
                json=body or {},
                headers=headers,
                timeout=self.timeout,
            )
            if r.status_code == 401:  # 票据失效（如 QQ 重启后）：清缓存重试一次
                self._clear_cred(self.base)
                headers = self._ensure_auth()
                r = self.session.post(
                    f"{self.base}{path}",
                    json=body or {},
                    headers=headers,
                    timeout=self.timeout,
                )
            r.raise_for_status()
            return r.json()
        except requests.HTTPError:
            raise
        except Exception:
            raise

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

    def reload_plugin(self, plugin_id: str) -> None:
        """禁用再启用插件，使磁盘上的新代码/数据生效。"""
        self._post("/api/Plugin/SetStatus", {"id": plugin_id, "enable": False})
        self._post("/api/Plugin/SetStatus", {"id": plugin_id, "enable": True})
