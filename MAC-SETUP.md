# macOS / 苹果版 部署与使用指南

视频工厂的引擎本就**跨平台**（Node + ffmpeg + 本地 TTS 都在 macOS 原生运行），Apple Silicon（M 系列）更是本地 TTS 的理想机器——单核也能跑 MOSS-TTS-Nano。本文给 Mac 用户三种用法，从最简单到能分发给别人。

---

## 0. 一次性前置（装 Homebrew + 基础工具）

```bash
# 1) 装 Homebrew（已装可跳过）
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# 2) 装运行所需（Node 跑引擎、ffmpeg 拼接配音、python 跑本地 TTS）
brew install node ffmpeg python

# 3) 验证
node -v        # ≥ 18
ffmpeg -version
python3 -V
```

Apple Silicon 说明：以上全部是 **arm64 原生**，无需 Rosetta。若你装的是 Intel 版 Homebrew（`/usr/local`）也能跑，只是慢一点；推荐用原生 arm64 Homebrew（`/opt/homebrew`）。

---

## 1. 最简单：双击启动（推荐给自己用）

仓库里已带 `mac/start.command`。

```bash
# 首次给执行权限（只需一次）
chmod +x "mac/start.command"
```

然后在访达里**双击 `mac/start.command`**：它会自动检查环境、必要时装依赖+编译、起服务、并打开浏览器到 `http://localhost:8080`。

首次双击若被拦（"未受信任的开发者"）：右键该文件 →「打开」→ 在弹窗里再点「打开」一次即可。

---

## 2. 终端用法（开发/调试）

```bash
npm install          # 装依赖
npm run build        # 编译 TypeScript
npm test             # 自检（应全绿）
npm run web          # 起 web，浏览器开 http://localhost:8080

# CLI 出片
node dist/bin/cli.js health                                   # 看模型/TTS/ffmpeg 状态
node dist/bin/cli.js generate --product "便携榨汁杯" --count 1   # 出一条
node dist/bin/cli.js long --duration 60                       # 长视频
```

---

## 3. 桌面 App：打包成 `.app` / `.dmg`（分发给别人）

用 `desktop/` 里的 Electron 外壳，产出**双击安装、双击运行**的原生 Mac App，且终端用户无需自己装 Node。

```bash
# 先在仓库根目录编译（外壳会把 dist/ 打进 App）
npm install && npm run build

# 再打包桌面版
cd desktop
npm install
npm run dist:mac     # 产物在 desktop/release/：视频工厂-3.2.0-arm64.dmg + x64.dmg
```

把 `.dmg` 发给对方 → 打开 → 把「视频工厂」拖进「应用程序」即可。

> 图标可选：放 `desktop/icon.icns`（1024×1024）会自动采用，不放则用默认图标。

### Gatekeeper（未签名 App 首次打开）

自用或内测不必买 Apple 开发者账号，让对方这样打开一次即可：

- 右键 App →「打开」→ 弹窗里再点「打开」；或
- 终端执行：`xattr -dr com.apple.quarantine "/Applications/视频工厂.app"`

要正式上架/大规模分发，再走 Apple Developer 签名 + 公证（`codesign` + `notarytool`），需付费开发者账号。

---

## 4. 本地 TTS（零云成本配音）在 Mac 上

```bash
cd tts-server
pip3 install -r requirements.txt
TTS_BACKEND=moss python3 app.py     # 默认 :9881
```

然后在根目录 `.env` 配：

```
LOCAL_TTS_ENABLED=true
LOCAL_TTS_BASE_URL=http://localhost:9881
LOCAL_TTS_MODEL=moss
```

引擎即通过 `LocalTtsAdapter` 接入，配音成本为 0。Apple Silicon 上 PyTorch 自带 MPS 加速，MOSS-TTS-Nano（0.1B）即便走 CPU 也能实时合成；Kokoro / Piper 同理。

---

## 5. 常见问题

**端口被占用**：`WEB_PORT=8090 node dist/web/server.js`（桌面外壳同样认 `WEB_PORT`）。

**`ffmpeg: command not found`**：`brew install ffmpeg`；或在 `.env` 设 `FFMPEG_BIN` 指向绝对路径。

**M 系列上 pip 装 torch 慢/报错**：先 `pip3 install --upgrade pip`，PyTorch 官网选 macOS arm64 的安装命令；MOSS/Kokoro 依赖会随之就位。

**双击 `.command` 闪退**：先 `chmod +x mac/start.command`；或终端里 `bash mac/start.command` 看报错。

**只想要纯前端、不跑后端**：直接用浏览器打开 `web/public/index.html`，界面会自动进"纯前端"模式（只做脚本/选型预览，不真实出片）。

---

跨平台对照：Windows 同样支持（`winget install ffmpeg` + `npm run web`，桌面版 `cd desktop && npm run dist:win` 出 `.exe`）。引擎代码无平台分支，三端一致。
