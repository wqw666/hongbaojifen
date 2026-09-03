#!/usr/bin/env bash
# ==========================================
# 红包积分管理系统 停止脚本
#   优先按 app.pid 停止；无 pid 文件时按进程名查找兜底
# ==========================================
cd "$(dirname "$0")"

stop_pid() {
    local PID=$1
    if kill -0 "$PID" 2>/dev/null; then
        echo "[停止] 发送终止信号 PID=$PID"
        kill "$PID"
        # 优雅退出最多等 15 秒
        for i in $(seq 1 15); do
            kill -0 "$PID" 2>/dev/null || { echo "[停止] 已退出"; return 0; }
            sleep 1
        done
        echo "[强制] 优雅退出超时，强制结束"
        kill -9 "$PID" 2>/dev/null
    else
        echo "[提示] PID=$PID 不存在（进程可能已退出）"
    fi
}

if [ -f app.pid ]; then
    stop_pid "$(cat app.pid)"
    rm -f app.pid
else
    echo "[提示] 无 app.pid 文件，按进程名查找..."
    FOUND=0
    # 兼容 Linux (pgrep) 与 Git Bash (ps)
    if command -v pgrep >/dev/null 2>&1; then
        for PID in $(pgrep -f "hongbaojifen-api.*\.jar" 2>/dev/null); do
            stop_pid "$PID"; FOUND=1
        done
    else
        for PID in $(ps -ef 2>/dev/null | grep "hongbaojifen-api.*\.jar" | grep -v grep | awk '{print $2}'); do
            stop_pid "$PID"; FOUND=1
        done
    fi
    [ "$FOUND" = "0" ] && echo "[提示] 未找到运行中的进程"
fi
