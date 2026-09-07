# 红包积分管理系统

会员积分管理系统。会员从 QQ 群拉取，以 **QQ 号作为唯一标识**；系统对外开放接口，供外部程序（执行器 agent）上下分/查积分/上报对局结算。QQ 只有两种身份：**会员**（members，由 agent 从群成员同步）或 **操作员**（管理员登录的管理 QQ，agent 心跳自动登记）；不做普通 QQ 号池。仅管理员一人登录本系统，会员不能登录。

版本迁移当前到 **V1.0.12**（flyway 历史：V1 / 1.0.1 / 1.0.2 / 1.0.3 / 1.0.4 / 1.0.7 / 1.0.8 / 1.0.9 / 1.0.10 / 1.0.11 / 1.0.12，版本号不连续为计划内跳过）。跨会话交接、上线部署注意见 [HANDOFF.md](HANDOFF.md)。

## 技术栈

- 后端：Spring Boot 3.5.3 + JDK 21 + MySQL 8 + Flyway + Spring Security (JWT)
- 前端：React 18 + Ant Design 5 + Vite（PC 管理后台，`/admin/`）
- 端口：后端 8892，前端开发 3002

## 快速启动

```bash
# 1. 建库（MySQL）
mysql -uroot -p -e "CREATE DATABASE IF NOT EXISTS hongbaojifen DEFAULT CHARACTER SET utf8mb4"

# 2. 后端（Flyway 自动建表/迁移，跳过前端构建）
mvn -Dskip.fe=true spring-boot:run -Dspring-boot.run.profiles=local

# 3. 前端开发
cd frontend && npm install && npm run dev
# 浏览器访问 http://localhost:3002/admin/
```

- 默认管理员：`admin / admin123`（首次启动自动创建，**请尽快修改或部署时改 application-prod.yml 相关配置**）
- 打包：`./build.sh`（或 `build.bat`，自动构建前端并打包进 jar），产物 `target/hongbaojifen-api-<版本>.jar`
- 部署：服务器 `/opt/hongbaojifen/` 常驻 `start.sh` / `stop.sh` / `deploy.sh`；发版 = `scp jar → /tmp/` + `ssh /opt/hongbaojifen/deploy.sh`（详见 DEPLOY.md）

## 页面功能（左侧菜单 9 项；「报表」为增值服务，默认隐藏）

| 菜单 | 说明 |
| --- | --- |
| 会员管理 | 会员列表/新增/编辑/删除（有流水不可删）、手动上下分、积分流水抽屉、统计卡片 |
| 报表（增值） | **只读经营总览（非技术口径）**：存量总览（会员/积分存量/群/执行器/操作员）· 今日四口径分开（手动充值上分 / 手动提现下分 / 玩法结算赢 / 玩法结算输）+ 今日抽水/局数/参与玩家/玩法分布 · 近7日趋势（**今天在最上**）· 近N天游戏汇总（受保留期）· 执行器与群明细（展开看群，含未绑定群）· 5 个玩家榜 Top10 · **当前部署信息**（服务器 IP/系统/JDK + 数据库地址与版本）· **下载报表**按钮（把当日全部统计生成一份可留档/打印的 HTML 日报，文件名带日期）。流水永久、游戏数据按 `game_record_retention_days` 保留；抽水口径 = 每局净额 −Σtotal_delta。**当前菜单隐藏**（收费增值服务未开放，代码与路由保留）：`frontend/src/App.jsx` 顶部 `REPORT_MENU_ENABLED` 改为 `true` 并重新构建即放出菜单；已登录用户也可用 URL `/report` 直达 |
| QQ群管理 | 群信息（群号/群名/创建时间/群主/管理员/人数）；状态 **正常/封禁**（附封禁原因）——封禁的群 agent 停同步、停玩法，且不会被 agent 重新上报解封（只能后台手动解封） |
| 操作员管理 | **操作员 QQ**（agent 上报本机登录的管理 QQ）：改备注/停用/**禁手动上下分**（can_manual_points）/删除；显示最近登录时间、来源 IP、主机名 |
| 配置管理 | 通用键值配置（玩法提示词、系统参数；数据保留天数等）。**登录免责声明**：key `disclaimer_text`（V1.0.12 内置 agent 同款 8 条文案），登录成功后弹窗展示、确认后关闭；在此编辑保存后下次登录生效，删除该行则不再弹窗 |
| 执行器管理 | 部署在 QQ 群的 agent：注册即得 token（一次性显示），心跳维持在线；列表含状态/最近心跳/来源 IP/绑定的操作员QQ。操作：编辑/删除/**重置token**/**封禁**/**封禁并重置token**/解封；详情含命令下发与结果（下发用 curl，见下） |
| 会员玩法管理 | 玩法规则文件（Python 源码）：**唯一内置「复合玩法」**（rule_fuhe.py，V1.0.11 收敛全部旧玩法种子），可查看/重传文件、启停；agent 自动拉取并在游戏群运行（复合玩法 = 群内下注/「撑」即可撑庄 + 管理员红包开奖 + agent 面板手动结算上报，玩法见 agent/README「游戏玩法」） |
| 游戏记录 | 游戏对局列表（群/玩法/局号/时间/会员数/净积分/异常数），详情含逐条**回放时间线**；默认保留 30 天（配置键 `game_record_retention_days` 可改） |
| 操作记录 | 登录/上下分/增删改等操作审计（默认保留 30 天，`operation_log_retention_days`） |
| 用户管理 | 后台登录用户：新增/删除/重置密码（内置 admin 标金章、不可删除；密码 BCrypt、用户名唯一）；**仅超级管理员 admin 可见可管**，其余用户接口返回 403 |

> **复合玩法行不要手动删除**：「会员玩法管理」中复合玩法那一行是内置种子（`seed_rules/rule_fuhe.py`）。若在管理页删除该行，agent/服务重启时 `PlayRuleService.ensureSeedRules`（启动兜底）会把它连文件一起复活——这是**预期行为**（保证玩法不丢）。停用玩法请用页面上的启停状态开关，不要删行。

## 对外开放接口（`/api/open/**`，给执行器 agent 调用）

所有请求需带请求头 `X-Api-Key: <app.open.api-key>`（见 `application.yml`，内网可配空关闭校验）。

| 接口 | 说明 |
| --- | --- |
| `POST /api/open/points/up` | 上分 `{qq, points, reason, bizNo?}`；会员不存在自动建档 |
| `POST /api/open/points/down` | 下分 `{qq, points, reason, bizNo?}`；余额不足拒绝 |
| `GET /api/open/points/{qq}` | 查某会员积分（`exists` 区分是否建档） |
| `GET /api/open/points/records?qq=&page=&size=` | 查积分增减记录（分页） |
| `GET /api/open/points/batch?qqs=10001,10002` | 批量查积分（≤500，去重保序；未建档 `exists:false`） |
| `POST /api/open/executor/heartbeat` | 心跳 `{token, host?, version?, admin_qq?, admin_nickname?}`；带 `admin_qq` 时自动登记/续活**操作员**（已存在不改停用/禁手动状态）并更新其最近登录时间/IP，同时更新执行器绑定的操作员QQ。执行器被封禁 → `40310`。**离线判定**：超过 `app.executor-offline-seconds`（默认 30s，agent 心跳默认 10s）未收到心跳自动置离线（查询列表/任意心跳时触发；封禁中不受影响） |
| `POST /api/open/groups` | 上报/更新 QQ 群 `{group_id, create_time, group_name?, owner_qq?, admin_qqs?, member_count?, executor_token?}`（幂等；create_time 为 `yyyy-MM-dd HH:mm:ss` 19 位；上报绑定该执行器为管理执行器）；**封禁中的群不可由上报解封** |
| `GET /api/open/groups?keyword=&status=` | QQ 群列表（status=`active\|banned` 过滤） |
| `POST /api/open/qq-accounts` | 上报/更新**操作员 QQ** `{qq, nickname?, remark?}`（幂等，类型固定为操作员） |
| `GET /api/open/qq-accounts` | 操作员 QQ 列表（含状态/权限/最近登录信息） |
| `DELETE /api/open/qq-accounts/{qq}` | 删除操作员 QQ |
| `POST /api/open/members/batch` | 批量注册会员 `{executor_token, members:[{qq, nickname?, group_id?}]}`（幂等，≤1000/次，非法 QQ 记 skipped）；**注册人 = 执行器绑定的操作员QQ**（仅建档时落 `registrar_qq`，已存在不改） |
| `POST /api/open/games/report` | **对局结算上报**（见下「游戏对局结算」） |
| `POST /api/open/executor/commands/poll` | 执行器拉取命令 `{token}`（原子标记 sent；超时自动重投） |
| `POST /api/open/executor/commands/{id}/result` | 执行器回报 `{token, status: done\|failed, message?}` |
| `GET /api/open/rules` | 启用的玩法规则列表 |
| `GET /api/open/rules/{id}/download` | 下载玩法规则文件 |

管理端（JWT）配套接口：`GET/POST/PUT/DELETE /api/admin/executors`、`POST /api/admin/executors/{id}/reset-token|ban|unban`（ban 请求体可带 `reason`；解封不换 token）、`GET /api/admin/qq-accounts`、`POST/PUT/DELETE /api/admin/qq-accounts`（PUT 可改 `can_manual_points: allowed|denied`）、`POST/GET /api/admin/executors/{id}/commands`、`GET /api/admin/game-records?group_id=&play_name=&page=&size=`、`GET /api/admin/game-records/{id}`（含该局完整回放 events）。

### 幂等约定

- `bizNo` 业务单号：同一单号重复提交不会重复加减（返回 `duplicate: true`）。手动上下分应传唯一单号（单笔流水 biz_no = `game:{round_id}:{qq}` 由系统内部生成）。
- 对局用 `round_id` 幂等：同一局重复上报返回原结果、不重复入账（agent 崩溃重传/重试安全）。

### 游戏对局结算

```
POST /api/open/games/report
{executor_token, round_id(≤96 字符), play_id?, play_name?, group_id?, events:[...]}
events 每条（≤2000/局）：{qq, nickname?, msg?, reply?, delta}   ← 消息级明细
→ {round_id, duplicate, member_count, total_delta, event_count, warning_count, warning}
```

- 首次上报该局 → 建局、逐事件入账并记录回放时间线；`delta>0` 加分 / `<0` 扣分 / `0` 只记过程不建流水。
- **未知会员 / 会员已停用 / 余额不足**：该事件按 `delta=0` 记入时间线并累计进 `warning`（不整局拒绝、不重复扣成功事件）。
- 管理端「游戏记录」可看每局列表与逐条回放；记录默认保留 30 天，`RetentionCleanupService` 定时清理（每 6h，初始延迟 5min）。

### 响应格式

```json
{ "code": 0, "message": "ok", "data": { ... } }
```
错误时 `code` 非 0（40001 参数、40100 未登录、40101 token过期、40102 密码错误、40310 执行器已被封禁、40311 操作员被停用/禁止手动、40400 不存在、40900 积分不足、40901 重复单号、50002 内部错误）。

## 执行器命令通道（远程管理 agent）

管理端通过命令远程控制 agent（配置/会员同步等），执行结果回报可见：

- 状态机 `pending(待取) → sent(已下发待回报) → done/failed(执行完)`；`sent` 超过 **5 分钟**未回报自动重排回 `pending` 重新投递（agent 离线/崩溃后恢复自愈）。**命令必须幂等**，重投最多重复执行一次。
- 去重：同一执行器相同 `command+params` 且未完成时，重复下发返回原命令 id（不重复建行）；每个执行器待执行上限 50 条（超出 40001 拒绝）；7 天前已完成的命令自动清理。
- v1 命令集（agent 端契约）：`get_status` 回报状态 / `set_config {"heartbeat_interval_sec":60,...}` 改 agent 配置（白名单键）/ `sync_members {"group_id":"..."}` 把群成员同步为会员并回报统计。

```bash
# 下发（管理员 JWT）
curl -X POST http://localhost:8892/api/admin/executors/1/commands \
  -H "Authorization: Bearer $ADMIN_TOKEN" -H "Content-Type: application/json" \
  -d '{"command":"sync_members","params":"{\"group_id\":\"123456\"}"}'
# agent 拉取并回报（X-Api-Key + 执行器 token）
curl -X POST http://localhost:8892/api/open/executor/commands/poll \
  -H "X-Api-Key: hbjf-open-2026" -H "Content-Type: application/json" -d '{"token":"<执行器token>"}'
curl -X POST http://localhost:8892/api/open/executor/commands/1/result \
  -H "X-Api-Key: hbjf-open-2026" -H "Content-Type: application/json" \
  -d '{"token":"<执行器token>","status":"done","message":"同步完成"}'
```

admin 前端「执行器管理」详情可见命令列表与结果（管理页面暂不做下发 UI，用接口/curl）。

## 积分规则

- `points` 当前积分；`total_income` 累计上分；`total_outcome` 累计下分
- 下分时余额不足返回 409；上分时会员不存在自动建档（积分从 0 起）
- 积分变动与流水写入同一事务；对局结算按局校验后逐事件入账（见上）

## 目录结构

```
src/main/java/com/hbjf/api/
  controller/   接口层（admin 管理端 + open 开放端：executor/group/qq-account/member/points/games/rules/commands）
  service/      业务层（MemberService 积分核心、ExecutorService 心跳/封禁/操作员绑定、GameRecordService 对局、
                QqAccountService 操作员、RetentionCleanupService 定时清理等）
  security/     JWT 认证 + X-Api-Key 开放接口过滤器
  dao/          RowMapMapper 行映射
  util/         MapBuilder 响应构造
src/main/resources/
  db/migration/ Flyway 迁移（V1__init.sql 建表 + V1.0.1~V1.0.4 / V1.0.7~V1.0.12 增量；5/6 号跳号未用）
  seed_rules/   玩法内置种子（rule_fuhe.py 复合玩法，启动 ensureSeedRules 兜底落盘 data/rules/）
  application*.yml  配置（local 本地 / prod 生产）
frontend/       PC 管理后台（React + AntD，打包进 static/admin）
data/rules/     玩法文件存储目录（自动创建）
tools/e2e_full.py   全链路 E2E 脚本（对本地 MySQL 真实写入，见 HANDOFF.md 坑12）
```

## 开发约定

- API 字段 snake_case；`created_at/updated_at` 为 `yyyy-MM-dd HH:mm:ss` 字符串
- 数据库变更只走 Flyway 迁移（新建 `V1.x.y__描述.sql`），禁止改已执行迁移；H2 测试库同步追加 `src/test/resources/schema-h2.sql`
- 测试：`mvn -Dskip.fe=true test`（跳过前端构建，51 个用例，含命令通道/群封禁/操作员/对局结算/心跳超时离线/玩法种子兜底/报表口径空库与部署信息）
- 全链路验证：`tools/e2e_full.py`（本机后端 8892 + MySQL）；agent 冒烟 `python -m integration.smoke`（见 agent/README.md）
