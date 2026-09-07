# 红包积分管理系统 — 会话交接

> 跨会话续接开发必读。最近一次完整验证：**2026-09-04**（Request B 8 项全做完：后端 40 测试全绿 + E2E 13 步 ✓ + agent 冒烟 ✓ + 前端 build ✓）。

## 项目状态

- **v1.0.4 功能集已完成**（后端 Spring Boot 3.5.3/JDK21/MySQL8/Flyway + 前端 React18/AntD5/Vite + agent 执行器）
- 本迭代 = Request B 8 项：QQ群管理+创建时间/状态(封禁)；**删除普通QQ号管理**（QQ 只是 member 或 操作员）；执行器**封禁**（仅封禁 / 封禁并重置token）；执行器详情=最近心跳IP/绑定的操作员QQ；删 executors 负责群号列（一执行器多群）；**游戏记录**（agent 上报对局回放，默认保留 30 天，保留天数配置键可改）；操作员**禁手动**（can_manual_points，禁手动不禁自动）；完整流程（心跳带 admin_qq 自动登记操作员 → 群/会员同步 → 玩法 → 结算上报 → 后端实时入账，人工审核不需要——后端自动校验）
- 默认管理员 `admin / admin123`（首次启动自动创建，BCrypt）
- 数据库 `hongbaojifen`（root/19991020），flyway_schema_history：V1/1.0.1/1.0.2/1.0.3/1.0.4 全部 success
- **不做红包金额↔积分自动挂钩**；玩法 delta 由玩法文件返回、对局结算时入账

## 服务与端口

| 服务 | 端口 | 启动方式 |
| --- | --- | --- |
| 后端 | 8892 | **当前实例 = bash 启动的 mvn 进程（PID 12204）**——原 IDEA 启动的旧进程（PID 20704）在 V1.0.3 文件落地前 boot，缺新表/列，已 taskkill；日志在 `%TEMP%\hbjf-backend.log`（写 /tmp/hbjf-backend.log） |
| 前端开发 | 3002 | `cd frontend && npm run dev`（proxy /api → 8892）；生产构建进 jar static/admin |
| agent GUI | — | `agent/run_dev.bat`（dev）；打包见 agent/README |

**后端重启注意**：现在 8892 由 bash 进程占着，若在 IDEA 里重新跑，先停 bash 那个（或换端口），否则端口冲突；IDEA 旧 run tab 显示已停止属正常。

**部署架构**（start.sh/stop.sh 只用于 Ubuntu 服务器，不用于开发；build.sh 只打包不上服务器）：
- 本地打包：`./build.sh` 或 `build.bat` → `target/hongbaojifen-api-<V>.jar`（build 不含前端时用 `mvn -Dskip.fe=true package`）
- 服务器 `/opt/hongbaojifen/`：start.sh / stop.sh / deploy.sh / VERSION / logs/
- 发版：`scp jar ubuntu@IP:/tmp/` → `ssh ubuntu@IP /opt/hongbaojifen/deploy.sh`（新 jar 首启自动跑 1.0.1~1.0.4 迁移）

## 本迭代新概念（与 v1.0 旧文档差异速查）

- **操作员 QQ**（qq_accounts 收窄，type 恒 admin_qq）：agent 心跳带 `admin_qq` 自动登记/续活（upsert 不触碰 status/can_manual_points；最近登录时间/IP/主机由服务端记）；「手动上下分」权限：member 手动上分要求 `can_manual_points=allowed`，自动玩法结算不受限
- **执行器封禁**：banned → 心跳等公共守卫拒 `40310 执行器已被封禁`；`/api/admin/executors/{id}/ban|unban|reset-token`；封禁并重置token = ban + reset 一步
- **群封禁**：qq_groups.status 收敛为 active/banned（旧 paused 废弃），封禁群不被 open upsert 解封；agent 侧封禁群不启玩法、不同步
- **对局结算**：`POST /api/open/games/report`（round_id ≤96 幂等、events ≤2000、delta 逐事件入账、未知会员/停用/余额不足 → delta=0 + warning）；管理端 game-records 列表 + `/{id}` 回放；`RetentionCleanupService` @Scheduled 每 6h 清 30 天前（配置键 `game_record_retention_days` / `operation_log_retention_days`，走 dicts）
- **操作员身份守卫**（agent 侧 `integration` 封禁/停用处理）：`ExecutorBanned` → 停摆红字；`OperatorDisabled`（40311）同
- 授权边界：**后端 Java 改动已被授权**用于本功能集；约束：不主动 git commit（除非被要求）、redbag/ 永不入库、agent GUI 线程纪律（daemon + after(0)）、登录态/密钥/token 永不提交

## 待办（后续迭代方向）

- [ ] 玩法文件版本管理（历史版本/回滚）
- [ ] 操作日志分页与导出（前端）
- [ ] 修改默认管理员密码功能
- [ ] 执行器命令下发前端 UI（现在 curl）
- [ ] 红包金额 → 积分挂钩设计（若做：建议单独发版评估）
- [ ] 上线前：改默认密钥/管理员密码 + 打包 jar + 服务器 deploy 后核对迁移

## 开放接口（agent 对接）

全部在 `/api/open/**`，需 `X-Api-Key: hbjf-open-2026`（application.yml `app.open.api-key`）。完整表见根 README.md；v1.0.3/1.0.4 增量：

- `POST /api/open/executor/heartbeat` `{token, host?, version?, admin_qq?, admin_nickname?}` — admin_qq 自动登记/续活操作员并记最近登录时间/IP（执行器封禁 → 40310）；**超时自动离线**：agent 心跳默认 10s，后端阈值 `app.executor-offline-seconds` 默认 30s，超时未心跳 → offline（list/心跳/解封时刷库；封禁中除外，离线不阻下次心跳回线）
- `POST /api/open/groups` `{group_id, create_time(19位必填), group_name?, owner_qq?, admin_qqs?, member_count?, executor_token?}` — 绑定管理执行器；封禁群不可上报解封
- `POST /api/open/members/batch` `{executor_token, members:[{qq,nickname?,group_id?}]}` — 注册人 = token 绑定操作员QQ（仅 INSERT 时落 registrar_qq）
- `POST /api/open/games/report` `{executor_token, round_id, play_id?, play_name?, group_id?, events:[{qq,nickname?,msg?,reply?,delta}]}` → `{round_id, duplicate, member_count, total_delta, event_count, warning_count, warning}`
- `GET /api/admin/game-records?group_id=&play_name=&page=&size=` + `GET /api/admin/game-records/{id}`（含 events 回放；record 带 operator_qq/executor_name/round_id）
- 管理端 qq-accounts PUT 可改 `can_manual_points`（allowed/denied）；executors ban/unban/reset-token
- 错误码新增：`40310 执行器已被封禁`、`40311 操作员被停用/禁止手动`

## 测试与验证手段

- 后端：`mvn -Dskip.fe=true test`（40 用例；测试用 H2 共享 mem 库，断言 id 前先 `TRUNCATE TABLE`）
- 全链路 E2E（本机 MySQL 真实写入，可重复）：`python tools/e2e_full.py` — admin登录→建执行器→心跳自动登记操作员→群上报(create_time)→会员幂等注册→批量查分→对局上报+5/+2/未知会员warning→重复上报幂等→积分实时入账→管理端回放→封禁40310→解封恢复（13 步）
- agent 冒烟：`cd agent && python -m integration.smoke --base http://localhost:8892 --key hbjf-open-2026 --token <token> --self-qq 60001 --self-nick 冒烟操作员 --member-count 3 --member-prefix 60030 --play-events 4`（操作员自动登记/群/会员同步/对局上报+幂等）
- agent 引擎自测：`python -m integration.play_engine`（v2 delta/热重载/坏文件…）
- 前端：`cd frontend && npm run build`（约 5s）
- 临时验证脚本/冒烟 token 等放 `%TEMP%`（不入库）

## 坑与经验（重要）

1. **中文禁止 bash 直接传**（GBK 乱码 → JSON 解析失败 50002）— 写 UTF-8 文件再 `curl -d @file`。E2E 因此用 Python 脚本实现。
2. **改 Java 代码必须重启应用** — 运行中 JVM 不加载新代码（IDEA/后台进程都一样）。
3. **biz_no 唯一约束必须存 NULL 而非空串** — 空串多条会撞 UNIQUE。对局流水 biz_no=`game:{round_id}:{qq}` 由此幂等。
4. **测试共享 H2 内存库**：同一 context 复用 mem 库自增不重置 → 断言 id 前必须 TRUNCATE（DELETE 不重置自增）；**TRUNCATE 遇 FK 会炸，executor_commands 相关先 DELETE**。
5. **H2 保留字**：`value` 在 H2 中需加引号（schema-h2.sql dict_items 表用 `"value"`）。
6. **pom 的 exec 插件会先构建 frontend** — 仅编译后端用 `mvn -Dskip.fe=true compile/test`；全量 `mvn package` 才含前端构建。
7. **Flyway 不建库** — 新环境先 `CREATE DATABASE hongbaojifen` 再启动。
8. **已执行的迁移不可改** — 后续变更新建 `V1.x.y__描述.sql`，H2 的 schema-h2.sql 同步追加。
9. **TaskStop 只杀 bash 包装进程，java 子进程会残留**（占端口）— `netstat -ano | grep :8892` 找 PID 再 `taskkill //F //PID <pid>`。stop.sh 的按名兜底（pgrep -f）可覆盖大部分情况。
10. **VERSION 文件在 Windows 编辑会带 \r（CRLF）** — start.sh/deploy.sh 已用通配符 + `tr -d '\r'` 防御；手动编辑 VERSION 注意行尾。
11. **运行中后端可能落后于代码/迁移**（血泪）：IDEA 在 V1.0.3 落地前启动的旧进程 → 新 URL 通（controller 按需加载）但新表/列缺失（对局上报 50002）。判断：查 flyway_schema_history 是否到 1.0.4，或 `GET /api/open/groups` 是否带 create_time。修复：taskkill 后重启（Flyway 自动补齐），别急着改代码。
12. **tools/e2e_full.py 需要 8892 起好后端**（本机 MySQL 真实写入）；执行器名/round_id 带时间戳，重复执行不冲突；积分断言用「基线→变动量」而非绝对值（可重复跑）。
13. **agent 停摆态**：执行器被封禁(40310)或操作员被停用/禁手动(40311) → SyncWorker/玩法全部停下，GUI 红字提示；解封后重开 GUI 自动恢复；**若重置过 token 需在 GUI 重填**。红包局在结算成功前留在本地，关 GUI 前先在玩法页「结算」或「作废本局」。
14. **后端当前由 bash 启动（PID 12204）**，IDEA 的旧 run tab 显示 stopped 属正常；要用 IDEA 跑先把 bash 那个 kill 掉。

## 结构速览

- `src/main/java/com/hbjf/api/` — controller（admin 管理端 + open 开放端）/ service（含 RetentionCleanupService 定时清理）/ security / dao（RowMapMapper）/ util（MapBuilder）
- `src/main/resources/db/migration/` — V1__init.sql（9 表）+ V1.0.1(executor_commands)/V1.0.2(玩法种子)/V1.0.3(群封禁·执行器操作员·游戏记录)/V1.0.4(biz_no 加宽)/V1.0.7(红包玩法种子)/V1.0.8(游戏记录回放明细)/V1.0.9(保留天数配置)/V1.0.10(执行器费率)/V1.0.11(删旧玩法种子只留复合玩法)/V1.0.12(登录免责声明字典种子 disclaimer_text)；5/6 号跳号未用。玩法内置种子在 `src/main/resources/seed_rules/rule_fuhe.py`（启动 ensureSeedRules 兜底落盘 data/rules）
- `frontend/src/App.jsx` — 9 菜单（报表默认隐藏：增值服务开关 `REPORT_MENU_ENABLED=false` 在文件顶部，改 true + 重新构建即放出菜单，路由保留可 /report 直达）；**登录免责声明弹窗**：登录后读字典 `disclaimer_text`（`GET /api/admin/dicts?key=`，值非空才弹、不可跳过、确认按钮「我已阅读并确认」关闭；配置管理可编辑，删除行则不再弹）：会员管理 / 报表（`Report.jsx` 只读经营总览：今日手动上分/下分与玩法赢/输四口径分开 + 抽水局数、近7日趋势**今天在最上**、执行器与群展开明细（含未绑定群）、5 个玩家榜 Top10、右侧「当前部署信息」卡（overview.deployment：服务器IP/系统/JDK/端口/环境 + DB 地址与版本）+ 顶部「下载报表」按钮（前端 Blob 生成当日 HTML 日报，文件名带日期），接口 `GET /api/admin/report/overview` → `ReportService`（trend 降序返回、deployment() 读 DataSource metadata + VERSION()））/ QQ群管理 / 操作员管理 / 配置管理 / 执行器管理 / 会员玩法管理 / 游戏记录 / 操作记录 / 用户管理（仅内置超级管理员 admin 可见可管，`AdminUsers.jsx` 增删用户/重置密码）；组件在 `components/`（QqGroupManager 有封禁状态、QqAccountManager 有禁手动、ExecutorManager 有封禁按钮、GameRecordManager 回放）
- `agent/` — 执行器（README + docs/代码架构说明.md + docs/玩法v2结算与上报设计.md 见 agent 侧）；integration/ 对接包独立于 GUI
- `data/rules/` — 玩法文件存储（运行时自动创建）
- `tools/e2e_full.py` — 全链路 E2E（本机）
