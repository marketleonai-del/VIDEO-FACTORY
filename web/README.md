# 视频工厂 Web 应用（双模式）

极简单页前端 + 零依赖后端，跑在 `universal-video-generator` 引擎之上。支持短/长视频、全 AI / 混合、多段拼接、自进化遥测。

## 两种部署模式

### 模式 1 · 纯前端（开源免费，用户自带 Key）
- 直接打开 `web/public/index.html`（或丢到任意静态托管：GitHub Pages / Vercel / Netlify）。
- 前端检测不到后端 → 进入"纯前端模式"，让用户填自己的 API Key（仅存浏览器 `localStorage`，不上传）。
- 适合技术用户/开发者。注意：浏览器直连第三方视频 API 可能有 CORS 限制，建议技术用户用浏览器插件或本地代理；或直接用模式 2。

### 模式 2 · 后端 SaaS（你部署，统一管 Key）
```bash
cp ../.env.example ../.env     # 填各家视频/TTS API Key（缺则 demo）
npm run build && node dist/web/server.js   # 或开发期：npm run web
# 打开 http://localhost:8080
```
- 前端检测到 `/api/config` → 自动进入"后端模式"，用户无需填 Key。
- 后端统一管 Key、转发、**逐段生成 + 拼接**、统计；可加免费额度/计费（复用主项目的 Auth/Quota/Metering）。
- 部署：Vercel/Railway/VPS/Docker（主项目已带 Dockerfile）。

> 同一套前端代码，自动适配两种模式（`fetch('/api/config')` 探测）。

## 前端能力
- 2×2 场景矩阵（全AI/混合 × 短/长）；时长 10/20/30/60s（=1/2/3/6 段拼接）。
- 描述 + 参考图（锁主体）+ 混合模式上传素材；大"生成"按钮。
- **多段生成进度 + 拼接进度**逐步可见；结果可播放/下载/重生成/调参重生成。
- 隐私：匿名遥测**默认关**（opt-in 开关）。

## 后端 API
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/config` | 探测后端在线 + 能力（前端据此切模式） |
| POST | `/api/generate` | `{prompt,mode,durationSec}` → `{jobId}`（异步逐段生成+拼接） |
| GET | `/api/jobs/:id` | 进度：每段 state + stitch + videoUrl |
| GET | `/api/stats` | 简单统计（总数/成功/按模式） |
| POST | `/api/telemetry` | 匿名 opt-in 遥测（喂自进化） |

## 配置
- 后端 Key/端点见主项目 `.env.example`（缺则 demo 模式，流程可跑、出占位结果）。
- `WEB_PORT`（默认 8080）、`UVG_GITHUB`（页脚链接）、`UVG_WEB_JOBS`/`UVG_WEB_STATS`（落盘路径）。
- 真实出片需各家 API Key + 系统装 ffmpeg（拼接）。
