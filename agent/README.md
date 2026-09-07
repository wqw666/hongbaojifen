# agent — QQ 群红包监控执行器

红包积分管理系统（hongbaojifen 总后台）的外部执行器。部署在 QQ 群侧 Windows 电脑上：监控/自动领取 QQ 钱包红包，统计领取明细（领取人/时间/金额）；对接总后台，作为**执行器**接受远程命令、上报 QQ 群与群成员（自动注册为**会员**），并支持按**玩法**在游戏群计分、**结算上报**入账（连接信息在 GUI「总后台对接」页配置）。

QQ 只有两种身份：**会员**（members，从群成员同步而来）或**操作员**（本机登录的管理 QQ，心跳自动登记）

## 目录结构

| 路径 | 说明 |
|------|------|
| `gui/` | Python + CustomTkinter 桌面端（扫码登录；主界面 7 个 tab：群管理/会员/游戏玩法/积分审批/实时监控/总后台对接/更多） |
| `integration/` | **总后台对接包**（独立于 GUI，纯 requests+标准库，见下文「与总后台对接」） |
| `play_rules/` | 玩法文件样本（**rule_fuhe.py 复合玩法**——当前唯一玩法，与总后台内置种子同源镜像） |
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

**换 QQ / 退出登录**：关闭 agent 窗口不会退出 QQ（进程保持运行，重开即自动进主界面）。需要换号时点主界面右上角**「退出登录」**——立即停止本机 QQ 与 NapCat（QQ 退出登录、服务进程结束）；回到登录页后点**「启动 QQ 环境并扫码登录」**重新拉起，扫码即可登录新号（无快捷登录，每次都以扫码方式登录）。

## 与总后台（hongbaojifen）对接

对接代码全部在 `integration/`（GUI 只做挂载，互不影响）：

| 模块 | 职责 |
|------|------|
| `backend_client.py` | `HbjfClient(base_url, api_key, token)`：心跳(带操作员QQ)/群/操作员QQ/会员/命令/**对局上报**客户端 |
| `sync_worker.py` | `SyncWorker` 后台线程：周期心跳(自动登记操作员QQ并记登录时间) → 拉取远程命令 → 本地执行 → 回报 done/failed；收到 40310/40311 进入停摆 |
| `member_sync.py` | 会员同步：取群成员 → 上报群信息(含群创建时间) → 批量查积分 → 未建档自动注册为会员(注册人=操作员QQ) |
| `auth.py` | 把本机登录 QQ 上报为**操作员**（通过 agent 登录并连接总后台的都是操作员） |
| `smoke.py` | 全链路冒烟测试（心跳自动登记/群/会员同步/对局上报+幂等可选） |
| `play_engine.py` | 玩法引擎：加载玩法 .py 算回复与积分变动（`engine.last_delta`）+ `call_rule(fn_name, gid, *args)` 原样透传带返回值协议；`RoundSession` 为 v2 遗留类（v3 复合玩法不启用）；`python -m integration.play_engine` 自测 |
| `redpacket_game.py` | 红包计分会话驱动 `RedPacketGame`：红包开奖会话/作废/手动结算上报（GUI 玩法页使用；`python -m integration.redpacket_game` 自测） |

### 使用

1. 总后台「执行器管理」新增执行器 → 拿到 token（**一机一号**，不与他人共用）
2. GUI「总后台对接」页：填总后台地址 / 对接密钥 / token → 「测试连接」→ 保存。
   **心跳随 agent 启动进主界面自动开启**（配置齐全即起，界面无「停止」入口；改连接/密钥/token
   保存后自动重启对接生效），封禁/停用解封后或需重连时点「启动对接」
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

心跳默认 **10 秒一次**，agent 启动进主界面后**自动开启、无法手动停止**——agent 存活且能调用总后台期间心跳持续上报，总后台显示「在线」与「agent 实际在调总后台」必然一致；总后台超过阈值（默认 30s ≈ 3 个心跳周期，`app.executor-offline-seconds` 可调）未收到心跳才自动把执行器显示为**离线**（即 agent 进程退出/断网时）。若调大本机心跳间隔，需同步调大总后台阈值避免误判离线。

## 游戏玩法（复合玩法唯一化 · 手动操作台 + 红包开奖 + 结算上报）

**玩法已收敛为唯一「复合玩法」（大吃小×撑庄合一）**：总后台「会员玩法管理」只有这一行内置玩法（`seed_rules/rule_fuhe.py`），本 agent 启动/重启用时自动拉取并激活，GUI「游戏玩法」页是它的**手动操作台**——不再有多玩法列表与启停切换。操作员在面板点「开始本局」→ 群成员发数字下注/发「撑」即可撑庄 → 点「停止下注」封盘 → 管理员在群内发红包开奖 → 点「结算」开奖并上报总后台实时入账（管理端「游戏记录」可见每局**回放**，默认保留 30 天）。**积分只经红包结算产生**（下注/撑庄不动任何积分），群主/管理员（NapCat 登录 QQ）也是玩家（只有机器人自己的发言不触发，防回环）。**玩法细节（指令文案、点数算法、结算向量）以 `play_rules/rule_fuhe.py` 顶部说明与内建自测为准**，本节讲操作与协议。

### 链路

```
QQ 群消息 → NapCat 插件 play.ts（过滤 message_sent/白名单群/纯文本）→ POST 127.0.0.1:6101/play/msg
红包领取   → 插件实时推送 → POST 127.0.0.1:6101/play/redpacket
          → PlayTab 本地 ThreadingHTTPServer 入队 → daemon worker（_worker_loop）
文字消息   → 内置查分「查/查分/查积分」→ 局外基础玩法（上100/下100 申请确认等）
          → 下注预检（≥ 最小下注 10、不超当前积分）→ 玩法引擎算回复（下注登记/撑庄/引导语）
          → POST 插件 /play/reply → send_group_msg（自动 @ 发言者）
红包事件   → RedPacketGame.handle_claim（只认本局第 1 个管理员红包）→ 逐领取人回执「已记录：抢到 X 元（点数 N）」
          → 红包总份数 < 需开奖人数 → 自动作废播报（积分未扣）
结算（面板按钮，worker 线程）→ 玩法 settle_redpacket 逐人开奖算 delta（未领者按 0 点）
          → POST /api/open/games/report {round_id, ...} → 总后台逐事件入账（round_id 幂等）
          → 逐人 @ 开奖回复 + @全体 播报结算汇总
```

### GUI 玩法页操作（手动操作台）

1. 启动 agent 进主界面「游戏玩法」页：自动从总后台拉取**唯一玩法**并激活（`_auto_restore`；连接好总后台前按钮置灰）
2. 勾选**游戏群**（可多群；勾选即时推送插件转发名单并落盘，重启自动恢复）
3. 「当前群」选群 → 「开始本局」→ @全体 播报开场白（含局号：**纯数字递增 0000001 起，R7 起不带玩法名前缀**；`game_round_seq` 持久递增）
4. 群内玩法：成员直接发**数字下注**（如 500；旧词「下注500」兼容）；发**「撑」即可撑庄**（先到先得，已下注的不能再撑）；发「上100/下100」提交上/下分申请（进「积分审批」页）；群内发「开始游戏/开局」只引导去面板（Ruling：不开局）
5. 「停止下注」封盘：@全体 播报短句「停止下注，合计 N 分」+ **下注汇总表格图**（R7 起用图片整齐展示；尾部「请管理员发红包开奖（每份…）」引导已移除，开红包指引只在玩法日志提示管理员）；**无进行中的局**点停注 = 只记玩法日志、不发群；**撑庄局先查庄家余额**（需 ≥ 下注池+抽水，不足自动作废播报，积分未扣）
6. 管理员在群内发**红包**开奖 → 成员抢到即收到回执（金额+点数）；红包份数不足 → 自动作废播报；**第 2 个管理员红包被忽略**（只认第 1 个，防凑份数）
7. 「结算」开奖：未领完需**再点一次确认**（未领者按 0 点输光）→ @全体 播报短句「本局结果，总分 N 分」+ **结算结果表格图**（R7 起；无逐人 @ 开奖）→ 开奖表补「结果」列（保留到下次开局）
   - 开奖表每行 = 下注人或庄家；**双击「抢到金额」可直接改值**（补记管理员没抢到的成员），点数即时重算；R7 起每次改/补值各记一条 delta0 对局回放（「管理员改开奖金额：旧→新 元」/「管理员补值开奖：X 元」），随结算上报管理端回放可见
8. 「作废本局」= 播报作废并关局、**不上报不结算**（无人下注/红包不足/余额不足/手动均可作废，积分未扣）
9. 「更新玩法文件」= 从总后台重下载最新玩法 → 热生效；「重试未上报」= 补上报失败/未上报的红包局（每 30s 自动兜底重试，round_id 幂等防重复入账）

- 回放：本局内下注、撑庄、领取回执、开奖回复与群聊天全程计入对局回放（`record_chat`），随结算上报总后台
- 总后台未知会员/停用/余额不足 → 该事件按 delta=0 记入时间线并随结算回报 `warning` 明细
- 被总后台封禁（40310）/操作员停用或禁手动（40311）→ 引擎与同步全部停摆（GUI 红字），局与事件保留，后台处理后重开/重试恢复
- 常驻查分指令：成员发「查 / 查分 / 查积分」→ @回复其当前积分（查总后台余额；先于玩法与局状态处理，不入对局不计回放，每人 3s 冷却）

### 复合玩法规则速览（rule_fuhe.py）

- **下注期**：发数字即下注（自动 @ 回复「本局 {局号}，{累计下注名单}」）；发「撑」→ 成为庄家（已下注者不能撑庄）；群主/管理员也按同一规则参与（机器人自己除外）
- **点数**：每份红包金额（0.01~0.99 元）的两位小数位数字相加取个位（0.60→6 点、0.77→4 点）；金额越界（>0.99 或非法）回执提示不计点，可在开奖表修正或作废
- **封盘后**：无庄 = 大吃小（点数比大小，赢家吃输家，抽水按费率 `game_fee_rate`‰ 抽取）；有庄 = 撑庄擂台（点数小于庄家押注归庄、大于庄家 1:1 赢得押注、同点退回押注；未抢红包按 0 点）。平局/多同名次消化顺序见 rule_fuhe `_settle` 与自测验收向量
- 自测：`python play_rules/rule_fuhe.py`（69 断言：下注/撑庄/封盘/点数比较/大吃小/撑庄结算/作废向量；R7 起含封盘/结算 img 结构化表格断言、发红包尾巴移除断言）

### 玩法文件协议（总后台玩法页上传的 .py 必须遵守）

```python
def handle_message(group_id: int, qq: int, nickname: str, text: str):
    """三种返回值：
    None                        = 不回复（纯互动，不回复）
    "回复文本"                   = v1 写法，自动 @ 发言者（积分变动 0）
    {"reply": "文本", "delta": n}  或 ("文本", n)   = v2：reply 同上；delta>0 加分 / <0 扣分 / 0 只记过程
    """
```

- 玩法可选协议（未定义即忽略）：`handle_redpacket` / `settle_redpacket` / `handle_round_start(group_id[, round_id])` / `handle_round_end` / `handle_round_abort`（旧「结束本局」终止问询，复合玩法已用 `handle_void` 取代）/ `bettor_qqs` / `betting_open`
- **带返回值协议**（复合玩法在用）：`handle_seal(group_id, rate_permille) -> {ok,text,banker_check}`（封盘汇总）、`handle_void(group_id) -> str`（作废公告并复位）、`claim_need(group_id) -> int`（需开奖人数）、`bet_snapshot(group_id) -> list`（开奖表行数据）、`seal_info(group_id) -> dict`（局状态/按钮门控）——一律经 `engine.call_rule(fn_name, group_id, *args)` 调用、返回值原样透传，GUI 按上述结构自行解析；`handle_redpacket` 逐领取人回执、`settle_redpacket(group_id, claims, rate_permille)` 结算返回 `{"events":[{qq,nickname,reply,delta}...], "announce":str}`
- 玩法就是一段普通 Python 源码，可自由 import 标准库/自写逻辑；上传格式任意，引擎按 .py 执行
- 引擎按文件 mtime **热重载**：总后台重传 → 玩法页「更新玩法文件」/agent 下次启用即取新逻辑
- 引擎对同一 qq 1 秒内重复发言去重（防刷屏）；玩法代码抛异常只记日志不崩引擎；每次调用后的积分变动落在 `engine.last_delta`
- 样本在 `play_rules/`（**rule_fuhe.py 复合玩法**，与总后台内置种子同源）；协议细节/自测：`python -m integration.play_engine`

玩法相关配置项：`play_rule_id` / `play_rule_name` / `play_group_id`（游戏群号，逗号分隔多个）/ `play_group_current` / `play_enabled` / `play_callback_port`（默认 6101）/ `game_round_seq`（局号计数器）/ `game_fee_rate`（费率‰，默认 20）/ `play_min_bet`（最小下注，默认 10）/ `play_auto_register_members`（启用玩法后自动把游戏群成员注册为会员）。玩法文件缓存于 `%APPDATA%\QQHongbaoMonitor\plays\rule_{id}.py`。完整架构见 [docs/代码架构说明.md §8](docs/代码架构说明.md)；玩法 v2 历史设计见 [docs/玩法v2结算与上报设计.md](docs/玩法v2结算与上报设计.md)（2026-09-06 起仅留档）。

**后端注意**：玩法文件存放目录由总后台 `rules-dir` 配置；上传用相对路径（如默认 `./data/rules`）时 `MultipartFile.transferTo` 会解析到 Servlet 临时目录导致下载「文件已丢失」，已修复为绝对路径落盘（prod 请继续用绝对路径，如 `/opt/hongbaojifen/data/rules`）。

## 注意

- 仅支持 QQ 钱包红包；机器人账号须收到过该群红包消息
- NapCat 与 PC QQ 同协议，同一 QQ 不可双开
- 自动化存在封号风险，请合规使用
