#!/bin/bash
# Gemini服务器启动脚本
# 自动检查并建立SSH隧道

set -e

# 使用国内镜像加速 Hugging Face 模型下载
export HF_ENDPOINT=https://hf-mirror.com

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# 颜色定义
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${GREEN}🚀 启动语音控制服务(多代理 + 事件流)...${NC}"

# 检查SSH隧道（同时检查两个关键映射端口）
check_ssh_tunnel() {
    # 检查控制端口 1502 和 语音端口 8866
    if (netstat -tlnp 2>/dev/null | grep -q ":1502" || ss -tlnp 2>/dev/null | grep -q ":1502") && \
       (netstat -tlnp 2>/dev/null | grep -q ":8866" || ss -tlnp 2>/dev/null | grep -q ":8866"); then
        return 0
    else
        return 1
    fi
}

# 建立并监控SSH隧道
setup_ssh_tunnel() {
    echo -e "${YELLOW}📡 正在建立SSH隧道...${NC}"
    # 强制清理
    pkill -f "10.10.70.218" || true
    sleep 1
    
    # 使用心跳检测和自动重连配置
    ssh -o "ServerAliveInterval 15" \
        -o "ServerAliveCountMax 3" \
        -o "ConnectTimeout 10" \
        -o "ExitOnForwardFailure yes" \
        -f -N -L 1502:localhost:502 -L 8866:localhost:8800 -p 2222 root@10.10.70.218 2>&1
    
    sleep 2
    
    if check_ssh_tunnel; then
        echo -e "${GREEN}✅ SSH隧道已建立 (Modbus:1502, Voice:8866->8800)${NC}"
        # 启动后台监控进程（如果还没启动）
        if ! pgrep -f "monitor_ssh_tunnel" > /dev/null; then
            monitor_ssh_tunnel &
        fi
        return 0
    else
        echo -e "${RED}❌ SSH隧道建立失败，请检查机器人网络或端口占用${NC}"
        return 1
    fi
}

# 后台监控函数（更激进的恢复策略）
monitor_ssh_tunnel() {
    while true; do
        sleep 5
        if ! check_ssh_tunnel; then
            echo -e "${RED}⚠️  检测到SSH隧道异常(Broken Pipe)，正在强制修复...${NC}"
            # 杀死所有相关 ssh 进程
            pkill -f "10.10.70.218" || true
            # 强制释放端口
            fuser -k 1502/tcp 2>/dev/null || true
            fuser -k 8866/tcp 2>/dev/null || true
            sleep 1
            # 重新建立
            ssh -o "ServerAliveInterval 15" \
                -o "ServerAliveCountMax 3" \
                -o "ConnectTimeout 10" \
                -o "ExitOnForwardFailure yes" \
                -f -N -L 1502:localhost:502 -L 8866:localhost:8800 -p 2222 root@10.10.70.218 2>&1
        fi
    done
}

# 检查conda环境
check_conda_env() {
    if ! command -v conda &> /dev/null; then
        echo -e "${RED}❌ 未找到conda命令${NC}"
        return 1
    fi
    
    if ! conda env list | grep -q "functional_call"; then
        echo -e "${YELLOW}⚠️  未找到functional_call环境，请先创建:${NC}"
        echo -e "   conda create -n functional_call python=3.11"
        return 1
    fi
    
    return 0
}

# 主流程
main() {
    # 检查conda环境
    if ! check_conda_env; then
        exit 1
    fi
    
    # 检查SSH隧道
    if ! check_ssh_tunnel; then
        echo -e "${YELLOW}⚠️  未检测到SSH隧道${NC}"
        if ! setup_ssh_tunnel; then
            echo -e "${RED}❌ 无法建立SSH隧道，退出${NC}"
            exit 1
        fi
    else
        echo -e "${GREEN}✅ SSH隧道已存在${NC}"
    fi
    
    # 检查.env文件
    if [ ! -f ".env" ]; then
        echo -e "${YELLOW}⚠️  未找到.env文件${NC}"
        echo -e "${YELLOW}💡 请创建.env文件并配置DASHSCOPE_API_KEY（通义千问）${NC}"
        exit 1
    fi
    
    # 启动服务器
    echo -e "${GREEN}🎯 启动语音控制服务...${NC}"
    echo ""
    
    # 获取conda环境的Python路径并直接运行，确保日志实时显示
    CONDA_ENV_PATH=$(conda env list | grep "^functional_call" | awk '{print $NF}' | head -1)
    
    if [ -z "$CONDA_ENV_PATH" ] || [ ! -f "$CONDA_ENV_PATH/bin/python3" ]; then
        echo -e "${RED}❌ 无法找到conda环境的Python解释器${NC}"
        exit 1
    fi
    
    # 设置环境变量禁用Python输出缓冲，确保日志实时显示
    export PYTHONUNBUFFERED=1
    "$CONDA_ENV_PATH/bin/python3" -u voice_server.py
}

main "$@"

