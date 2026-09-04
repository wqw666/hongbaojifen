"""玩法 2：数字 +2 / 非数字提示

玩家在群里发一个纯数字，机器人自动 @ 他并回复「数字 + 2」；
发非数字内容则回复「请输入数字~」。

玩法文件协议 v1（总后台玩法管理页上传的 .py 必须遵守）：
    def handle_message(group_id: int, qq: int, nickname: str, text: str) -> str | None
返回 None = 不回复；返回 str = 回复文本（发送侧自动 @ 发言者）。
"""
from __future__ import annotations


def handle_message(group_id: int, qq: int, nickname: str, text: str) -> str | None:
    t = (text or "").strip()
    if not t.isdigit():
        return "请输入数字~"
    return str(int(t) + 2)
