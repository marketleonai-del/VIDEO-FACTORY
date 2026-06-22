# 视频工厂 · 桌面版（Electron 外壳）

把 web 引擎包成一个**双击即用**的桌面 App。macOS 出 `.app`/`.dmg`（Apple Silicon arm64 + Intel x64），Windows 出 `.exe`。

> 外壳只负责"拉起本地 web 服务 + 开窗口加载"。真正的生成能力在仓库根目录的引擎里（`dist/web/server.js`），桌面版与浏览器版功能完全一致。

## 开发期运行（先在仓库根目录 `npm run build`）

```bash
cd desktop
npm install            # 装 electron / electron-builder
npm start              # 起外壳：自动拉起 dist/web/server.js 并开窗口
```

## 打包成安装包

```bash
# macOS（需在 Mac 上执行，产物在 desktop/release/）
npm run dist:mac       # 出 视频工厂-3.2.0-arm64.dmg 和 x64.dmg
# Windows（在 Windows 上执行）
npm run dist:win       # 出 .exe（NSIS 安装包）
```

产物默认在 `desktop/release/`。`extraResources` 会把仓库根目录的 `dist/` 与 `web/public/` 一并打进 App，所以**打包前务必先在根目录 `npm run build`**。

## 图标（可选）

放 `desktop/icon.icns`（mac，1024×1024）与 `desktop/icon.ico`（win）即可被 electron-builder 采用；不放也能打包，用默认图标。

## 说明

- 打包后用 **Electron 自带的 Node**（`ELECTRON_RUN_AS_NODE`）跑服务，终端用户**无需另装 Node**。
- 但**ffmpeg**（拼接/混音）与**本地 TTS 服务**（`tts-server/`，需 Python）仍按各自方式安装，详见根目录 `MAC-SETUP.md`。
- 端口默认 `8799`，可用环境变量 `WEB_PORT` 覆盖。
