param(
    [string]$ReleaseDir = (Join-Path (Split-Path $PSScriptRoot -Parent) "release")
)

$doc = @"
========================================
  agent — QQ 群红包监控执行器 使用说明
========================================

【您收到的文件】
  agent.exe               主程序（推荐双击）
  一键启动.bat / 启动.vbs  无黑窗启动（含 NapCat 预热）
  tools\NapCat\           内置 NapCat（必需，勿删）
  gui\ plugin\ client\    完整源码

【首次使用】（约 5 分钟）

  1. 解压整个 release 文件夹到任意目录

  2. 双击「一键启动.bat」或「agent.exe」

  3. 软件内：
     - 「QQ 扫码登录」→ 启动 NapCat → 手机 QQ 扫码
     - 「设置」填要监听的群号 → 保存
     - 「部署插件并进入监控」
     - 「状态」页刷新连接，绿色即成功

  4. 开始监控：实时看红包、领取人、金额、时间

【日常使用】
  · 一键启动.bat：自动重启 NapCat + 打开 GUI
  · 实时监控：红包列表 + 领取明细（约 2 秒刷新）
  · 历史查询：群号 + 时间段
  · 发红包：请在 QQ 里手动发

【目录说明】
  agent.exe             GUI 主程序
  tools\NapCat\         QQ 协议框架（内置）
  gui\                  Python 界面源码
  plugin\src\           TypeScript 插件源码
  plugin\dist\          已编译插件 index.mjs
  client\query.py       命令行查红包脚本
  plugin_bundle\        打包进 exe 的插件副本
  build.bat             开发者重新打包

【源码模式调试】
  安装 Python 3.10+ 后运行 run_dev.bat

【配置保存位置】
  %APPDATA%\QQHongbaoMonitor\config.json

【注意】
  · 仅支持 QQ 钱包红包
  · 每台机器独立扫码登录，不要复制其他机器的登录态
  · 同一 QQ 不要同时开两个 PC 客户端
  · 自动化存在封号风险，请合规使用

【技术支持】
  软件内「使用说明」页有更详细步骤
========================================
"@

$path = Join-Path $ReleaseDir "使用说明.txt"
[System.IO.File]::WriteAllText($path, $doc, [System.Text.UTF8Encoding]::new($false))
Write-Host "Wrote $path"
