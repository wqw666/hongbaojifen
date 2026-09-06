"""二维码图片：按当前二维码 URL 用 qrcode 库现生成。

不用 NapCat 缓存 qrcode.png——该文件只在 QQ 生成新码时重写，过期后不再更新，
若优先读它，界面会永远停在过期码上（扫码失败，点「刷新」也只是重读同一文件）。
URL 每轮轮询都从 QQ 实时取，按 URL 渲染才能保证图上画的与 QQ 侧的码一致。
"""

from __future__ import annotations

from PIL import Image


def qrcode_image(url: str = "", size: int = 260) -> Image.Image | None:
    url = (url or "").strip()
    if not url:
        return None
    try:
        import qrcode

        qr = qrcode.QRCode(box_size=4, border=2)
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
        img.thumbnail((size, size), Image.Resampling.LANCZOS)
        return img
    except Exception:
        return None


def pil_to_ctk(pil_img: Image.Image, size: int = 260):
    import customtkinter as ctk

    return ctk.CTkImage(light_image=pil_img, dark_image=pil_img, size=(size, size))
