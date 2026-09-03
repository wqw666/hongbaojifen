#!/usr/bin/env bash
# ==========================================
# 红包积分管理系统 启动脚本（Ubuntu 服务器专用，/opt/hongbaojifen/）
#   用法: ./start.sh [prod|local]    默认 prod
#   环境变量 PROFILE 也可指定 profile
#   要求: 与本脚本同级的 hongbaojifen-api-<版本>.jar
#   日志: logs/app.log  PID: app.pid
# ==========================================
cd "$(dirname "$0")"

# VERSION 仅用于日志显示（可选文件，缺失不阻断启动）
if [ -f VERSION ]; then V=$(tr -d '\r' < VERSION); else V="?"; fi
# 通配符匹配：不依赖 VERSION 与 jar 文件名强一致（多版本并存时取最新）
JAR=$(ls -t hongbaojifen-api-*.jar 2>/dev/null | head -1)
if [ -z "$JAR" ]; then
    echo "[错误] 未找到 hongbaojifen-api-*.jar（与脚本同级目录），请用本地 build.sh 打包后 scp 上传"
    exit 1
fi

# 已在运行则拒绝重复启动
if [ -f app.pid ]; then
    PID=$(cat app.pid)
    if kill -0 "$PID" 2>/dev/null; then
        echo "[错误] 应用已在运行 (PID=$PID)，如需重启请先 ./stop.sh"
        exit 1
    fi
    rm -f app.pid
fi

PROFILE=${PROFILE:-${1:-prod}}
mkdir -p logs

command -v java >/dev/null 2>&1 || { echo "[错误] 未找到 java，请安装 JDK 21 (sudo apt install openjdk-21-jre)"; exit 1; }

nohup java -Xms256m -Xmx512m -jar "$JAR" --spring.profiles.active="$PROFILE" > logs/app.log 2>&1 &
echo $! > app.pid
echo "[启动] PID=$(cat app.pid)  profile=$PROFILE  日志=logs/app.log"

# 等待健康检查（最多 60 秒）
for i in $(seq 1 30); do
    if curl -s -o /dev/null http://localhost:8892/health 2>/dev/null; then
        echo "[就绪] http://localhost:8892/health ok"
        exit 0
    fi
    sleep 2
done
echo "[警告] 启动超过 60 秒未就绪，请查看 logs/app.log"
exit 1
