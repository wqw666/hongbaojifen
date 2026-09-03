# 红包积分管理系统 — 会话交接

> 跨会话续接开发必读。最新一次完整构建验证：2026-08-31（jar 1.0.0 全链路通过）。

## 项目状态

- **v1.0.0 已完成**：后端（Spring Boot 3.5.3/JDK21/MySQL/Flyway）+ 前端（React18/AntD5/Vite）+ 15 个测试全绿 + jar 打包 + 端到端验证通过
- 默认管理员 `admin / admin123`（首次启动自动创建，BCrypt）
- 数据库 `hongbaojifen`（root/19991020），Flyway V1 已执行

## 服务与端口

| 服务 | 端口 | 启动方式 |
| --- | --- | --- |
| 后端 | 8892 | 开发：`mvn -Dskip.fe=true spring-boot:run -Dspring-boot.run.profiles=local` 或 IDE |
| 前端开发 | 3002 | `cd frontend && npm run dev`（proxy /api → 8892） |
| 管理页面 | `/admin/` | 根路径 302 重定向到此 |

**部署架构**（start.sh/stop.sh 只用于 Ubuntu 服务器，不用于开发；build.sh 只打包不上服务器）：
- 本地打包：`./build.sh` 或 `build.bat` → `target/hongbaojifen-api-<V>.jar`
- 服务器 `/opt/hongbaojifen/` 常驻文件：`start.sh`（启动+健康检查，要求 jar 与脚本同级）/ `stop.sh`（PID 停止+按名兜底）/ `deploy.sh`（接收端：停旧→备份→换新→启动）/ `VERSION` / `logs/`
- 发版：`scp jar ubuntu@IP:/tmp/` → `ssh ubuntu@IP /opt/hongbaojifen/deploy.sh`

## 待办（后续迭代方向）

- [ ] 执行器 agent 程序本体（demo：拉群成员 → 建档 → 引导玩游戏 → 调开放接口上下分）
- [ ] 会员批量导入（QQ号 → 会员）联动 QQ号管理池
- [ ] 玩法文件版本管理（历史版本/回滚）
- [ ] 操作日志分页与导出
- [ ] 修改默认管理员密码功能

## 开放接口（agent 对接）

全部在 `/api/open/**`，需 `X-Api-Key: hbjf-open-2026`（application.yml `app.open.api-key`）：

- `POST /api/open/points/up|down` `{qq, points, reason, bizNo?}` — 上分不存在自动建档；下分余额不足 409
- `GET /api/open/points/{qq}`、`GET /api/open/points/records?qq=&page=&size=`
- `POST /api/open/executor/heartbeat` `{token, host?, version?, group_id?}` — token 在管理端建执行器时一次性显示
- `GET /api/open/rules`、`GET /api/open/rules/{id}/download` — 仅 active 玩法可拉取

**幂等**：bizNo 有 UNIQUE 约束，重复提交返回 `duplicate:true`；不传 bizNo 存 NULL 不参与唯一约束。

## 坑与经验（重要）

1. **中文禁止 bash 直接传**（GBK 乱码 → JSON 解析失败 50002）— 写 UTF-8 文件再 `curl -d @file`。这就是验证第 1 次失败的根因，不是代码 bug。
2. **改 Java 代码必须重启应用** — 运行中 JVM 不加载新代码（IDEA/后台进程都一样）。
3. **biz_no 唯一约束必须存 NULL 而非空串** — 空串多条会撞 UNIQUE（曾导致第二次手动上下分 409）。V1__init.sql 中该列 `NULL DEFAULT NULL`。
4. **测试共享 H2 内存库**：不同测试类同一 context 配置会复用同一个 mem 库，自增不重置 → 测试断言 id 前必须 `TRUNCATE TABLE`（DELETE 不重置自增）。
5. **H2 保留字**：`value` 在 H2 中需加引号（schema-h2.sql dict_items 表用 `"value"`）。
6. **pom 的 exec 插件会先构建 frontend** — 仅编译后端用 `mvn -Dskip.fe=true compile/test`；全量 `mvn package` 才含前端构建。
7. **Flyway 不建库** — 新环境先 `CREATE DATABASE hongbaojifen` 再启动。
8. **V1__init.sql 尚未执行时可直接改**（本会话已执行过，后续变更必须新建 V1.x.y 迁移）。
9. **TaskStop 只杀 bash 包装进程，java 子进程会残留**（占端口）—— 用 `netstat -ano | grep :8892` 找 PID 再 `taskkill //F //PID <pid>`。stop.sh 的按名兜底（pgrep -f）可覆盖大部分情况。
10. **VERSION 文件在 Windows 编辑会带 \r（CRLF）** → `cat VERSION` 得 `1.0.0\r`，拼文件名找不到 jar（用户踩过）。start.sh/deploy.sh 已改为通配符 `hongbaojifen-api-*.jar` + `ls -t | head -1` 取最新 + `tr -d '\r'` 防御，不再依赖 VERSION 与文件名强一致。手动操作 VERSION 时注意行尾。

## 结构速览

- `src/main/java/com/hbjf/api/` — controller（admin 管理端 / open 开放端）/ service / security（JwtAuthFilter + OpenApiKeyFilter）/ config
- `src/main/resources/db/migration/V1__init.sql` — 9 张表
- `frontend/` — 8 个菜单页：会员管理（核心）、操作记录、QQ号、QQ群、管理员QQ号、字典、执行器、玩法文件
- `data/rules/` — 玩法文件存储（运行时自动创建）
- 测试 `src/test/` — AuthFlowTest / PointFlowTest / OpenApiTest（15 用例）
