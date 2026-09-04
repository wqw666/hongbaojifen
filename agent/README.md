# agent — QQ 群红包监控执行器

红包积分管理系统（hongbaojifen 总后台）的外部执行器。部署在 QQ 群侧 Windows 电脑上：监控/自动领取 QQ 钱包红包，统计领取明细（领取人/时间/金额）；对接总后台，作为**执行器**接受远程命令、上报 QQ 群与群成员（自动注册为**会员**），并支持按**玩法**在游戏群计分、**结算上报**入账（连接信息在 GUI「总后台对接」页配置）。

QQ 只有两种身份：**会员**（members，从群成员同步而来）或**操作员**（本机登录的管理 QQ，心跳自动登记）

## 目录结构

| 路径 | 说明 |
|------|------|
| `gui/` | Python + CustomTkinter 桌面端（扫码登录/状态/实时监控/历史查询/设置/总后台对接/游戏玩法/使用说明） |
| `integration/` | **总后台对接包**（独立于 GUI，纯 requests+标准库，见下文「与总后台对接」） |
| `play_rules/` | 两个测试玩法样本（rule_add1.py 数字+1 / rule_add2.py 数字+2），可上传总后台试用 |
| `plugin/` | NapCat 插件 TS 源码（grabRedBag / pullDetail / play 玩法转发），构建产物 `plugin/dist/index.mjs` |
| `client/` | 命令行红包查询工具 `query.py` |
| `tools/` | `NapCat/` QQ 框架运行时（不入 git，`setup_napcat.bat` 安装）+ `restart_napcat_hidden.vbs` |
| `scripts/` | 打包脚本（assemble_release.py 等） |
| `docs/` | **框架与代码架构说明**（[代码架构说明.md](docs/代码架构说明.md)，理解代码先读它） |
| `打包exe.bat` | **一键打包 exe（快速测试用）**，产物 `dist\agent.exe` |
| `build.bat` / `QQHongbaoMonitor.spec` | 完整交付包打包（exe + release + zip） |
| `一键启动.bat` / `start_silent.bat` / `启动.vbs` | 启动链：同步插件 → 拉起 NapCat → 等插件就绪 → 打开 GUI |
| `使用说明.txt` | 部署机器的上手说明 |

配置保存在 `%APPDATA%\QQHongbaoMonitor\config.json`。

**想理解代码**：先读 [docs/代码架构说明.md](docs/代码架构说明.md)（启动链/目录/GUI 线程模型/插件抓包流程/数据存储/总后台对接/打包全讲清，行号引用源码）。

## 本地开发

- 依赖：Python 3.10+；Node.js 18+ 仅在需要重新构建插件（`plugin/dist` 缺失）时必需
- GUI 调试：`run_dev.bat`（`pip install -r requirements.txt` + `python gui/main.py`）
- 插件构建：`cd plugin && npm install && npm run build`
- NapCat 安装：`tools\setup_napcat.bat "NapCat解压目录"`（本机 `tools\NapCat` 已就位，不入 git）

## 打包

两个入口（都在 `agent\` 目录双击即可，自动定位 Python）：

```
打包exe.bat   只打包 exe：产物 dist\agent.exe，改代码后反复测试用（快）
build.bat     完整交付包：产物 release\ + agent_交付包.zip（慢，含插件构建/NapCat 组装）
```

- `打包exe.bat`：跳过 release/NapCat 拷贝，几十秒出包；插件已构建或 `plugin_bundle\index.mjs` 存在时无需 Node
- `build.bat`：插件已构建时无需 Node（自动定位 Python：本地路径 → `py -3` → PATH）；产物 `release\`（解压即用目录）+ `agent_交付包.zip`（根目录）

## 部署到机器

1. 解压 `agent_交付包.zip` 到目标 Windows 机器
2. 双击 `一键启动.bat`（或 `启动.vbs` 无黑窗启动）
3. 软件内扫码登录 NapCat → 设置群号 → 部署插件 → 开始监控
4. **每台机器独立扫码登录**，不要复制其他机器的登录态

## 与总后台（hongbaojifen）对接

对接代码全部在 `integration/`（GUI 只做挂载，互不影响）：

| 模块 | 职责 |
|------|------|
| `backend_client.py` | `HbjfClient(base_url, api_key, token)`：心跳(带操作员QQ)/群/操作员QQ/会员/命令/**对局上报**客户端 |
| `sync_worker.py` | `SyncWorker` 后台线程：周期心跳(自动登记操作员QQ并记登录时间) → 拉取远程命令 → 本地执行 → 回报 done/failed；收到 40310/40311 进入停摆 |
| `member_sync.py` | 会员同步：取群成员 → 上报群信息(含群创建时间) → 批量查积分 → 未建档自动注册为会员(注册人=操作员QQ) |
| `auth.py` | 把本机登录 QQ 上报为**操作员**（通过 agent 登录并连接总后台的都是操作员） |
| `smoke.py` | 全链路冒烟测试（心跳自动登记/群/会员同步/对局上报+幂等可选） |
| `play_engine.py` | 玩法引擎 v2：加载玩法 .py 算回复与积分变动（`engine.last_delta`）+ `RoundSession` 每群每局缓冲/结算（GUI 玩法页使用；`python -m integration.play_engine` 自测） |

### 使用

1. 总后台「执行器管理」新增执行器 → 拿到 token（**一机一号**，不与他人共用）
2. GUI「总后台对接」页：填总后台地址 / 对接密钥 / token → 「测试连接」→ 「启动对接」
3. 「操作员QQ」区点「上报本机登录QQ」→ 总后台自动登记为**操作员**（此后每次心跳自动续活，并记录最近登录时间/来源 IP/主机）
4. 选「会员群」（或填写群号），「立即同步会员」→ 群信息（含创建时间）与群成员全部上报注册进总后台会员库（注册人=操作员QQ）
5. 总后台管理员在「执行器管理」下发命令，agent 自动执行并回报；执行器被总后台**封禁**（40310）或操作员被停用/禁手动（40311）→ agent 停摆红字提示，后台处理后重开即恢复

### 远程命令集 v1（总后台 → agent）

| 命令 | 参数 | 效果 |
|------|------|------|
| `get_status` | — | 回报在线状态/配置快照 |
| `set_config` | `{"heartbeat_interval_sec": 60}` | 改本机配置（白名单键：`heartbeat_interval_sec`/`member_group_id`/`auto_sync_members`） |
| `sync_members` | `{"group_id": "..."}` | 立即同步指定群（缺省用已配置的会员群） |

命令**必须幂等**：总后台发送 5 分钟未收到回报会重投一次，重复执行不应产生副作用（同步类命令天然幂等）。

### 冒烟测试

```bash
python -m integration.smoke --base http://localhost:8892 --key hbjf-open-2026 \
  --token <执行器token> --self-qq <本机QQ> --self-nick <昵称> \
  --member-count 3 --member-prefix 60030 --play-events 4
# --self-qq 验证操作员QQ自动登记(心跳带 admin_qq)；--member-count N 跑 N 个假成员的会员同步
# --play-events N 用 N 条假事件跑对局上报 + round_id 重复上报幂等（后两者会真实写库！）
```

后端停机时应输出友好「无法连接总后台」而非 traceback。

### 配置项（`%APPDATA%\QQHongbaoMonitor\config.json`，GUI 维护）

`backend_base`（默认 `http://localhost:8892`）、`backend_api_key`、`executor_token`、`executor_name`、`heartbeat_interval_sec`（默认 10，最小 5）、`member_group_id`（会员群号）、`auto_sync_members`（周期任务中自动同步，≥5 分钟一次）。

心跳默认 **10 秒一次**；总后台超过阈值（默认 30s ≈ 3 个心跳周期，`app.executor-offline-seconds` 可调）未收到心跳即自动把执行器显示为**离线**——离线只是展示语义，不阻止下次心跳自动回线；若调大本机心跳间隔，需同步调大总后台阈值避免误判离线。

## 游戏玩法（多游戏群自动回复 + 结算上报）

在总后台「会员玩法管理」上传玩法文件，本 agent 拉取后在指定**游戏群**里自动回复并按玩法返回的积分变动计分；操作员在 GUI 结算上报对局 → 总后台实时入账，管理端「游戏记录」可见每局**回放**（默认保留 30 天）。群主/管理员（NapCat 登录 QQ）也是玩家，同样触发（只有机器人自己的发言不触发，防回环）。

### 链路

```
QQ 群消息 → NapCat 插件 play.ts → POST 127.0.0.1:6101/play/msg（本机回调）
          → 玩法引擎 RuleEngine 加载玩法 .py 算 (回复, delta) → POST 插件 /play/reply
          → send_group_msg（自动 @ 发言者）→ 群成员看到机器人回复
          → 该条 (reply, delta) 计入当前局 RoundSession 事件缓冲
          → 「结算并上报」→ POST /api/open/games/report → 总后台逐事件入账
```

GUI「游戏玩法」页操作：填总后台地址 →「刷新玩法列表」（仅显示**启用**状态玩法）→ 选中玩法 → 填/选**游戏群号（可多个，逗号分隔）**→（可选勾「自动注册群成员为会员」）→「启用玩法」（自动下载玩法文件 → 打开插件转发 → 激活引擎）。重启 GUI 自动恢复上次玩法（重新拉最新文件）并续传未结算对局。「停止玩法」先自动结算，成功才停。

- 每群独立成局：`round_id = 群尾号-开赛时间-随机`，同局事件累积 → 结算一次性入账；`round_id` 幂等，崩溃/重传不重复计分
- 事件缓冲达 1000 条自动结算（软阈值），2000 条硬性截断（先结算再继续）；玩法页实时显示每群「局号 | 事件数 | 净积分」
- 总后台未知会员/停用/余额不足 → 该事件按 delta=0 记入时间线并随结算回报 `warning` 明细
- 被总后台封禁（40310）/操作员停用或禁手动（40311）→ 引擎与同步全部停摆（GUI 红字），未结算事件保留，后台处理后重开/重试恢复

### 玩法文件协议（总后台玩法页上传的 .py 必须遵守）

```python
def handle_message(group_id: int, qq: int, nickname: str, text: str):
    """三种返回值：
    None                        = 不回复（纯互动，不回复）
    "回复文本"                   = v1 写法，自动 @ 发言者（积分变动 0）
    {"reply": "文本", "delta": n}  或 ("文本", n)   = v2：reply 同上；delta>0 加分 / <0 扣分 / 0 只记过程
    """
```

- 玩法就是一段普通 Python 源码，可自由 import 标准库/自写逻辑；上传格式任意，引擎按 .py 执行
- 引擎按文件 mtime **热重载**：总后台重传 → 玩法页重新启用/agent 下次启用即取新逻辑
- 引擎对同一 qq 1 秒内重复发言去重（防刷屏）；玩法代码抛异常只记日志不崩引擎；每次调用后的积分变动落在 `engine.last_delta`
- 样本在 `play_rules/`（rule_add1.py 数字+1、rule_add2.py 数字+2，v1 无积分版）；v2 写法参考引擎自测内置用例：`python -m integration.play_engine`

玩法相关配置项：`play_rule_id` / `play_rule_name` / `play_group_id`（游戏群号，逗号分隔多个）/ `play_enabled` / `play_callback_port`（默认 6101）/ `play_auto_register_members`（启用玩法后自动把游戏群成员注册为会员）。玩法文件缓存于 `%APPDATA%\QQHongbaoMonitor\plays\rule_{id}.py`。完整设计见 [docs/玩法v2结算与上报设计.md](docs/玩法v2结算与上报设计.md)。

**后端注意**：玩法文件存放目录由总后台 `rules-dir` 配置；上传用相对路径（如默认 `./data/rules`）时 `MultipartFile.transferTo` 会解析到 Servlet 临时目录导致下载「文件已丢失」，已修复为绝对路径落盘（prod 请继续用绝对路径，如 `/opt/hongbaojifen/data/rules`）。

## 注意

- 仅支持 QQ 钱包红包；机器人账号须收到过该群红包消息
- NapCat 与 PC QQ 同协议，同一 QQ 不可双开
- 自动化存在封号风险，请合规使用
