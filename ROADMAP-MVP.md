# universal-video-generator — 技术落地路径 + 行动计划

> 代码已建好（引擎 + web 双模式 + 本地 TTS + 自进化 + HQ MVP + Docker/CI + 文档）。本文不重写代码，只讲**怎么一步步落地、发布、运营**。
> 命令请在仓库根目录执行。时间预估按「一个人、已装 Node 18+」估。

---

## 技术路径（Step 1–5）

### Step 1 — MVP：跑通第一条带语音真视频

目标：从「代码能编译」到「产出一条真·AI 视频 + 真·语音」，验证全链路通。

1. 装依赖 + 编译 + 自检：`npm i && npm run build`，再 `npm test`（跑 core/runtime/evolution/longvideo 等套件，应全绿）。
2. 先跑离线 demo，不花钱验证编排：`node dist/examples/basic-usage.js`、`node dist/examples/long-video.js`（用占位 adapter，确认工作流/拼接/质检通）。
3. 起 web 双模式自测：`npm run web`，浏览器开 `http://localhost:8080`，走一遍表单（产品→脚本→选型→预估）。
4. 接 1 个真实视频模型 Key：复制 `.env.example` 为 `.env`，填一家即可（建议先 Seedance 或可灵，单条便宜、出片快），其余留空走回退。
5. 装 ffmpeg（拼接/混音必需）：Windows 用 `winget install ffmpeg` 或 choco；mac `brew install ffmpeg`；确认 `ffmpeg -version`。
6. 起本地 TTS：`cd tts-server && pip install -r requirements.txt && TTS_BACKEND=moss python app.py`（默认 :9881），在 `.env` 配 `LOCAL_TTS_ENABLED=true`、`LOCAL_TTS_BASE_URL=http://localhost:9881` 即由 `LocalTtsAdapter` 接入。
7. 出第一条真视频：`node dist/bin/cli.js generate --product "便携榨汁杯" --count 1`；要长视频用 `node dist/bin/cli.js long --duration 60`。

时间预估：装/编译 15–20 min；离线 demo 10 min；接 Key + ffmpeg + TTS 30–45 min；首条真视频 15 min。**半天内出片**。
成功标准：`npm test` 全绿；web demo 表单走通；产出 1 个 mp4，**有真实画面 + 真实语音**，`health` 命令报模型/TTS/ffmpeg 全 OK。

### Step 2 — 公开版打磨（开源可用的「能装就能跑」）

目标：陌生人 clone 下来，照 README 五分钟内能跑离线 demo，配个 Key 就能出片。

1. 完善 `.env.example`：每个变量加注释（哪家模型、去哪拿 Key、留空=该家禁用走回退），确保**不填也能跑离线 demo**。
2. 默认开关定调：本地进化（core/evolution）默认开但**仅匿名隐式遥测 + 默认不外发**（opt-in 才上报），README 显著说明可一键关。
3. `VersionChecker` 指向 HQ：把 core/evolution/VersionChecker.ts 的 endpoint 配成你 HQ 的 `/params/latest`（先用占位 URL，HQ 上线后改真域名）。
4. README 补「30 秒上手 + 截图/GIF」：放 web 界面截图、一条产出视频的帧、CLI 输出样例；写清四条命令（build/test/web/generate）。
5. 健康自检入口前置：README 第一段就提 `node dist/bin/cli.js health`，让用户先确认环境。

时间预估：.env 注释 + 开关 1.5 h；VersionChecker 接线 0.5 h；README + 截图 2–3 h。**约 1 个工作日**。
成功标准：在一台干净机器上 clone→`npm i && npm run build`→离线 demo 一次通过；遥测默认不外发；README 有截图、命令复制即用。

### Step 3 — 总部端 HQ（聚合遥测 → 下发更优参数）

目标：把 hq/ 这个 MVP 升级成「能长期收数、能下发参数」的小服务。**HQ 不开源**（见 Step 4 的 .gitignore）。

1. 先用现成 MVP 收数：`node dist/hq/collector.js` 起 collector，确认能接 Reporter 上报、能聚合（aggregate.ts）。
2. 把内存聚合换持久存储：现在是进程内存，落地需**实现 `IJobStore` 同接口**（参照 core/JobStore.ts + FileJobStore.ts），新增 `PostgresJobStore` / Redis 版，**不改调用方**。
   - 选型：Node 服务（现成）+ Postgres（聚合结果/版本/审计，强一致）+ Redis（计数/限流/热点参数缓存）。前端看板任意框架（先 Next.js/Vite 起个只读看板即可）。
3. 加管理后台：列「各参数当前值 / 候选值 / 隐式质量分趋势 / opt-in 设备数」，支持人工「采纳/回滚」候选参数（复用 EvolutionEngine 的验证门控 + 回滚逻辑）。
4. 打通下发：实现 `/params/latest`（返回当前生效参数集 + 版本号），客户端 VersionChecker 定期拉取；上线前先灰度（按设备哈希放量）。

命令骨架：`node dist/hq/collector.js`（收）→ `node dist/hq/aggregate.js`（提炼，如未拆分则在 collector 内）→ 自建 `hq/server`（暴露 /params/latest + 后台）。
时间预估：MVP 收数当天通；换 Postgres/Redis（实现同接口）2–3 天；管理后台只读版 2 天 + 采纳/回滚 1–2 天；下发灰度 1 天。**约 1–2 周**。
成功标准：collector 持续收数不丢；重启后聚合结果还在（已落库）；后台能看趋势并一键回滚；客户端能拉到 `/params/latest` 且版本号正确递增。

### Step 4 — 上 GitHub（开源主仓，排除 HQ 与密钥）

目标：把**客户端引擎**开源，HQ 与一切密钥**不进仓**。

1. 写 `.gitignore`：至少排除 `node_modules/`、`dist/`、`.env`、`.env.*`、`hq/`（HQ 不开源）、本地产物（输出 mp4/音频/临时帧）。
2. 初始化并提交：`git init && git add -A && git commit -m "feat: universal-video-generator v0.1 (engine + web + local TTS + evolution)"`。
3. 关联远端并推送：`git remote add origin <your-repo-url> && git branch -M main && git push -u origin main`。
4. 打 release：`git tag v0.1.0 && git push origin v0.1.0`，在 GitHub 上基于 tag 发 Release，正文贴 CHANGELOG.md 要点。
5. README 收尾：徽章（CI 状态/license）、一句话定位（「视频界的 LangChain：统一所有 AI 视频模型 + TTS」）、上手四步、支持的模型/TTS 清单（引 references/）、贡献指南指向 CONTRIBUTING.md。

时间预估：.gitignore + 首次提交 0.5 h；推送 + release + README 收尾 1.5–2 h。**半天内**。
成功标准：仓库里**没有** `.env`/`hq/`/产物；CI（.github/workflows/ci.yml）在 PR 上自动绿；陌生人能按 README 跑通离线 demo；Release 页有 v0.1.0。

### Step 5 — 运营迭代（让它有人用、持续变好）

目标：拿到首批真实用户，把「自进化」喂上真实数据，形成反馈→迭代闭环。

1. 首批用户（20–50 个）：定向发给做 UGC/短视频/出海的开发者与团队；给「5 分钟跑通」清单 + 一条你自己产出的样片当钩子。
2. 收反馈三件套：GitHub Issues 模板（bug/模型适配请求/选型建议）+ 一个轻量问卷（最想接哪个模型、卡在哪步）+ 观察 opt-in 遥测里的隐式质量分。
3. 迭代节奏：双周一个小版本——优先「新增 Adapter（只加文件不改核心）」「修跑通路上的坑」「按真实数据微调进化参数」；每版更新 CHANGELOG + 打 tag。
4. 社区：README 置顶 roadmap 与「想要的模型投票」入口；接受 PR 走 CONTRIBUTING.md；活跃贡献者拉进 maintainer。
5. 长视频/矩阵当差异化卖点持续打磨（多段续帧一致 + 拼接 + 混用户素材），这是对手少做的部分。

时间预估：首批触达 1 周；之后双周节奏持续。
成功标准：见下方「PMF 验证指标」。

---

## 行动计划

### 今天就能做的 3 件事
1. `npm i && npm run build && npm test`——确认全绿，本地基线稳。
2. 跑离线 demo（`node dist/examples/basic-usage.js`）+ `npm run web` 开 8080 自测一遍表单。
3. 复制 `.env.example`→`.env`，挑**一家便宜模型**填 Key，装 ffmpeg，目标半天出**第一条真视频**。

### 第一周目标
- Step 1 完成：稳定出带语音真视频；`health` 全 OK。
- Step 2 过半：`.env.example` 注释清楚、遥测默认不外发、README 加截图与四条命令。
- 起 `node dist/hq/collector.js` 把 HQ MVP 跑起来收数（哪怕先内存版）。

### 第一个月目标
- Step 2–4 收口：公开版打磨完，HQ 换上 Postgres/Redis（实现同接口）、有只读看板，客户端 VersionChecker 能拉 `/params/latest`。
- 上 GitHub 发 v0.1.0，CI 绿、HQ/密钥不进仓。
- 触达首批 20–50 用户，开始收 Issues 与 opt-in 遥测。

### 怎么验证 PMF（具体指标）
- **opt-in 率**：愿意开匿名遥测的活跃用户占比 ≥ 25%（说明信任 + 觉得有用）。
- **复用率**：装过的人**第二条及以后**视频的占比 ≥ 50%（一次性试用之外有真需求）。
- **隐式质量分趋势**：自进化上线后，隐式质量信号（QualitySignals）**周环比稳定上升**且回滚次数下降——证明「数据→更优参数」闭环真的在起效。
- **留存**：周活/月活回访（WAU/MAU）月环比正增长；30 天留存 ≥ 20%。
- **贡献者数**：30 天内外部 PR/新增 Adapter ≥ 3 个、Star 有机增长——证明「只加 Adapter 不改核心」的扩展模型被社区接受。

> PMF 信号判定：上述 5 项里**复用率 + 隐式质量分上升**两条是核心，两条都达标即视为初步 PMF；opt-in 率、留存、贡献者数为佐证。
