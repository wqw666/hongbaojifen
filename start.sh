#!/usr/bin/env bash
# ==========================================
# 红包积分管理系统 启动脚本（Ubuntu 服务器专用，/opt/hongbaojifen/）
#   用法: ./start.sh [prod|local]    默认 prod
#   环境变量 PROFILE 也可指定 profile
#   要求: 与本脚本同级的 hongbaojifen-api-<版本>.jar
#   日志: logs/app.log  PID: app.pid
#   端口: 8892 —— 启动前自动检查占用并结束占用者（旧实例/残留 java 进程），再启动
# ==========================================
cd "$(dirname "$0")"

PORT=8892

# ---------- 端口/进程探测 ----------
# 端口是否被监听
port_busy() {
    if command -v ss >/dev/null 2>&1; then
        ss -lnt "sport = :$PORT" 2>/dev/null | tail -n +2 | grep -q .
    elif command -v lsof >/dev/null 2>&1; then
        [ -n "$(lsof -ti "tcp:$PORT" -sTCP:LISTEN 2>/dev/null)" ]
    elif command -v netstat >/dev/null 2>&1; then
        netstat -lnt 2>/dev/null | grep -q ":$PORT "
    else
        return 1
    fi
}

# 占用 $PORT 或属于本项目的进程号（多路探测取并集）
port_pids() {
    if command -v ss >/dev/null 2>&1; then
        ss -lptn "sport = :$PORT" 2>/dev/null | grep -oE 'pid=[0-9]+' | cut -d= -f2
    fi
    if command -v lsof >/dev/null 2>&1; then
        lsof -ti "tcp:$PORT" -sTCP:LISTEN 2>/dev/null
    fi
    if command -v fuser >/dev/null 2>&1; then
        fuser "$PORT/tcp" 2>/dev/null | tr ' ' '\n'
    fi
    # 兜底：按进程名找本项目（ss/lsof/fuser 不可用或无权限查看占用者时）
    if command -v pgrep >/dev/null 2>&1; then
        pgrep -f "hongbaojifen-api.*\.jar" 2>/dev/null
    else
        ps -ef 2>/dev/null | grep "hongbaojifen-api.*\.jar" | grep -v grep | awk '{print $2}'
    fi
}

# 先 TERM 优雅退出（最多 15 秒），超时 KILL
kill_one() {
    local PID=$1
    kill -0 "$PID" 2>/dev/null || return 0
    echo "[清理] 结束占用进程 PID=$PID ($(ps -p "$PID" -o args= 2>/dev/null | cut -c1-90))"
    kill "$PID" 2>/dev/null
    for i in $(seq 1 15); do
        kill -0 "$PID" 2>/dev/null || return 0
        sleep 1
    done
    echo "[清理] PID=$PID 优雅退出超时，强制结束"
    kill -9 "$PID" 2>/dev/null
    sleep 1
}

# VERSION 仅用于日志显示（可选文件，缺失不阻断启动）
if [ -f VERSION ]; then V=$(tr -d '\r' < VERSION); else V="?"; fi
# 通配符匹配：不依赖 VERSION 与 jar 文件名强一致（多版本并存时取最新）
JAR=$(ls -t hongbaojifen-api-*.jar 2>/dev/null | head -1)
if [ -z "$JAR" ]; then
    echo "[错误] 未找到 hongbaojifen-api-*.jar（与脚本同级目录），请用本地 build.sh 打包后 scp 上传"
    exit 1
fi

# ---------- 启动前清理：端口占用 / 旧实例（含 app.pid 残留） ----------
OLD_PIDS=$( { port_pids; [ -f app.pid ] && cat app.pid; } 2>/dev/null \
            | grep -E '^[0-9]+$' | sort -u )
if [ -n "$OLD_PIDS" ]; then
    if port_busy; then
        echo "[清理] 端口 $PORT 被占用（旧实例/残留进程），先结束再启动"
    else
        echo "[清理] 检测到旧实例进程，先结束再启动"
    fi
    for PID in $OLD_PIDS; do kill_one "$PID"; done
    # 等端口真正释放（最多 10 秒），避免新进程 bind 竞态
    for i in $(seq 1 10); do
        port_busy || break
        sleep 1
    done
    if port_busy; then
        echo "[警告] 端口 $PORT 仍被占用，启动可能失败，请检查：ss -lptn \"sport = :$PORT\""
    fi
fi
rm -f app.pid

PROFILE=${PROFILE:-${1:-prod}}
mkdir -p logs

command -v java >/dev/null 2>&1 || { echo "[错误] 未找到 java，请安装 JDK 21 (sudo apt install openjdk-21-jre)"; exit 1; }

nohup java -Xms256m -Xmx512m -jar "$JAR" --spring.profiles.active="$PROFILE" > logs/app.log 2>&1 &
echo $! > app.pid
echo "[启动] PID=$(cat app.pid)  profile=$PROFILE  日志=logs/app.log"

# 等待健康检查（最多 60 秒）
for i in $(seq 1 30); do
    if curl -s -o /dev/null "http://localhost:$PORT/health" 2>/dev/null; then
        echo "[就绪] http://localhost:$PORT/health ok"
        exit 0
    fi
    sleep 2
done
echo "[警告] 启动超过 60 秒未就绪，请查看 logs/app.log"
exit 1
