# 红包积分管理系统 — 会话交接

> 跨会话续接开发必读。最近一次完整验证：**2026-09-13**（积分来源分离：后端 64 测试全绿 + agent selftest 76 / integration 32 全绿 + play_engine ✓ + 前端 build ✓）。
>
> **2026-09-13 积分来源分离（迁移 V1.0.14）**：上分下分与结算得分失分分开——会员页此前只能靠 `reason` 文案区分「操作改分」与「游戏结算改分」，流水也混在一起。现在 `point_records.source` 落三类值：**manual 后台手动 / approve 群内审批 / game 玩法结算**（历史回填 `玩法:%`→game、`biz_no LIKE 'approve:%'`→approve、其余 manual；并补齐 V1.0.13 之后手动流水漏掉的 `flow_amount`）。① 写入侧：`MemberService.adjustPoints` 新增 5 参重载（`source` 白名单，非 approve 一律归 manual，**外部不能伪造 game**）；`OpenPointController` up/down 读可选 `source`（agent 审批传 approve）；`GameRecordService` 结算恒写 `source='game'`。② 读取侧：会员列表每行附 6 个来源聚合（`manual_income/manual_outcome/approve_income/approve_outcome/game_income/game_outcome/game_flow`，按「会员×来源」`GROUP BY` 实时聚合，不落计数列）；`/api/admin/point-records` 加 `source` 过滤并返回 `summary`（**全量口径，不随筛选变化**）。③ 展示：会员列表加 **手动上分/手动下分/玩法得分/玩法失分** 四列（手动 = 后台手动 + 群内审批，悬浮看拆分，保留累计上下分对账）；流水抽屉加合计卡（手动上分/下分、玩法得分/失分、玩法流水额）+ 来源筛选标签（全部/手动/审批/玩法）+ 类型细分 7 种标签（手动上分·下分 / 审批上分·下分 / 结算得分·失分 / 流水）。④ **报表口径改读 source**（`ReportService` 4 处 `reason LIKE '玩法:%'` 全部换掉）：手动 = 非 game（含 approve）、玩法 = game，显示口径不变但不再依赖文案约定。⑤ **agent 侧修正**：`integration/backend_client.py::up_points/down_points` 的 `source` 从写死的 `approve` 改成参数（默认 `manual`）——两个调用点此前共用同一个写死值，**agent 会员页的手动上/下分会被误记成「群内审批」**；现在 `approve_tab.py::_process_selected` 显式传 `approve`、`main_window.py::_members_manual` 显式传 `manual`，APP_VERSION → `2026.09.13-03`（此修复必须重新分发 agent 才生效）。测试：新增 `PointSourceSeparationTest`（7 用例：三类来源落库、伪造 game 被归一、列表聚合、summary 与过滤、报表按 source 而非 reason）+ `GameSettlementScenariosTest`（5 用例 / 8 局剧本：撑庄三方·四方·平局、大吃小两方·三方、未知会员跳过、余额不足跳过；逐局断言 Σdelta = −抽水、source=game、群计数累加、撑庄两侧流水对等、平局 FLOW 行不动分）。
>
> **2026-09-08 玩法播报迭代（R8）**：玩法群公告一律**普通消息、不带 @全体成员**（插件层移除前缀与 at-all，一处覆盖开局/停结/结算/作废）；开局只发 `————开始————` 一条横幅；停结 = `————停结————` / 玩法行（`本局玩法：撑庄（庄家：X）` 或 `本局玩法：大吃小`）/ `合计 N 分` 三段 + 汇总表图；成员下注只回当前这笔金额数字（@ 引擎带）；结算表加「红包金额」列（后台改/补值行标 `（代填）`），撑庄尾行 = `撑 甲（红包0.40 点4）：结算 -10`（未抢 = 红包0.00 点0）；总后台游戏记录列「净变动」改名「抽水」（恒显示正数绿色）。验证：agent 玩法 selftest 71 断言绿 + integration 30 绿 + 插件重建 0 处「全体成员」+ `frontend && npm run build` ✓；**后端 Java 零改动**（前端改样需重新 `mvn package` 进 jar 才生效）。R7 同批玩法语义（局号纯数字/封盘结算表格图/回放记录管理员改开奖）见 agent/docs/代码架构说明.md §8。
>
> **2026-09-13 群经营数据 + 流水额与积分分离（迁移 V1.0.13）**：① 群列表新增三个经营数字——**积分剩余**（群内会员积分合计，实时聚合）、**累计抽水**、**总对局数**。**为什么累计值存数字而不是每次聚合**：`game_records` 走 30 天保留策略（`RetentionCleanupService` 每 6h 清一次），纯实时聚合的「累计」会随时间缩水 → 累计落库为 `qq_groups.game_count`/`rake_total`（结算首次入账时累加、按现有记录回填），口径标准但会缩水的「近 30 天」才实时聚合（保留期内两者一致）。② `point_records`/`game_record_events` 增 `flow_amount`（历史回填 = `delta`）；`game_records(group_id, created_at)` 加索引。③ 玩法 `rule_fuhe.py::_settle_qzz` 结算事件新增 `flow`：挑战者 `下注//2`、庄家 = 挑战者之和（**恒正、两侧对等**，下注 100 → 玩家侧 50 + 庄家侧 50；平局 `delta=0` 也记流水），**积分仍按 `delta` 全额结算**；大吃小不带 `flow`（后端按 `flow=delta` 兜底）；agent `redpacket_game._apply_batch_settle` 只在玩法给了 `flow` 时才下发该键（不给 ≠ 传 0）。④ 展示：agent「群管理」表加三列（`累计（近30天 X）`，点「刷新」更新，未上报到总后台的群为空）、总后台「QQ群管理」同三列（悬浮看近 30 天）+ **点群名开成员抽屉**（群统计 + 成员明细，`/api/admin/members?group_id=`）、「游戏记录」页头「总抽水（当前筛选）」+ 回放里事件级流水额。验证：后端 52 用例全绿、agent selftest 76 + integration 32 全绿、play_engine ✓、前端 build ✓；**顺手修**：`SeedPlayRuleTest` 写死的种子字节数（32707）早在 72b7b3b 改玩法文件时就失效，已改为**四副本逐字节一致性守卫**（改玩法文件不同步会立刻失败）。
>
> **2026-09-12**：`start.sh` 与**新增的 `start.bat`**（Windows 版，纯 ASCII+CRLF）都加了**启动前端口占用检查**——8892 被旧实例/残留 java 进程占用时先结束占用者再启动，重复执行即重启（详见「坑与经验」9）。jar + agent.exe 重新打包时**发现 `target/` 里的旧 jar 三份 `application-*.yml` 全是旧值（password 123456 ≠ 源码 19991020）**，会导致启动 `Access denied`；`mvn clean package` 重建后与源码逐字节一致，`start.bat` 端到端 `[ready]` 通过（详见「坑与经验」15）。

## 项目状态

- **v1.0.4 功能集已完成**（后端 Spring Boot 3.5.3/JDK21/MySQL8/Flyway + 前端 React18/AntD5/Vite + agent 执行器）
- 本迭代 = Request B 8 项：QQ群管理+创建时间/状态(封禁)；**删除普通QQ号管理**（QQ 只是 member 或 操作员）；执行器**封禁**（仅封禁 / 封禁并重置token）；执行器详情=最近心跳IP/绑定的操作员QQ；删 executors 负责群号列（一执行器多群）；**游戏记录**（agent 上报对局回放，默认保留 30 天，保留天数配置键可改）；操作员**禁手动**（can_manual_points，禁手动不禁自动）；完整流程（心跳带 admin_qq 自动登记操作员 → 群/会员同步 → 玩法 → 结算上报 → 后端实时入账，人工审核不需要——后端自动校验）
- 默认管理员 `admin / admin123`（首次启动自动创建，BCrypt）
- 数据库 `hongbaojifen`（root/19991020），flyway_schema_history：V1/1.0.1/1.0.2/1.0.3/1.0.4/1.0.7~1.0.14 全部 success（5/6 号跳号）
- **不做红包金额↔积分自动挂钩**；玩法 delta 由玩法文件返回、对局结算时入账

## 服务与端口

| 服务 | 端口 | 启动方式 |
| --- | --- | --- |
| 后端 | 8892 | **当前实例 = `start.bat` 启动的 java 进程（PID 18892，2026-09-13 23:13 重建的 jar，已跑 V1.0.14 迁移）**；日志 `logs/app.log`；重复执行 `start.bat` 即重启（自动杀占用者）。**注意 `start.bat` 优先取仓库根目录的 `hongbaojifen-api-*.jar`**（server 风格），根目录那份必须与 `target/` 同步——2026-09-13 就是根目录的旧 jar（09-12）盖住了新构建，导致启动后 Flyway 停在 1.0.12（见「坑与经验」16）。**在 Git Bash 里调 `start.bat` 必须写 `.\start.bat`**（`cmd //c "cd /d D:\work\hongbaojifen && start.bat local"` 会报「不是内部或外部命令」），且脚本末尾有 `pause`（`%cmdcmdline%` 含 start.bat 时触发）→ 用 `< /dev/null` 或后台跑 |
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
- **积分与流水分离**（V1.0.13）：事件可带 `flow?`（流水额，缺省 = `delta`）；撑庄模式流水按「下注半额」恒正两侧对等记（积分仍全额按 `delta`），平局 `delta=0` 但记 `type=FLOW` 流水；群计数 `qq_groups.game_count`/`rake_total` 在**首次**入账时累加（重复上报不重复计数），供群列表「累计」列（不随 30 天记录清理缩水）
- **积分来源分离**（V1.0.14）：`point_records.source` = `manual`（后台手动，含 open 接口不传 source 的历史语义）/ `approve`（群内审批，agent `up_points`/`down_points` 传 `source:"approve"`）/ `game`（对局结算，`GameRecordService` 恒写）；白名单外（含伪造 `game`）一律归一 `manual`。读取侧：会员列表 6 个来源聚合列（`manual_income/outcome`、`approve_income/outcome`、`game_income/outcome`、`game_flow`，按「会员×来源」实时 `GROUP BY`）+ `/api/admin/point-records?source=` 过滤与 `summary`（全量口径，不随筛选变）。**报表口径改读 source**（`ReportService.SRC_MANUAL`/`SRC_GAME` 常量，用 `COALESCE(source,'manual')` 兜历史 NULL）：手动 = 非 game（含 approve）、玩法 = game —— 显示口径与 V1.0.13 一致，但不再依赖 `reason` 文案
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
- `POST /api/open/games/report` `{executor_token, round_id, play_id?, play_name?, group_id?, events:[{qq,nickname?,msg?,reply?,delta,flow?}]}` → `{round_id, duplicate, member_count, total_delta, event_count, warning_count, warning}`；`flow` 只记流水额不动积分（缺省 = delta）
- `GET /api/open/groups` 每行含 `points_total`/`game_count`/`rake_total`（累计，计数列）+ `game_count_30d`/`rake_total_30d`（近 30 天实时聚合）
- `GET /api/admin/game-records?group_id=&play_name=&page=&size=`（`data.rake_total` = 当前筛选总抽水）+ `GET /api/admin/game-records/{id}`（含 events 回放 + `flow_amount`；record 带 operator_qq/executor_name/round_id）+ `GET /api/admin/members?group_id=`（群管理成员抽屉）
- 管理端 qq-accounts PUT 可改 `can_manual_points`（allowed/denied）；executors ban/unban/reset-token
- 错误码新增：`40310 执行器已被封禁`、`40311 操作员被停用/禁止手动`

## 测试与验证手段

- 后端：`mvn -Dskip.fe=true test`（64 用例 = 原有 52 + `PointSourceSeparationTest` 7 + `GameSettlementScenariosTest` 5；测试用 H2 共享 mem 库，断言 id 前先 `TRUNCATE TABLE`；H2 建表脚本 `src/test/resources/schema-h2.sql` 是**手写维护**的，加列要同步改，否则新用例全挂）
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
9. **TaskStop 只杀 bash 包装进程，java 子进程会残留**（占端口）— `netstat -ano | grep :8892` 找 PID 再 `taskkill //F //PID <pid>`。stop.sh 的按名兜底（pgrep -f）可覆盖大部分情况；**服务器 `start.sh` 现已自带端口清理**（2026-09-12）：启动前探测 8892 占用（ss→lsof→fuser，兜底 pgrep 按 jar 名）→ 先结束占用者（TERM 15s→KILL）并等端口释放 → 再启动，重复执行即重启，不会出现 Port already in use 启动失败。**Windows 侧 `start.bat`（2026-09-12 新增，仓库根）**同样自带端口清理：netstat 解析 8892 LISTENING 的 PID（去重）→ `taskkill /F /T` → 轮询等释放（最多 10 轮）→ 再启动；实测重复执行即重启（`[clean] port 8892 occupied by PIDs: … - killing` → `[ready]`）。
10. **VERSION 文件在 Windows 编辑会带 \r（CRLF）** — start.sh/deploy.sh 已用通配符 + `tr -d '\r'` 防御；手动编辑 VERSION 注意行尾。
11. **运行中后端可能落后于代码/迁移**（血泪）：IDEA 在 V1.0.3 落地前启动的旧进程 → 新 URL 通（controller 按需加载）但新表/列缺失（对局上报 50002）。判断：查 flyway_schema_history 是否到 1.0.4，或 `GET /api/open/groups` 是否带 create_time。修复：taskkill 后重启（Flyway 自动补齐），别急着改代码。
12. **tools/e2e_full.py 需要 8892 起好后端**（本机 MySQL 真实写入）；执行器名/round_id 带时间戳，重复执行不冲突；积分断言用「基线→变动量」而非绝对值（可重复跑）。
13. **agent 停摆态**：执行器被封禁(40310)或操作员被停用/禁手动(40311) → SyncWorker/玩法全部停下，GUI 红字提示；解封后重开 GUI 自动恢复；**若重置过 token 需在 GUI 重填**。红包局在结算成功前留在本地，关 GUI 前先在玩法页「结算」或「作废本局」。
14. **后端本机启停统一走仓库根 `start.bat`**（2026-09-12 起；服务器用 `start.sh`）——启动前自动清理 8892 占用者，重复执行即重启；要用 IDEA 跑先跑一次 `start.bat`（或先 taskkill 占用 8892 的 PID）。
15. **打包产物必须校验**（2026-09-12 血泪）：`target/` 里的 jar 曾出现三份 `application-*.yml`（base/local/prod）**全是旧值**——password 123456，而 `src/main/resources` 与 `target/classes` 都是 19991020 → 启动 `Access denied for user 'root'@'localhost'`、健康检查永远不 `[ready]`；该 jar 时间戳与构建记录对不上，来源无法复原。**教训：交付/发版前别信 `target/` 里的既有 jar**——用 python zipfile 比对 jar 内 `BOOT-INF/classes/application*.yml` 与源码的 md5（顺带 `unzip -p … 'static/admin/assets/*.js' | grep -c 抽水` 确认前端是新的），不一致就 `mvn clean package -Drevision=1.0.0 -DskipTests` 重打。
16. **根目录的 jar 会盖住新构建**（2026-09-13 血泪）：`start.bat` 选 jar 的顺序是「本目录优先，其次 `target\`」（第 35-44 行，照顾服务器风格），仓库根长期留着一份 `hongbaojifen-api-1.0.0.jar`（09-12 的旧产物）→ `mvn clean package` 出来的新 jar 在 `target/` 根本没被启动，日志显示 Flyway「Current version 1.0.12, up to date. No migration necessary」（新迁移压根不在 jar 里）。**判断**：`ls -l --time-style=+%m-%d_%H:%M hongbaojifen-api-*.jar target/hongbaojifen-api-*.jar` + `md5sum` 比两份；**处理**：`cp -f target/hongbaojifen-api-1.0.0.jar .` 覆盖根目录那份（或删掉根目录那份）再跑 `start.bat`；启动成功的标志是日志出现 `Migrating schema ... to version "1.0.13"` + `Successfully applied 1 migration`。
17. **累计抽水可能是负数，是历史数据不是算错**：口径 `抽水 = Σ(-total_delta)`（与报表页、README 一致），真实牌局 Σdelta = −抽水 ≤ 0 → 抽水恒正；但库里有两类**非牌局**历史记录 Σdelta 为正——2026-09-05 的单人测试局（id 6~15，`红包玩法1`/`复合玩法1`，total_delta +6~+15）和 `tools/e2e_full.py` 造的 E2E 局（每条 +7/+2，纯算术测试）。它们会把累计抽水拉低甚至变负（如群 1121550065 显示 -36、E2E 群 -28）。**新产生的真实牌局一切正常**（2026-09-13 live 校验：撑庄局 delta +80/0/-90、flow 50/25/75 → Σdelta -10 → 群 rake_total +10 ✓）。若日后想让「累计抽水」永不为负，改口径为 `SUM(CASE WHEN total_delta < 0 THEN -total_delta ELSE 0 END)`（迁移 + `GameRecordService` 累加处同步改），但会与「游戏记录」页每局显示的口径脱钩，需用户拍板。
18. **`-Dskip.fe=false` 仍会跳过前端构建**（2026-09-13 血泪）：pom 里跳过前端是**激活式 profile**（`<activation><property><name>skip.fe</name></property>`，第 165-172 行）——Maven 的 property 激活只看「属性是否存在」，不看值，所以 `-Dskip.fe=false` 反而把 profile 点亮了 → `frontend.skip=true` → vite 不跑、`copy-frontend-dist` 也不拷，打出来的 jar **没有 `BOOT-INF/classes/static/admin/`**（体积小 ~400KB，页面 404）。**要带前端就完全别传这个属性**：`mvn clean package -Drevision=1.0.0 -DskipTests`；打完必看三样——日志有 `vite build` + `Copying 2 resources from frontend\dist`、`unzip -l … | grep static/admin/index.html`、jar 内 `application*.yml` 与源码逐字节一致。
19. **一个 client 方法服务两个语义相反的调用点 = 迟早串味**（2026-09-13）：`backend_client.up_points/down_points` 曾把 `source="approve"` **写死在函数体里**，但调用它的有两处——积分审批 tab（该 approve）和会员页手动上/下分（该 manual）→ agent 里手动改分会被记成「群内审批」，总后台来源列全错。**教训**：语义参数不写死，改成带默认值的形参（默认取「最保守/最常见」的那个），**每个调用点显式传值**（`approve_tab` 传 `approve`、`main_window` 传 `manual`），并在架构文档里点名这两处；验证用**拦截 `_post` 断言请求体**（不发真实请求），比读代码可靠。
20. **Git Bash 里调 `.bat` 必须带 `.\` 或先 `cd /d`**（2026-09-13 二次踩）：`cmd //c "build.bat"` → 「'build.bat' 不是内部或外部命令」且**退出码仍是 0**（`; echo $?` 取的是后一条命令的），很容易误判成「构建成功」。可用写法：`cmd //c "cd /d D:\work\hongbaojifen\agent && .\build.bat" > log 2>&1 < /dev/null`（末尾 `pause` 靠 `< /dev/null` 直接返回）。**判据**：看产物时间戳/md5，别信退出码。

## 结构速览

- `src/main/java/com/hbjf/api/` — controller（admin 管理端 + open 开放端）/ service（含 RetentionCleanupService 定时清理）/ security / dao（RowMapMapper）/ util（MapBuilder）
- `src/main/resources/db/migration/` — V1__init.sql（9 表）+ V1.0.1(executor_commands)/V1.0.2(玩法种子)/V1.0.3(群封禁·执行器操作员·游戏记录)/V1.0.4(biz_no 加宽)/V1.0.7(红包玩法种子)/V1.0.8(游戏记录回放明细)/V1.0.9(保留天数配置)/V1.0.10(执行器费率)/V1.0.11(删旧玩法种子只留复合玩法)/V1.0.12(登录免责声明字典种子 disclaimer_text)/V1.0.13(群累计对局与累计抽水计数列 + point_records/game_record_events 流水额 flow_amount + game_records(group_id,created_at) 索引)/V1.0.14(point_records.source 来源列 + 历史回填 + flow_amount 补齐 + (member_id,source) 索引)；5/6 号跳号未用。玩法内置种子在 `src/main/resources/seed_rules/rule_fuhe.py`（启动 ensureSeedRules 兜底落盘 data/rules）
- `frontend/src/App.jsx` — 9 菜单（报表默认隐藏：增值服务开关 `REPORT_MENU_ENABLED=false` 在文件顶部，改 true + 重新构建即放出菜单，路由保留可 /report 直达）；**登录免责声明弹窗**：登录后读字典 `disclaimer_text`（`GET /api/admin/dicts?key=`，值非空才弹、不可跳过、确认按钮「我已阅读并确认」关闭；配置管理可编辑，删除行则不再弹）：会员管理 / 报表（`Report.jsx` 只读经营总览：今日手动上分/下分与玩法赢/输四口径分开 + 抽水局数、近7日趋势**今天在最上**、执行器与群展开明细（含未绑定群）、5 个玩家榜 Top10、右侧「当前部署信息」卡（overview.deployment：服务器IP/系统/JDK/端口/环境 + DB 地址与版本）+ 顶部「下载报表」按钮（前端 Blob 生成当日 HTML 日报，文件名带日期），接口 `GET /api/admin/report/overview` → `ReportService`（trend 降序返回、deployment() 读 DataSource metadata + VERSION()））/ QQ群管理 / 操作员管理 / 配置管理 / 执行器管理 / 会员玩法管理 / 游戏记录 / 操作记录 / 用户管理（仅内置超级管理员 admin 可见可管，`AdminUsers.jsx` 增删用户/重置密码）；组件在 `components/`（QqGroupManager 有封禁状态 + 积分剩余/累计抽水/总对局三列 + 点群名开成员抽屉、QqAccountManager 有禁手动、ExecutorManager 有封禁按钮、GameRecordManager 回放 + 页头总抽水、MemberManager 手动/玩法四列来源拆分 + 流水抽屉合计卡与来源筛选）
- `agent/` — 执行器（README + docs/代码架构说明.md + docs/玩法v2结算与上报设计.md 见 agent 侧）；integration/ 对接包独立于 GUI
- `data/rules/` — 玩法文件存储（运行时自动创建）
- `tools/e2e_full.py` — 全链路 E2E（本机）
