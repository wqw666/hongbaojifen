# agent — QQ 群红包监控执行器

红包积分管理系统（hongbaojifen 总后台）的外部执行器。部署在 QQ 群侧 Windows 电脑上：监控/自动领取 QQ 钱包红包，统计领取明细（领取人/时间/金额），后续将统计结果上报总后台（对接中）。

## 目录结构

| 路径 | 说明 |
|------|------|
| `gui/` | Python + CustomTkinter 桌面端（登录/状态/实时监控/历史查询/设置） |
| `plugin/` | NapCat 插件 TS 源码（grabRedBag / pullDetail），构建产物 `plugin/dist/index.mjs` |
| `client/` | 命令行红包查询工具 `query.py` |
| `tools/` | `NapCat/` QQ 框架运行时（不入 git，`setup_napcat.bat` 安装）+ `restart_napcat_hidden.vbs` |
| `scripts/` | 打包脚本（assemble_release.py 等） |
| `build.bat` / `QQHongbaoMonitor.spec` | 一键打包 `agent.exe` |
| `一键启动.bat` / `start_silent.bat` / `启动.vbs` | 启动链：同步插件 → 拉起 NapCat → 等插件就绪 → 打开 GUI |
| `使用说明.txt` | 部署机器的上手说明 |

配置保存在 `%APPDATA%\QQHongbaoMonitor\config.json`。

## 本地开发

- 依赖：Python 3.10+；Node.js 18+ 仅在需要重新构建插件（`plugin/dist` 缺失）时必需
- GUI 调试：`run_dev.bat`（`pip install -r requirements.txt` + `python gui/main.py`）
- 插件构建：`cd plugin && npm install && npm run build`
- NapCat 安装：`tools\setup_napcat.bat "NapCat解压目录"`（本机 `tools\NapCat` 已就位，不入 git）

## 打包

```
build.bat
```

在 `agent\` 目录内即可完成（插件已构建时无需 Node）：自动定位 Python（本地路径 → `py -3` → PATH）、安装依赖、PyInstaller 打包 `agent.exe`、组装交付包。

产物：`release\`（解压即用目录）+ `agent_交付包.zip`（根目录）。

## 部署到机器

1. 解压 `agent_交付包.zip` 到目标 Windows 机器
2. 双击 `一键启动.bat`（或 `启动.vbs` 无黑窗启动）
3. 软件内扫码登录 NapCat → 设置群号 → 部署插件 → 开始监控
4. **每台机器独立扫码登录**，不要复制其他机器的登录态

## 与总后台（hongbaojifen）对接

- 平台「执行器管理」注册本 agent → 获得 token
- 心跳接口现成：`POST /api/open/executor/heartbeat`（Header `X-Api-Key`）
- 红包统计上报：规划中，需平台侧新增接收接口（agent 端在 `gui/` 增加上报模块）

## 注意

- 仅支持 QQ 钱包红包；机器人账号须收到过该群红包消息
- NapCat 与 PC QQ 同协议，同一 QQ 不可双开
- 自动化存在封号风险，请合规使用
