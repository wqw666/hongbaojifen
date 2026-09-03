# 红包积分管理系统

会员积分管理系统。会员从 QQ 群拉取，以 **QQ 号作为唯一标识**；系统对外开放积分接口，供外部程序（执行器 agent）上分/下分/查积分。仅管理员一人登录使用，会员不能登录本系统。

## 技术栈

- 后端：Spring Boot 3.5.3 + JDK 21 + MySQL 8 + Flyway + Spring Security (JWT)
- 前端：React 18 + Ant Design 5 + Vite（PC 管理后台，`/admin/`）
- 端口：后端 8892，前端开发 3002

## 快速启动

```bash
# 1. 建库（MySQL）
mysql -uroot -p -e "CREATE DATABASE IF NOT EXISTS hongbaojifen DEFAULT CHARACTER SET utf8mb4"

# 2. 后端（Flyway 自动建表，跳过前端构建）
mvn -Dskip.fe=true spring-boot:run -Dspring-boot.run.profiles=local

# 3. 前端开发
cd frontend && npm install && npm run dev
# 浏览器访问 http://localhost:3002/admin/
```

- 默认管理员：`admin / admin123`（首次启动自动创建，**请尽快修改或部署时改 application-prod.yml 相关配置**）
- 打包：`./build.sh`（或 `build.bat`，自动构建前端并打包进 jar），产物 `target/hongbaojifen-api-<版本>.jar`
- 部署：服务器 `/opt/hongbaojifen/` 常驻 `start.sh` / `stop.sh` / `deploy.sh`；发版 = `scp jar → /tmp/` + `ssh /opt/hongbaojifen/deploy.sh`（详见 DEPLOY.md）

## 页面功能（左侧菜单 8 项）

| 菜单 | 说明 |
| --- | --- |
| 会员管理 | 会员列表/新增/编辑/删除（有流水不可删）、手动上下分、积分流水抽屉、统计卡片 |
| 页面操作记录管理 | 登录/上下分/增删改等操作审计 |
| QQ号管理 | 普通 QQ 号池（执行器拉人用资源），支持批量导入 |
| QQ群管理 | 群信息维护（群号/群主/管理员/人数），执行器拉人的目标群 |
| 管理员QQ号管理 | 群内管理/发号用的管理员号 |
| 字典 | 通用键值配置（玩法提示词、系统参数等） |
| 执行器管理 | 部署在 QQ 群的 agent 程序，注册即得 token，心跳维持在线 |
| 会员玩法管理 | 上传玩法规则文件（游戏规则/脚本），执行器拉取后在群里引导会员玩游戏 |

## 对外开放接口（`/api/open/**`，给执行器 agent 调用）

所有请求需带请求头 `X-Api-Key: <app.open.api-key>`（见 `application.yml`，内网可配空关闭校验）。

| 接口 | 说明 |
| --- | --- |
| `POST /api/open/points/up` | 上分 `{qq, points, reason, bizNo?}`；会员不存在自动建档 |
| `POST /api/open/points/down` | 下分 `{qq, points, reason, bizNo?}`；余额不足拒绝 |
| `GET /api/open/points/{qq}` | 查某会员积分（`exists` 区分是否建档） |
| `GET /api/open/points/records?qq=&page=&size=` | 查积分增减记录（分页） |
| `POST /api/open/executor/heartbeat` | 执行器心跳 `{token, host?, version?, group_id?}` |
| `GET /api/open/points/batch?qqs=10001,10002` | 批量查积分（≤500，去重保序；未建档 `exists:false`） |
| `POST /api/open/groups` | 上报/更新 QQ 群 `{group_id, group_name?, owner_qq?, admin_qqs?, member_count?}`（幂等） |
| `GET /api/open/groups` | QQ 群列表（含群号/群名/群主/管理员/人数） |
| `POST /api/open/qq-accounts` | 上报/更新 QQ 号 `{qq, type: admin_qq\|qq, nickname?, remark?}`（幂等） |
| `GET /api/open/qq-accounts?type=admin_qq` | QQ 号列表（按类型） |
| `DELETE /api/open/qq-accounts/{qq}` | 删除 QQ 号 |
| `POST /api/open/members/batch` | 批量注册会员 `{members:[{qq, nickname?, group_id?}]}`（幂等，≤1000/次，非法 QQ 记 skipped） |
| `POST /api/open/executor/commands/poll` | 执行器拉取命令 `{token}`（原子标记 sent；超时自动重投） |
| `POST /api/open/executor/commands/{id}/result` | 执行器回报 `{token, status: done\|failed, message?}` |
| `POST/GET /api/admin/executors/{id}/commands` | 管理端下发/查询执行器命令（JWT；admin 页面执行器管理可见结果） |
| `GET /api/open/rules` | 启用的玩法规则列表 |
| `GET /api/open/rules/{id}/download` | 下载玩法规则文件 |

### 幂等约定

`bizNo` 业务单号：同一单号重复提交不会重复加减（返回 `duplicate: true`）。游戏对局/活动批次应传唯一单号，避免执行器重试导致积分重复发放。不传 bizNo 则不幂等（适合手动操作）。

### 响应格式

```json
{ "code": 0, "message": "ok", "data": { ... } }
```
错误时 `code` 非 0（40001 参数、40100 未登录、40101 token过期、40102 密码错误、40400 不存在、40900 积分不足、40901 重复单号、50002 内部错误）。

## 执行器命令通道（远程管理 agent）

管理端通过命令远程控制 agent（红包管理/会员同步等），执行结果回报可见：

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
- 积分变动与流水写入同一事务

## 目录结构

```
src/main/java/com/hbjf/api/
  controller/   接口层（admin 管理端 + open 开放端）
  service/      业务层（MemberService 积分核心、ExecutorService 心跳、PlayRuleService 文件存储等）
  security/     JWT 认证 + X-Api-Key 开放接口过滤器
  dao/          RowMapMapper 行映射
  util/         MapBuilder 响应构造
src/main/resources/
  db/migration/ Flyway 迁移（V1__init.sql 建表）
  application*.yml  配置（local 本地 / prod 生产）
frontend/       PC 管理后台（React + AntD，打包进 static/admin）
data/rules/     玩法文件存储目录（自动创建）
```

## 开发约定

- API 字段 snake_case；`created_at/updated_at` 为 `yyyy-MM-dd HH:mm:ss` 字符串
- 数据库变更只走 Flyway 迁移（新建 `V1.x.y__描述.sql`），禁止改已执行迁移
- 测试：`mvn -Dskip.fe=true test`（跳过前端构建，26 个用例，含命令通道全链路）
