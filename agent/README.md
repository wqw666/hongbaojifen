# agent — QQ 群红包监控执行器

红包积分管理系统（hongbaojifen 总后台）的外部执行器。部署在 QQ 群侧 Windows 电脑上：监控/自动领取 QQ 钱包红包，统计领取明细（领取人/时间/金额）；v1.1 起对接总后台，作为**执行器**接受远程命令、把 QQ 群/QQ 号/会员同步到总后台（连接信息在 GUI「总后台对接」页配置）。

## 目录结构

| 路径 | 说明 |
|------|------|
| `gui/` | Python + CustomTkinter 桌面端（扫码登录/状态/实时监控/历史查询/设置/总后台对接） |
| `integration/` | **总后台对接包**（独立于 GUI，纯 requests+标准库，见下文「与总后台对接」） |
| `plugin/` | NapCat 插件 TS 源码（grabRedBag / pullDetail），构建产物 `plugin/dist/index.mjs` |
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
| `backend_client.py` | `HbjfClient(base_url, api_key, token)`：心跳/群/QQ号/会员/命令通道客户端 |
| `sync_worker.py` | `SyncWorker` 后台线程：周期心跳 → 拉取远程命令 → 本地执行 → 回报 done/failed |
| `member_sync.py` | 会员同步：取群成员 → 上报群信息 → 批量查积分 → 未建档自动注册为会员 |
| `auth.py` | 把本机登录 QQ 上报为管理员 QQ（通过 agent 登录并连接总后台的都是管理员） |
| `smoke.py` | 全链路冒烟测试 |

### 使用

1. 总后台「执行器管理」新增执行器 → 拿到 token（**一机一号**，不与他人共用）
2. GUI「总后台对接」页：填总后台地址 / 对接密钥 / token → 「测试连接」→ 「启动对接」
3. 选「会员群」（或填写群号），「立即同步会员」→ 群成员全部注册进总后台会员库，群主/管理员自动推导上报
4. 总后台管理员在「执行器管理」下发命令，agent 自动执行并回报

### 远程命令集 v1（总后台 → agent）

| 命令 | 参数 | 效果 |
|------|------|------|
| `get_status` | — | 回报在线状态/配置快照 |
| `set_config` | `{"heartbeat_interval_sec": 60}` | 改本机配置（白名单键：`heartbeat_interval_sec`/`member_group_id`/`auto_sync_members`） |
| `sync_members` | `{"group_id": "..."}` | 立即同步指定群（缺省用已配置的会员群） |

命令**必须幂等**：总后台发送 5 分钟未收到回报会重投一次，重复执行不应产生副作用（同步类命令天然幂等）。

### 冒烟测试

```bash
python -m integration.smoke --base http://localhost:8892 --key hbjf-open-2026 --token <执行器token>
# 可选：--self-qq <QQ> 验证管理员号上报；--member-count N 跑 N 个假成员的会员同步（会真实入库！）
```

后端停机时应输出友好「无法连接总后台」而非 traceback。

### 配置项（`%APPDATA%\QQHongbaoMonitor\config.json`，GUI 维护）

`backend_base`（默认 `http://localhost:8892`）、`backend_api_key`、`executor_token`、`executor_name`、`heartbeat_interval_sec`（默认 30，最小 5）、`member_group_id`（会员群号）、`auto_sync_members`（周期任务中自动同步，≥5 分钟一次）。

## 注意

- 仅支持 QQ 钱包红包；机器人账号须收到过该群红包消息
- NapCat 与 PC QQ 同协议，同一 QQ 不可双开
- 自动化存在封号风险，请合规使用
