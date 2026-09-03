# 部署速查（红包积分管理系统）

## 本地开发

```bash
# 1. 建库
"/c/Program Files/MySQL/MySQL Server 8.0/bin/mysql.exe" -uroot -p -e "CREATE DATABASE IF NOT EXISTS hongbaojifen DEFAULT CHARACTER SET utf8mb4"

# 2. 后端（8892，Flyway 自动建表 + 自动创建管理员 admin/admin123）
mvn -Dskip.fe=true spring-boot:run -Dspring-boot.run.profiles=local

# 3. 前端（3002，访问 http://localhost:3002/admin/）
cd frontend && npm install && npm run dev
```

## 生产打包部署

```bash
# 1. 本地打包（自动 npm install + build 前端 → 打进 jar → 跑测试）
./build.sh           # 或 build.bat（Windows）；产物 target/hongbaojifen-api-<ver>.jar

# 2. 首次部署：初始化服务器目录（一次性）
ssh ubuntu@<IP> "mkdir -p /opt/hongbaojifen/logs && mysql -uroot -p -e \"CREATE DATABASE IF NOT EXISTS hongbaojifen DEFAULT CHARACTER SET utf8mb4\""
scp start.sh stop.sh deploy.sh VERSION ubuntu@<IP>:/opt/hongbaojifen/

# 3. 发版（jar 上传到 /tmp，deploy.sh 负责停旧→备份→换新→启动）
scp target/hongbaojifen-api-<ver>.jar ubuntu@<IP>:/tmp/
ssh ubuntu@<IP> /opt/hongbaojifen/deploy.sh
```

服务器上管理：`./start.sh` 启动 / `./stop.sh` 停止（日志 `logs/app.log`，健康检查 `http://localhost:8892/health`）。

生产环境注意：
- `application-prod.yml` 中改 `app.jwt.secret` 为强随机值、`app.open.api-key` 为执行器共享密钥
- 玩法文件目录默认 `/opt/hongbaojifen/data/rules/`（自动创建），确认进程有写权限
- 建议 Nginx 反代 8892：`/` 会 302 到 `/admin/`；SPA 深层路由（`/admin/xxx`）后端已做 fallback
- 开放接口（agent 用）固定前缀 `/api/open/**`，需 `X-Api-Key` 请求头

## 验证

```bash
curl http://localhost:8892/health                                   # {"status":"ok"}
curl -X POST http://localhost:8892/api/auth/login -H "Content-Type: application/json" \
     -d '{"username":"admin","password":"admin123"}'                 # 返回 token
curl -X POST http://localhost:8892/api/open/points/up \
     -H "X-Api-Key: <api-key>" -H "Content-Type: application/json" \
     -d '{"qq":"10000000","points":10,"reason":"test"}'              # 上分（中文务必用 UTF-8 文件传）
```

## 数据库变更

Flyway 迁移：新建 `src/main/resources/db/migration/V1.x.y__描述.sql`（禁止改已执行文件）。改表结构后测试的 `src/test/resources/schema-h2.sql` 需同步。
