/**
 * desktop/main.js — 视频工厂桌面外壳（Electron）
 * macOS 打包成 .app/.dmg（Apple Silicon arm64 + Intel x64），Windows 出 .exe。
 * 原理：拉起已编译的 web 服务（dist/web/server.js）→ 用窗口加载 http://127.0.0.1:PORT。
 * 引擎本身跨平台（Node + ffmpeg + 本地 TTS）；此外壳只负责"双击即用"。
 */
const { app, BrowserWindow, shell } = require("electron");
const { spawn } = require("node:child_process");
const path = require("node:path");
const fs = require("node:fs");
const http = require("node:http");

const PORT = process.env.WEB_PORT || 8799;
let serverProc = null;
let win = null;

function resolveServer() {
  const cands = [
    path.join(__dirname, "..", "dist", "web", "server.js"),                 // 开发：仓库已 build
    path.join(process.resourcesPath || "", "dist", "web", "server.js"),     // 打包后：随 App 附带
  ];
  return cands.find((p) => { try { return fs.existsSync(p); } catch { return false; } }) || cands[0];
}

function startServer() {
  const entry = resolveServer();
  // 用 Electron 自带的 Node 运行 server.js（ELECTRON_RUN_AS_NODE=1），打包后无需用户装 Node
  serverProc = spawn(process.execPath, [entry], {
    env: { ...process.env, WEB_PORT: String(PORT), ELECTRON_RUN_AS_NODE: "1" },
    stdio: "inherit",
  });
  serverProc.on("error", (e) => console.error("[desktop] server spawn error:", e));
}

function waitForServer(cb, tries) {
  tries = tries || 0;
  const req = http.get(`http://127.0.0.1:${PORT}/api/config`, () => cb());
  req.on("error", () => {
    if (tries > 80) return cb(); // 超时也加载：前端会自动进"纯前端"模式
    setTimeout(() => waitForServer(cb, tries + 1), 250);
  });
}

function createWindow() {
  win = new BrowserWindow({
    width: 1180, height: 820, minWidth: 360, minHeight: 600,
    backgroundColor: "#0A0A0B",
    titleBarStyle: "hiddenInset",            // macOS 原生红绿灯内嵌，更精致
    trafficLightPosition: { x: 16, y: 18 },
    webPreferences: { contextIsolation: true, nodeIntegration: false },
  });
  win.loadURL(`http://127.0.0.1:${PORT}/`);
  // 外链用系统浏览器打开（GitHub 等）
  win.webContents.setWindowOpenHandler(({ url }) => { shell.openExternal(url); return { action: "deny" }; });
}

app.whenReady().then(() => {
  startServer();
  waitForServer(() => createWindow());
  app.on("activate", () => { if (BrowserWindow.getAllWindows().length === 0) createWindow(); }); // macOS dock 点击
});
app.on("window-all-closed", () => { if (process.platform !== "darwin") app.quit(); }); // macOS 习惯：关窗不退
app.on("quit", () => { if (serverProc) { try { serverProc.kill(); } catch (e) { /* ignore */ } } });
