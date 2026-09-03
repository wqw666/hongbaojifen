#!/usr/bin/env bash
# ==========================================
# 红包积分管理系统 构建脚本（仅本地/CI 打包，不部署服务器）
#   ./build.sh          → 读取 VERSION 构建（含前端自动打包）
#   ./build.sh 1.1.0    → 指定版本并更新 VERSION
#   ./build.sh 1.1.0 skip → 跳过前端构建（仅后端）
# 产物: target/hongbaojifen-api-<版本>.jar
# ==========================================
set -e
cd "$(dirname "$0")"

if [ -n "$1" ]; then
    echo "$1" > VERSION
    V=$1
else
    V=$(cat VERSION)
fi
echo "[版本] $V"

# 配置 JDK（有 JAVA_HOME 则优先，否则用系统 java）
if [ -n "$JAVA_HOME" ] && [ -d "$JAVA_HOME/bin" ]; then
    export PATH="$JAVA_HOME/bin:$PATH"
fi
command -v java >/dev/null 2>&1 || { echo "[错误] 未找到 java，请安装 JDK 21 或设置 JAVA_HOME"; exit 1; }

# 是否跳过前端
if [ "$2" = "skip" ]; then
    echo "[前端] 跳过前端构建"
    mvn clean package -Drevision=$V -DskipTests -Dskip.fe=true -q
else
    echo "[前端] 自动构建 React 前端并内嵌到 JAR"
    mvn clean package -Drevision=$V -DskipTests -q
fi

JAR="target/hongbaojifen-api-$V.jar"
if [ -f "$JAR" ]; then
    SIZE=$(stat -c %s "$JAR" 2>/dev/null || wc -c < "$JAR")
    echo "[完成] $JAR  $SIZE bytes"
    echo
    echo "服务器部署流程（start.sh/stop.sh/deploy.sh 已常驻 /opt/hongbaojifen/）:"
    echo "  1) scp $JAR ubuntu@<服务器IP>:/tmp/"
    echo "  2) ssh ubuntu@<服务器IP> /opt/hongbaojifen/deploy.sh"
    echo
    echo "首次部署（服务器目录初始化）:"
    echo "  ssh ubuntu@<服务器IP> 'mkdir -p /opt/hongbaojifen/logs'"
    echo "  scp start.sh stop.sh deploy.sh VERSION ubuntu@<服务器IP>:/opt/hongbaojifen/"
else
    echo "[失败] 构建失败，未生成 $JAR"
    exit 1
fi
