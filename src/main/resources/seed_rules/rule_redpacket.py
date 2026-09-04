"""红包计分玩法（上传到总后台「会员玩法管理」后启用）：

管理员/群主在游戏群发红包 → 成员领取时 agent 自动按红包金额计分：
积分 = 金额各位数之和（1.11 元 → 1+1+1=3 分；0.15 → 0+1+5=6 分）。

- 每个领取人立即收到 @回复：「领取X元，获得Y积分」（handle_redpacket 返回值）
- 红包领完（recv_num>=total_num 或领取人数达群人数）agent 自动 @全体 播报逐人结算
- 机器人自己领取的份额不计分；同人同包幂等；只有群主/管理员发的红包参与玩法

返回协议同 handle_message：None=不计这条；str=(回复, 0分)；tuple/dict=(回复, 积分)。
"""


def handle_message(group_id, qq, nickname, text):
    """纯红包玩法：聊天消息不回复。"""
    return None


def handle_redpacket(group_id, qq, nickname, amount):
    """红包领取计分：积分=金额各位数之和，回复显示金额与积分。"""
    a = float(amount or 0)
    if a <= 0:
        return None
    s = f"{a:.2f}".replace(".", "")
    points = sum(int(ch) for ch in s if ch.isdigit())
    return f"领取{a:.2f}元，获得{points}积分", points
