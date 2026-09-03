#!/usr/bin/env bash
# ==========================================
# 红包积分管理系统 服务器部署接收端（Ubuntu 服务器，/opt/hongbaojifen/）
#   服务器目录: start.sh stop.sh deploy.sh VERSION hongbaojifen-api-<V>.jar logs/
#   配合本地流程: ./build.sh && scp target/hongbaojifen-api-<V>.jar ubuntu@<IP>:/tmp/
#                ssh ubuntu@<IP> /opt/hongbaojifen/deploy.sh
# ==========================================
cd "$(dirname "$0")"

# 依赖脚本检查
if [ ! -f start.sh ] || [ ! -f stop.sh ]; then
    echo "[错误] 缺少 start.sh / stop.sh，请先上传: scp start.sh stop.sh ubuntu@<IP>:/opt/hongbaojifen/"
    exit 1
fi

# VERSION 仅用于日志显示（可选文件，缺失不阻断部署）
if [ -f VERSION ]; then V=$(tr -d '\r' < VERSION); else V="?"; fi
# 通配符匹配 /tmp 上传的 jar（取最新；不依赖 VERSION 与文件名强一致）
TMP_JAR=$(ls -t /tmp/hongbaojifen-api-*.jar 2>/dev/null | head -1)
APP_DIR=$(pwd)

if [ -z "$TMP_JAR" ]; then
    echo "[错误] /tmp 下没有 hongbaojifen-api-*.jar"
    echo "       请先本地执行: ./build.sh && scp target/hongbaojifen-api-*.jar ubuntu@<IP>:/tmp/"
    exit 1
fi

# 停旧 → 备份 → 换新 → 启动
if [ -f app.pid ] && kill -0 "$(cat app.pid)" 2>/dev/null; then
    echo "[部署] 停止旧进程 PID=$(cat app.pid)"
    ./stop.sh
else
    echo "[部署] 无运行中进程，直接部署"
fi

JAR_NAME=$(basename "$TMP_JAR")
if [ -f "$APP_DIR/$JAR_NAME" ]; then
    cp "$APP_DIR/$JAR_NAME" "$APP_DIR/$JAR_NAME.bak.$(date +%Y%m%d%H%M%S)"
fi
cp "$TMP_JAR" "$APP_DIR/$JAR_NAME"
rm -f "$TMP_JAR"

echo "[部署] 启动新版本 $JAR_NAME"
./start.sh
