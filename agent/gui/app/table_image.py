"""群公告表格图片渲染（Pillow）：封盘/结算播报把纯文本表格渲染成整齐的表格图（R7-3/4）。

img 协议（玩法规则返回的 img 键，与文本表格同源的结构化数据）：
  {"caption": "————停结————\n本局玩法：撑庄（庄家：甲）\n合计 500 分",  # 图片外标题行（多行 = 公告文字段，GUI 随图发出）
   "headers": ["序号", "用户名称", "下注积分"],
   "rows":    [[1, "甲", 100], ...],      # 单元格按 str() 展示
   "right":   [0, 2],                     # 右对齐列下标（数值列）
   "footer":  "撑 甲（红包0.40 点4）：结算 -10"}  # 可选表尾行（撑庄结算紧凑行；庄未抢 = 红包0.00 点0）

渲染失败（无 Pillow / 无可用中文字体 / 数据异常 / 行数超限）返回 None，
调用方降级为整段纯文本表格。表格行数超过 TABLE_IMAGE_MAX_ROWS 时图片过长不整齐，也走降级。
"""
from __future__ import annotations

import io
import os

from PIL import Image, ImageDraw, ImageFont

TABLE_IMAGE_MAX_ROWS = 60

# 中文字体候选（Windows 雅黑/黑体 → macOS 苹方 → Linux Noto/Droid），粗体优先专用字体文件
_FONT_FILES = [
    ("C:/Windows/Fonts/msyhbd.ttc", "C:/Windows/Fonts/msyh.ttc"),
    ("C:/Windows/Fonts/simhei.ttf", "C:/Windows/Fonts/simhei.ttf"),
    ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/msyh.ttc"),
    ("C:/Windows/Fonts/simsun.ttc", "C:/Windows/Fonts/simsun.ttc"),
    ("/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/PingFang.ttc"),
    ("/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/STHeiti Medium.ttc"),
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
     "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"),
    ("/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
     "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf"),
]

_font_sel: tuple[str, str] | None = None       # 探测结果缓存 (regular, bold)
_font_cache: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}


def _pick_fonts() -> tuple[str, str] | None:
    """探测第一组真实存在的中文字体文件；全部缺失返回 None。"""
    global _font_sel
    if _font_sel is None:
        for reg, bold in _FONT_FILES:
            if os.path.isfile(reg):
                b = bold if os.path.isfile(bold) else reg
                _font_sel = (reg, b)
                break
        _font_sel = _font_sel or (None, None)
    return _font_sel if _font_sel[0] else None


def _font(path: str, size: int) -> ImageFont.FreeTypeFont:
    key = (path, size)
    f = _font_cache.get(key)
    if f is None:
        f = ImageFont.truetype(path, size)
        _font_cache[key] = f
    return f


# 表格配色（白底 + 浅灰表头 + 细分隔线，QQ 里观感干净）
_BG = "#FFFFFF"
_HEAD_BG = "#EEF1F6"
_ZEBRA_BG = "#FAFBFC"
_GRID = "#C9CDD4"
_TEXT = "#1F2329"
_HEAD_TEXT = "#1F2329"
_FOOT_TEXT = "#B2451B"
_PAD_X = 10          # 单元格左右留白
_MARGIN = 12         # 画布边距
_ROW_H = 27          # 数据行高
_HEAD_H = 32         # 表头行高
_FOOT_H = 30         # 表尾行高


def render_table_png(img: dict) -> bytes | None:
    """img 协议 → PNG bytes；不可渲染返回 None（调用方降级纯文本）。"""
    try:
        spec = img or {}
        headers = [str(h) for h in (spec.get("headers") or [])]
        rows = [[str(c) for c in r] for r in (spec.get("rows") or [])]
        if not headers or not rows or len(rows) > TABLE_IMAGE_MAX_ROWS:
            return None
        ncols = len(headers)
        rows = [r + [""] * (ncols - len(r)) for r in rows]  # 缺列补空（防御）
        right = {int(c) for c in (spec.get("right") or [])}
        footer = str(spec.get("footer") or "").strip()

        fonts = _pick_fonts()
        if fonts is None:
            return None
        reg_path, bold_path = fonts
        f_body = _font(reg_path, 15)
        f_head = _font(bold_path, 16)
        f_foot = _font(bold_path, 14)

        probe = ImageDraw.Draw(Image.new("RGB", (8, 8)))

        def tw(font: ImageFont.FreeTypeFont, s: str) -> float:
            return probe.textlength(s, font=font)

        widths: list[int] = []
        for c in range(ncols):
            w = int(max([tw(f_head, headers[c])]
                        + [tw(f_body, r[c]) for r in rows])) + _PAD_X * 2
            widths.append(w)

        total_w = sum(widths) + _MARGIN * 2
        total_h = _MARGIN + _HEAD_H + len(rows) * _ROW_H \
            + (_FOOT_H if footer else 0) + _MARGIN
        img_png = Image.new("RGB", (total_w, total_h), _BG)
        d = ImageDraw.Draw(img_png)

        asc_reg, desc_reg = f_body.getmetrics()
        lh_body = asc_reg - desc_reg  # 行内文字高（基准偏移用）
        y = _MARGIN

        def text_at(col_x: int, col_w: int, baseline: int, s: str,
                    font, align_right: bool) -> None:
            if align_right:
                x = col_x + col_w - _PAD_X - tw(font, s)
            else:
                x = col_x + _PAD_X
            d.text((x, baseline), s, font=font, fill=_TEXT, anchor="ls")

        # 表头行
        d.rectangle((_MARGIN, y, _MARGIN + sum(widths), y + _HEAD_H),
                    fill=_HEAD_BG)
        baseline = y + (_HEAD_H + lh_body) // 2
        x = _MARGIN
        for c in range(ncols):
            text_at(x, widths[c], baseline, headers[c], f_head, c in right)
            x += widths[c]
        y += _HEAD_H

        # 数据行（隔行底色）
        for i, r in enumerate(rows):
            if i % 2 == 1:
                d.rectangle((_MARGIN, y, _MARGIN + sum(widths), y + _ROW_H),
                            fill=_ZEBRA_BG)
            baseline = y + (_ROW_H + lh_body) // 2
            x = _MARGIN
            for c in range(ncols):
                text_at(x, widths[c], baseline, r[c], f_body, c in right)
                x += widths[c]
            y += _ROW_H

        # 水平分隔线：表头下沿 + 各行下沿（细网格）
        gy = _MARGIN + _HEAD_H
        for _ in range(len(rows) + 1):
            d.line((_MARGIN, gy, _MARGIN + sum(widths), gy), fill=_GRID, width=1)
            gy += _ROW_H
        # 纵向分隔线（表头到末行）
        gx = _MARGIN
        top = _MARGIN
        bottom = _MARGIN + _HEAD_H + len(rows) * _ROW_H
        d.line((gx, top, gx, bottom), fill=_GRID, width=1)
        for w in widths:
            gx += w
            d.line((gx, top, gx, bottom), fill=_GRID, width=1)
        # 表尾（如撑庄结算紧凑行）带顶部分隔线
        if footer:
            d.line((_MARGIN, y, _MARGIN + sum(widths), y), fill=_GRID, width=1)
            asc_f, desc_f = f_foot.getmetrics()
            lh_f = asc_f - desc_f
            d.text((_MARGIN + _PAD_X, y + (_FOOT_H + lh_f) // 2), footer,
                   font=f_foot, fill=_FOOT_TEXT, anchor="ls")

        buf = io.BytesIO()
        img_png.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:  # noqa: BLE001 — 渲染异常一律降级纯文本
        return None
