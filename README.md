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
| `GET /api/open/rules` | 启用的玩法规则列表 |
| `GET /api/open/rules/{id}/download` | 下载玩法规则文件 |

### 幂等约定

`bizNo` 业务单号：同一单号重复提交不会重复加减（返回 `duplicate: true`）。游戏对局/活动批次应传唯一单号，避免执行器重试导致积分重复发放。不传 bizNo 则不幂等（适合手动操作）。

### 响应格式

```json
{ "code": 0, "message": "ok", "data": { ... } }
```
错误时 `code` 非 0（40001 参数、40100 未登录、40101 token过期、40102 密码错误、40400 不存在、40900 积分不足、40901 重复单号、50002 内部错误）。

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
- 测试：`mvn -Dskip.fe=true test`（跳过前端构建，15 个用例）
