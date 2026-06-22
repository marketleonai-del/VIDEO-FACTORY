#!/bin/bash
# 视频工厂 · macOS 一键启动（双击此文件即可运行）
# 首次使用：右键 →「打开」一次（绕过 Gatekeeper）；或在终端执行 chmod +x start.command
# 作用：检查环境 → 必要时装依赖/编译 → 起 web 服务 → 自动开浏览器到 http://localhost:8080

set -e
cd "$(dirname "$0")/.." || exit 1   # 切到仓库根目录（本脚本在 mac/ 下）

echo "════════════════════════════════════"
echo "  视频工厂 · 正在启动…"
echo "════════════════════════════════════"

# 1) Node 检查（macOS 推荐 brew install node）
if ! command -v node >/dev/null 2>&1; then
  echo "✗ 未检测到 Node.js。请先安装："
  echo "    /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
  echo "    brew install node"
  echo "（装好后再次双击本文件）"
  read -n 1 -s -r -p "按任意键退出…"; exit 1
fi
echo "✓ Node $(node -v)"

# 2) ffmpeg 检查（拼接/配音必需；缺了也能跑 demo，只是不出最终成片）
if command -v ffmpeg >/dev/null 2>&1; then
  echo "✓ ffmpeg 已安装"
else
  echo "⚠ 未检测到 ffmpeg（拼接/配音需要）。安装：brew install ffmpeg"
fi

# 3) 依赖 + 编译（仅首次或缺失时）
if [ ! -d node_modules ]; then echo "▶ 安装依赖（首次较慢）…"; npm install; fi
if [ ! -d dist ]; then echo "▶ 编译…"; npm run build; fi

# 4) 起服务 + 自动开浏览器
PORT="${WEB_PORT:-8080}"
echo "▶ 启动 web 服务于 http://localhost:${PORT}"
( sleep 2; open "http://localhost:${PORT}" ) &
WEB_PORT="${PORT}" node dist/web/server.js
