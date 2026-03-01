#!/bin/bash
# ============================================
#  GenUI Agent — 双击启动
# ============================================

# 切换到脚本所在目录（即项目根目录）
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

# 加载 .env 环境变量
if [ -f ".env" ]; then
    export $(grep -v '^#' .env | xargs)
fi

# 激活虚拟环境
if [ -d ".venv" ]; then
    source .venv/bin/activate
fi

# 检查 streamlit 是否安装
if ! command -v streamlit &> /dev/null; then
    echo "❌ Streamlit 未安装，正在安装依赖..."
    pip install -r requirements.txt
fi

echo "🚀 正在启动 GenUI Agent..."
echo "   浏览器将自动打开，如未打开请访问 http://localhost:8501"
echo ""
streamlit run app.py

# 如果异常退出，保持窗口
echo ""
echo "按任意键关闭..."
read -n 1
