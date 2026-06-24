---
name: video-factory
version: "3.0"
description: >
  AI带货视频工厂，SkillOpt自进化引擎，300-Agent蜂群协同。
  输入产品（图片/名称）→ 自动分析卖点 → 写带货脚本 → 拆分故事板分镜 →
  逐镜生成AI视频 → ffmpeg拼接成片 → 矩阵批量200条 → 隐式信号驱动参数自进化。
triggers:
- 产品图
- 产品名
- /generate
- /矩阵
- /混剪
- /进化
- /角度
- /钩子
- /status
- /health
language: zh-CN
---

# VIDEO-FACTORY v3.0 — AI带货视频工厂

> **一句话**: 输入产品（图/名）→ 自动分析卖点 → 写带货脚本 → 拆故事板分镜 → 逐镜出AI视频 → ffmpeg拼接成片 → 矩阵200条 → SkillOpt自进化越用越好。

---

## 快速开始

```bash
# 1. 克隆仓库
git clone https://github.com/marketleonai-del/VIDEO-FACTORY.git
cd VIDEO-FACTORY

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env 填入你的 API Key:
#   OPENAI_API_KEY=sk-xxx
#   KLING_API_KEY=xxx
#   LIP_SYNC_API_KEY=xxx

# 3. 安装依赖并启动
npm install
node live-server.js          # 启动核心服务（端口 8088）

# 4. 或使用 Docker
docker-compose up -d

# 5. 打开 http://localhost:8088 开始使用
```

### 环境变量配置

| 变量 | 必填 | 说明 |
|------|------|------|
| `OPENAI_API_KEY` | 是 | GPT-4o / GPT-4o-mini API Key |
| `KLING_API_KEY` | 是 | 可灵AI视频生成 API Key |
| `LIP_SYNC_API_KEY` | 否 | 对口型服务 API Key |
| `UVG_PORT` | 否 | 服务端口，默认 8088 |
| `UVG_OUTPUT_DIR` | 否 | 视频输出目录，默认 `./.uvg-out` |
| `UVG_TELEMETRY` | 否 | 遥测开关，`on` / `off`，默认 `on` |
| `UVG_HQ_ADMIN_KEY` | 否 | HQ管理密钥，默认 `changeme` |

---

## 核心能力

### 1. 单条精品生成

完整生产链路（9步自动化）：

```
产品输入 → 卖点分析 → 受众定位 → 角度选择 → 钩子匹配
   → 脚本生成 → 故事板分镜 → AI逐镜生成 → ffmpeg拼接 → 质检输出
```

| 步骤 | 模块 | 说明 |
|------|------|------|
| 1 | `ProductInsight` | AI看图 + 结构化分析，提取3-5个核心卖点 |
| 2 | `AudienceProfiler` | 基于产品类型推断目标人群画像 |
| 3 | `AngleScanner` | 6大角度族自动扫描（痛点/功效/场景/情感/社会认同/对比） |
| 4 | `HookMatcher` | 10族钩子库智能匹配最佳开场 |
| 5 | `ScriptWriter` | 生成带货脚本（含画面描述、旁白、字幕标注） |
| 6 | `StoryboardSplit` | 9字段完整故事板：镜号/画面/旁白/字幕/B-roll/时长/转场/角度/钩子 |
| 7 | `VideoGenerator` | 丽帧(Luma) / 可灵(Kling) / Pika 逐镜生成 |
| 8 | `FFmpegStitch` | 智能拼接、转场、音画同步 |
| 9 | `QualityGate` | 120门质检自动检测 + 自动重修 |

### 2. 矩阵批量生成（最多200条）

```bash
POST /api/generate
# 参数 count: 200 即可批量生成
```

矩阵变体策略（4大轮换轴）：

| 轮换轴 | 变体数 | 示例 |
|--------|--------|------|
| 钩子开场 | 10族 x 3变体 = 30 | 悬念型/震惊型/反转型/... |
| 人设口吻 | 5种 | 闺蜜安利/专家背书/素人分享/... |
| 场景设定 | 4种 | 厨房/户外/办公室/睡前 |
| 节奏变速 | 3种 | 紧凑15s/标准30s/慢节奏60s |

差异化机制：
- 跨账号人设差异（避免内容重复）
- 平台适配（抖音9:16/快手/小红书/视频号）
- 防限流检测（相似度<阈值才输出）
- 成本估算（预生成报价）

### 3. AI+素材混剪

```bash
POST /api/hybrid-cut
```

混剪工作流：

```
用户上传素材 → 爆款AI前3秒钩子生成 → 真/AI决策矩阵
  → 智能拼接（用户素材 + AI片段） → 声纹锁统一音色 → 输出成片
```

**真 vs AI 决策矩阵**：

| 场景 | 决策 | 说明 |
|------|------|------|
| 前3秒钩子 | AI生成 | 需要强吸引力开头 |
| 产品展示 | 用户素材 | 真实感更强 |
| 使用效果 | 优先用户 | 真实对比有说服力 |
| 过渡/氛围 | AI补充 | 素材不足时填充 |

**声纹锁 `@voice1`**：
- 统一所有视频的旁白音色
- 支持品牌专属声纹注册
- TTS服务自动配音

### 4. SkillOpt 自进化引擎

```
Agent生成视频 → 隐式信号收集 → 本地Bandit优化 → 匿名上报HQ
  → 全局聚合分析 → 参数进化 → 下发所有Agent → 效果验证门控
```

**隐式信号（无需用户打分）**：

| 信号类型 | 采集方式 | 权重 |
|----------|----------|------|
| 完成率 | 视频是否生成成功 | 高 |
| 生成耗时 | 端到端时间 | 中 |
| 钩子点击率 | 播放/曝光比 | 高 |
| 完播率 | 看完/播放比 | 高 |
| 互动率 | 点赞评论转发 | 中 |
| 复检触发率 | QA门触发次数 | 低 |

**验证门控**（严格优于才采纳）：
- 新参数 vs 当前最优：A/B测试至少100次
- 置信度 > 95% 才替换
- 回滚机制：效果下降自动回退

---

## 300-Agent 蜂群

### 架构概览

```
                    ┌─────────────┐
                    │     HQ      │  ← 总部：参数聚合/下发/排行榜
                    │  (port 8089)│
                    └──────┬──────┘
                           │
        ┌──────────────────┼──────────────────┐
        │                  │                  │
   ┌────┴────┐      ┌─────┴─────┐     ┌──────┴──────┐
   │ Generator│      │  Evolver  │     │     QA      │
   │  x 200   │      │   x 50    │     │    x 30     │
   │          │      │           │     │             │
   │ • 视频生成│      │ • Bandit  │     │ • 120门质检 │
   │ • 故事板 │      │ • A/B测试 │     │ • 自动重修  │
   │ • ffmpeg │      │ • 参数进化│     │ • 合规检查  │
   └────┬────┘      └─────┬─────┘     └──────┬──────┘
        │                  │                  │
        └──────────────────┼──────────────────┘
                           │
                    ┌──────┴──────┐
                    │  Creative   │
                    │    x 20     │
                    │             │
                    │ • 钩子优化  │
                    │ • 角度评分  │
                    │ • 提示词进化│
                    └─────────────┘
```

### 启动蜂群

```bash
# 方式1：直接启动
node start-swarm.js --agents=300 --hq=http://localhost:8089

# 方式2：Docker Compose（自动启动 swarm-launcher 服务）
docker-compose --profile swarm up -d

# 方式3：缩小规模测试
node start-swarm.js --agents=10 --mode=auto

# 方式4：仅预览分布（不实际启动）
node start-swarm.js --agents=300 --mode=dry-run
```

### 命令行参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--agents=N` | 300 | 总Agent数量 |
| `--hq=URL` | http://localhost:8089 | HQ端点地址 |
| `--mode=MODE` | auto | 启动模式：auto / manual / dry-run |
| `--max-concurrent=N` | 50 | 最大并发启动数 |
| `--interval=MS` | 100 | Agent启动间隔（毫秒） |

### 角色分配

| 角色 | 数量 | 核心任务 | 优先级 |
|------|------|----------|--------|
| `generator` | 200 | 执行视频生成（故事板→AI视频→ffmpeg拼接） | P1 |
| `evolver` | 50 | 运行参数进化（Bandit优化、A/B测试） | P2 |
| `qa` | 30 | 执行质检（120门质量检测、合规审查） | P3 |
| `creative` | 20 | 优化创意（钩子精炼、角度评分、提示词进化） | P2 |

### Agent 工作循环

```javascript
// agent-worker.js 伪代码
while (running) {
  // 1. 从HQ获取当前最优参数
  params = await fetchParamsFromHQ();

  // 2. 领取任务
  task = await claimTask();

  // 3. 执行任务（根据角色不同）
  result = await executeTask(task, params);

  // 4. 上报遥测数据
  await reportTelemetry({
    taskId: task.id,
    params: params,
    signals: extractImplicitSignals(result),
    performance: { duration, cost, quality }
  });

  // 5. 本地参数微调（evolver角色）
  if (role === 'evolver') {
    localBanditUpdate(result);
  }

  // 6. 等待下次任务
  await sleep(1000);
}
```

---

## API 参考

### 基础信息

- **Base URL**: `http://localhost:8088`
- **Content-Type**: `application/json`
- **Health Check**: `GET /api/health`

### 端点列表

#### `POST /api/generate` — 生成视频

请求体：
```json
{
  "product": "便携榨汁杯",
  "platform": "douyin",
  "durationSec": 30,
  "count": 1,
  "imageB64": "data:image/jpeg;base64,/9j/4AAQ...",
  "angleFamily": "痛点解决",
  "hookType": "悬念型",
  "options": {
    "voiceOver": true,
    "subtitles": true,
    "bgm": true,
    "quality": "high"
  }
}
```

参数说明：

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `product` | string | 是 | 产品名称 |
| `platform` | string | 否 | 目标平台：`douyin`/`kuaishou`/`xiaohongshu`/`wechat`，默认 `douyin` |
| `durationSec` | number | 否 | 视频时长（15/30/60），默认 30 |
| `count` | number | 否 | 生成数量（1-200），默认 1 |
| `imageB64` | string | 否 | 产品图片 Base64 |
| `angleFamily` | string | 否 | 角度族（留空自动选择） |
| `hookType` | string | 否 | 钩子类型（留空自动匹配） |
| `options` | object | 否 | 生成选项 |

响应：
```json
{
  "jobId": "vf_abc123",
  "status": "queued",
  "estimatedTimeSec": 120,
  "queuePosition": 3
}
```

#### `GET /api/jobs/:id` — 查询任务状态

响应：
```json
{
  "jobId": "vf_abc123",
  "state": "succeeded",
  "progress": 100,
  "currentStep": "质检完成",
  "storyboard": [
    {
      "scene": 1,
      "shot": "特写",
      "visual": "疲惫的上班族看着桌上咬了一口的苹果",
      "voiceover": "每天想吃点水果，却懒得洗切榨...",
      "subtitle": "吃水果好麻烦",
      "duration": 3,
      "transition": "cut",
      "angle": "痛点呈现",
      "hook": "共鸣型"
    }
  ],
  "videoUrl": "http://localhost:8088/out/vf_abc123.mp4",
  "qualityScore": 87,
  "generatedAt": "2025-01-15T08:30:00Z",
  "cost": {
    "tokens": 4500,
    "videoCredits": 8,
    "estimatedUSD": 0.42
  }
}
```

状态值：`queued` → `analyzing` → `scripting` → `storyboarding` → `generating` → `stitching` → `qa` → `succeeded` / `failed`

#### `POST /api/hybrid-cut` — AI+素材混剪

请求体：
```json
{
  "product": "补水面膜",
  "materials": [
    { "type": "video", "url": "https://.../user-video1.mp4", "segment": "0-5" },
    { "type": "image", "url": "https://.../product-shot.jpg" }
  ],
  "hookPrompt": "熬夜后皮肤状态差？",
  "voiceId": "voice1",
  "outputDuration": 30
}
```

#### `POST /api/matrix` — 矩阵批量生成

请求体：
```json
{
  "product": "便携榨汁杯",
  "count": 200,
  "variations": {
    "hooks": true,
    "personas": true,
    "scenes": true,
    "tempos": true
  },
  "dedupThreshold": 0.75
}
```

#### `GET /api/config` — 获取当前配置

响应：
```json
{
  "version": "3.0",
  "skillParams": {
    "hookWeights": { "悬念型": 0.35, "震惊型": 0.25, ... },
    "angleWeights": { "痛点解决": 0.4, "功效证明": 0.3, ... },
    "generationParams": { "temperature": 0.8, "topP": 0.9 }
  },
  "telemetryEnabled": true,
  "swarmStatus": {
    "totalAgents": 300,
    "running": 287,
    "tasksCompleted": 15420
  }
}
```

#### `GET /api/health` — 健康检查

响应：
```json
{
  "status": "healthy",
  "timestamp": "2025-01-15T08:30:00Z",
  "version": "3.0",
  "services": {
    "core": "up",
    "hq": "up",
    "videoGen": "up",
    "tts": "up"
  }
}
```

#### `GET /api/stats` — 系统统计

响应：
```json
{
  "totalVideos": 15240,
  "totalAgents": 300,
  "activeAgents": 287,
  "queueDepth": 12,
  "avgQualityScore": 84.5,
  "avgGenerationTime": 98.2,
  "today": {
    "videos": 342,
    "tokens": 1540000,
    "costUSD": 28.4
  }
}
```

---

## 文件结构

```
VIDEO-FACTORY/
├── live-server.js              # 核心引擎入口
├── start-swarm.js              # 300-Agent蜂群启动器
├── package.json                # 依赖配置
├── .env.example                # 环境变量模板
├── Dockerfile                  # 主服务Docker镜像
├── Dockerfile.hq               # HQ服务Docker镜像
├── docker-compose.yml          # 完整部署栈
│
├── core/                       # 核心引擎
│   ├── creative/               # 创意引擎
│   │   ├── hook-engine.js      # 10族钩子库 + 匹配算法
│   │   ├── angle-engine.js     # 6大角度族扫描
│   │   ├── matrix-engine.js    # 矩阵变体生成
│   │   ├── script-writer.js    # 脚本生成器
│   │   └── prompt-templates.js # AI提示词模板
│   ├── video/                  # 视频处理
│   │   ├── video-generator.js  # AI视频生成（可灵/丽帧/Pika）
│   │   ├── hybrid-cut.js       # 混剪引擎
│   │   ├── ffmpeg-utils.js     # ffmpeg工具封装
│   │   ├── storyboard-split.js # 故事板拆分
│   │   └── video-downloader.js # 视频下载
│   ├── audio/                  # 音频处理
│   │   ├── voice-lock.js       # 声纹锁（统一音色）
│   │   ├── tts-client.js       # TTS服务客户端
│   │   └── bgm-selector.js     # 背景音乐选择
│   ├── quality/                # 质检系统
│   │   ├── QualityGate.js      # 120门质检引擎
│   │   ├── compliance-check.js # 合规检查（AI标识/广告法）
│   │   └── auto-retry.js       # 自动重修逻辑
│   ├── evolution/              # 自进化系统
│   │   ├── SkillParams.js      # 可训练参数定义
│   │   ├── EvolutionEngine.js  # Bandit进化引擎
│   │   ├── Telemetry.js        # 遥测数据收集
│   │   ├── VersionChecker.js   # 版本检查与更新
│   │   └── bandit.js           # Multi-Armed Bandit实现
│   └── swarm/                  # 蜂群系统
│       ├── agent-worker.js     # Agent工作进程
│       ├── agent-protocol.js   # Agent通信协议
│       └── data-loopback.js    # 数据回流管道
│
├── hq/                         # 总部系统
│   ├── server.js               # HQ服务入口
│   ├── collector.js            # 遥测数据收集
│   ├── aggregate.js            # 全局聚合分析
│   ├── leaderboard.js          # 参数排行榜
│   ├── agent-coordinator.js    # Agent协调器
│   └── params-store.js         # 参数存储
│
├── web/public/                 # Web UI
│   ├── index.html              # 主界面
│   ├── style.css               # 样式
│   └── app.js                  # 前端逻辑
│
├── bin/                        # CLI工具
│   └── cli.js                  # 命令行入口
│
├── tts-server/                 # TTS服务
│   ├── server.py               # TTS服务端
│   ├── voices/                 # 音色库
│   └── Dockerfile              # TTS镜像
│
├── MASTER/                     # 文档总纲
│   ├── SKILL.md                # 本文件
│   ├── ARCHITECTURE.md         # 架构设计
│   ├── API.md                  # API详细文档
│   └── CHANGELOG.md            # 版本变更
│
├── presets/                    # 预设配置
│   ├── hooks/                  # 钩子模板库
│   ├── angles/                 # 角度族模板
│   └── platforms/              # 平台配置
│
├── references/                 # 参考文档
│   ├── kling-api.md            # 可灵API文档
│   ├── luma-api.md             # 丽帧API文档
│   └── compliance/             # 合规参考
│
└── logs/                       # 日志目录
    ├── agents/                 # Agent日志
    └── swarm/                  # 蜂群日志
```

---

## 进化机制详解

### 参数空间

```javascript
// SkillParams.js — 可训练参数定义
const SKILL_PARAMS = {
  // 钩子权重
  hookWeights: {
    '悬念型':  { default: 0.20, range: [0.05, 0.40] },
    '震惊型':  { default: 0.15, range: [0.05, 0.35] },
    '反转型':  { default: 0.12, range: [0.05, 0.30] },
    '共鸣型':  { default: 0.18, range: [0.05, 0.35] },
    '提问型':  { default: 0.10, range: [0.05, 0.25] },
    '数字型':  { default: 0.08, range: [0.03, 0.20] },
    '挑战型':  { default: 0.07, range: [0.03, 0.20] },
    '故事型':  { default: 0.05, range: [0.02, 0.15] },
    '对比型':  { default: 0.03, range: [0.01, 0.10] },
    '权威型':  { default: 0.02, range: [0.01, 0.08] }
  },

  // 角度权重
  angleWeights: {
    '痛点解决':   { default: 0.35, range: [0.20, 0.50] },
    '功效证明':   { default: 0.25, range: [0.15, 0.40] },
    '场景代入':   { default: 0.20, range: [0.10, 0.35] },
    '情感共鸣':   { default: 0.10, range: [0.05, 0.25] },
    '社会认同':   { default: 0.07, range: [0.03, 0.20] },
    '对比突出':   { default: 0.03, range: [0.01, 0.15] }
  },

  // 生成参数
  generation: {
    temperature:      { default: 0.8,  range: [0.5, 1.0] },
    topP:             { default: 0.9,  range: [0.7, 1.0] },
    scriptLength:     { default: 150,  range: [100, 300] },
    shotCount:        { default: 6,    range: [4, 10] },
    maxRetryAttempts: { default: 3,    range: [1, 5] }
  },

  // 视频参数
  video: {
    preferredRatio:  { default: '9:16',  options: ['9:16', '16:9', '1:1'] },
    minDuration:     { default: 15,      range: [10, 30] },
    maxDuration:     { default: 60,      range: [30, 120] },
    transitionStyle: { default: 'smooth', options: ['cut', 'smooth', 'zoom'] }
  }
};
```

### Bandit 优化流程

```
每个 Evolver Agent:
  1. 从 HQ 拉取全局参数 P_global
  2. 在 P_global 邻域随机扰动得到 P_local
  3. 用 P_local 生成一批视频
  4. 收集隐式信号 S = {完成率, 耗时, 点击率, 完播率}
  5. 计算奖励 R = weightedSum(S)
  6. 上报 {P_local, R, N} 到 HQ

HQ 聚合器:
  1. 收集所有 Evolver 的上报
  2. 按参数值聚类（k-means）
  3. 每类计算平均奖励置信区间
  4. UCB 选择：argmax(μ + c·√(lnT/Ni))
  5. 生成新的全局最优参数 P_global'
  6. 验证门控：P_global' 必须显著优于 P_global
  7. 通过则下发，不通过则保留旧参数
```

### 验证门控

```javascript
function shouldAdopt(newParams, currentParams, results) {
  // 1. 样本量检查
  if (results.new.n < 100 || results.current.n < 100) return false;

  // 2. 均值比较
  const meanNew = results.new.meanReward;
  const meanCurrent = results.current.meanReward;
  const lift = (meanNew - meanCurrent) / meanCurrent;

  // 3. 置信区间检查（95%置信度）
  const ciNew = confidenceInterval(results.new.rewards, 0.95);
  const ciCurrent = confidenceInterval(results.current.rewards, 0.95);

  // 4. 严格门控：新参数的下界必须 > 旧参数的上界
  const significantlyBetter = ciNew.lower > ciCurrent.upper;

  // 5. 最小提升阈值
  const minLift = 0.05; // 5%

  return significantlyBetter && lift > minLift;
}
```

---

## CLI 工具

```bash
# 安装全局命令
npm link

# 生成视频
vf generate "便携榨汁杯" --platform douyin --duration 30

# 矩阵生成
vf matrix "补水面膜" --count 200 --variations all

# 混剪
vf hybrid-cut "益生菌" --materials ./my-videos/ --hook "肠道问题？"

# 查看状态
vf status
vf jobs
vf stats

# 管理蜂群
vf swarm start --agents 300
vf swarm status
vf swarm stop

# 查看进化参数
vf params show
vf params history

# 导出数据
vf export --format json --since 2025-01-01
```

---

## 合规

### AI生成内容标识

遵守中国《人工智能生成合成内容标识办法》（2025-09-01强制施行）：

```javascript
// compliance-check.js
const COMPLIANCE_RULES = {
  // 显式标识：视频开头2秒添加「AI生成」标签
  explicitLabel: {
    enabled: true,
    duration: 2,       // 秒
    position: 'top-left',
    style: '黑底白字，不透明度80%'
  },

  // 隐式标识：数字水印嵌入
  implicitWatermark: {
    enabled: true,
    type: '频域水印',
    payload: 'AI-generated by VIDEO-FACTORY'
  },

  // 元数据标识
  metadataLabel: {
    enabled: true,
    field: 'xmp:DigitalSourceType',
    value: 'trainedAlgorithmicMedia'
  }
};
```

### 广告法合规检查

自动检测并拦截：

| 违规类型 | 示例 | 处理方式 |
|----------|------|----------|
| 绝对化用语 | "最好"、"第一"、"顶级" | 自动替换为合规表述 |
| 未证功效 | "减肥"、"治疗" | 拦截并要求提供资质 |
| 虚假宣传 | "100%有效" | 替换为实测数据 |
| 违禁词 | "国家级"、"最高级" | 标红提醒 + 自动修改 |

### 分区合规

| 地区 | 要求 | 实现 |
|------|------|------|
| 中国大陆 | AI标识 + 广告法 + 平台规则 | 自动检测 + 修正 |
| 欧盟 | GDPR数据合规 + AI Act | 匿名遥测 + 透明披露 |
| 美国 | FTC披露要求 | AI生成声明 |

---

## 部署

### Docker 部署

```bash
# 构建并启动全部服务
docker-compose up -d

# 仅启动核心服务
docker-compose up -d video-factory hq

# 启动含监控栈
docker-compose --profile monitoring up -d

# 查看日志
docker-compose logs -f video-factory
docker-compose logs -f swarm-launcher

# 停止
docker-compose down

# 完全清除（含数据卷）
docker-compose down -v
```

### 环境要求

| 组件 | 最低 | 推荐 |
|------|------|------|
| CPU | 4核 | 16核 |
| 内存 | 8GB | 32GB |
| 磁盘 | 20GB SSD | 100GB NVMe |
| 网络 | 10Mbps | 100Mbps |
| Docker | 20.10+ | 最新 |

### 监控

启动监控栈后访问：
- Grafana: http://localhost:3000 (admin/admin)
- Prometheus: http://localhost:9090

关键监控指标：
- `vf_agents_running` — 运行中Agent数
- `vf_jobs_total` — 总任务数
- `vf_job_duration_seconds` — 任务耗时
- `vf_quality_score` — 质量评分
- `vf_cost_usd` — 成本

---

## License

MIT License — 详见 [LICENSE](LICENSE)

---

## 版本历史

| 版本 | 日期 | 变更 |
|------|------|------|
| 3.0 | 2025-01 | 300-Agent蜂群、SkillOpt自进化、混剪、矩阵200条 |
| 2.0 | 2024-11 | 多平台适配、120门质检、声纹锁 |
| 1.0 | 2024-09 | 初始版本：单条精品生成 |
