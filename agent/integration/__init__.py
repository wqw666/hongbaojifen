"""总后台对接集成包（agent 执行器功能）。

纯 requests + 标准库实现，不依赖 GUI / 插件模块，可独立测试：
- backend_client : HbjfClient，总后台开放接口客户端（心跳/群/QQ号/会员/命令通道）
- member_sync    : 群成员 → 会员库同步（拉群成员→上报群→批量查积分→注册未建档）
- sync_worker    : SyncWorker 后台周期线程（心跳 + poll 命令 + 执行回报）
- auth           : agent 登录 QQ 自动登记为管理员
- smoke          : 命令行冒烟测试 python -m integration.smoke
"""
