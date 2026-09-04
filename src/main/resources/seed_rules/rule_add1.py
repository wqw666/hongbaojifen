"""玩法 1：数字 +1

玩家在群里发一个纯数字，机器人自动 @ 他并回复「数字 + 1」；
发非数字 / 空内容则不回复。

玩法文件协议 v1（总后台玩法管理页上传的 .py 必须遵守）：
    def handle_message(group_id: int, qq: int, nickname: str, text: str) -> str | None
返回 None = 不回复；返回 str = 回复文本（发送侧自动 @ 发言者）。
"""
from __future__ import annotations


def handle_message(group_id: int, qq: int, nickname: str, text: str) -> str | None:
    t = (text or "").strip()
    if not t.isdigit():
        return None
    return str(int(t) + 1)
