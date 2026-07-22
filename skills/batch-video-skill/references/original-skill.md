# SKILL.md -- 带货视频批量生产 Skill

> **Skill ID**: `batch-video-skill` | **版本**: v1.0 | **日期**: 2026-07
> **状态**: 生产就绪 | **架构**: 分层架构 + 管道过滤器 + 策略模式
> **作者**: 300 Agent 融合项目

---

## 目录

- [1. 元信息](#1-元信息)
- [2. 功能概述](#2-功能概述)
- [3. 安装与配置](#3-安装与配置)
- [4. 核心概念与术语](#4-核心概念与术语)
- [5. 初始化流程](#5-初始化流程)
- [6. 工作流程](#6-工作流程)
- [7. Gem 模板管理](#7-gem-模板管理)
- [8. 视频生成模型参考](#8-视频生成模型参考)
- [9. MCP 工具参考](#9-mcp-工具参考)
- [10. 故障排查](#10-故障排查)
- [11. 常见问题解答](#11-常见问题解答)
- [12. 提示词工程指南](#12-提示词工程指南)
- [13. 附录](#13-附录)

---

## 1. 元信息

### 1.1 Skill 标识

| 属性 | 值 |
|------|-----|
| **Skill ID** | `batch-video-skill` |
| **版本** | v1.0 |
| **状态** | 生产就绪 (Production Ready) |
| **最后更新** | 2026-07 |
| **架构风格** | 分层架构 + 管道过滤器 + 策略模式 |
| **Python 版本** | 3.11+ |
| **Pydantic 版本** | v2 |

### 1.2 一句话描述

本 Skill 是一套**带货视频批量生产系统**：将产品图片输入，通过 AI 四维度分析 --> 智能路由匹配 Gem 模板 --> 提示词生成 --> 多模型视频生成，最终输出专业带货视频。支持单产品处理和批量并发处理两种模式。

### 1.3 适用场景

| 场景 | 说明 | 典型用户 |
|------|------|----------|
| **电商运营** | 为大量商品快速生成带货视频 | 电商运营团队、自媒体卖家 |
| **品牌营销** | campaign 期间批量生产视频素材 | 品牌市场部、广告公司 |
| **内容创作** | 为不同平台（抖音/小红书/快手）生成适配视频 | MCN 机构、内容创作者 |
| **产品上新** | 新品发布时快速制作宣传视频 | 产品经理、电商卖家 |
| **节日促销** | 大促期间批量更新商品视频 | 电商运营、促销策划 |

### 1.4 前置要求

| 要求 | 说明 | 必需 |
|------|------|:----:|
| Python 3.11+ | 运行环境 | ✅ |
| Google Gemini Cookie | 调用 Gemini Web API (`GEMINI_SECURE_1PSID`) | ✅ |
| Google Service Account | 访问 Google Workspace (`GWS_SA_FILE`) | ✅ |
| 至少一个视频模型 API Key | 即梦/可灵/Vidu/Veo/Runway/Pika 任选一个 | ✅ |
| 网络访问 | 可访问 Google APIs 和视频模型 APIs | ✅ |
| 磁盘空间 | 建议 > 10GB（视频缓存） | ✅ |

### 1.5 依赖清单

```yaml
# 核心依赖
python: ">=3.11"
gemini-webapi: "^1.0"              # Gemini Web API 客户端
curl_cffi: "^0.6"                  # TLS 指纹模拟
pydantic: "^2.0"                   # 数据校验
aiohttp: "^3.9"                    # 异步 HTTP
asyncio: "内置"                     # 异步运行时
fastmcp: "^1.0"                    # MCP Server 框架
loguru: "^0.7"                     # 结构化日志

# 基础设施
structlog: "^24.0"                 # 结构化日志
prometheus_client: "^0.20"         # 监控指标（可选）
watchdog: "^3.0"                   # 文件热重载（可选）

# Google Workspace
gws: "^0.1"                        # gws CLI (Rust 二进制)
google-api-python-client: "^2.0"   # Google API 客户端
google-auth: "^2.0"                # Google 认证

# 外部服务
视频提供商: "即梦(seedance) / 可灵(kling) / Vidu(vidu) / Veo(veo3) / Runway(runway) / Pika(pika)"
```

---

## 2. 功能概述

### 2.1 核心能力清单

| 能力 | 说明 | 对应 MCP 工具 |
|------|------|--------------|
| **图片深度分析** | 4 维度（产品 / 人群 / 场景 / 风格）VLM 分析 | `analyze_image` (T04) |
| **智能路由匹配** | 14 维加权评分自动匹配最佳 Gem 模板 | `match_gem_template` (T05) |
| **提示词生成** | 调用 Gemini Gem 生成专业视频提示词 | `generate_prompt` (T06) |
| **多模型视频生成** | 支持 6+ 视频模型（即梦 / 可灵 / Vidu / Veo / Runway / Pika） | `generate_video_raw` (T07) |
| **批量任务管理** | 并发控制、断点续跑、进度追踪 | `generate_video_batch` (T02) |
| **单产品视频生成** | 一站式完整流程（分析→路由→提示词→视频） | `generate_video_single` (T01) |
| **批量进度查询** | 实时查询批次处理进度 | `get_batch_progress` (T03) |
| **Gem 模板管理** | 列出、刷新、查看可用模板 | `list_gems` (T08) |
| **结果交付** | 自动上传 Google Drive、生成汇总报告 | 内置能力 |
| **健康监控** | 系统组件健康检查、错误码体系 | `health_check` (T09) |
| **配置管理** | 四层配置叠加、热更新、配置查询 | `get_config` (T10) |

### 2.2 系统架构图

```
产品图片输入
    |
    v
+---------------------+    +---------------------+    +---------------------+    +---------------------+
|   图片分析           | -> |   智能路由           | -> |   提示词生成         | -> |   视频生成           |
| (4 维度 VLM)        |    | (14 维加权匹配)      |    | (Gem 调用)           |    | (6+ 模型适配器)      |
| ImageAnalyzer       |    | SmartRouter          |    | PromptGenerator      |    | VideoGenerationFacade|
+---------------------+    +---------------------+    +---------------------+    +----------+----------+
                                                                                         |
                                                                                         v
                                                                              +---------------------+
                                                                              |   结果交付           |
                                                                              | Drive 上传 / 报告    |
                                                                              | DeliveryManager      |
                                                                              +---------------------+
```

### 2.3 核心模块结构

```
batch-video-skill/
├── config_manager.py          # 配置管理器 (4层配置 + Pydantic校验)
├── image_analyzer.py          # 图片分析器 (4维度 VLM 分析)
├── smart_router.py            # 智能路由器 (14维加权评分)
├── gem_registry.py            # Gem 注册中心 (缓存 + Sheets同步)
├── prompt_generator.py        # 提示词生成器 (Gem调用 + 模板渲染)
├── video_generator.py         # 视频生成框架 (ABC + 熔断器 + 重试)
│   └── adapters/
│       ├── seedance.py        # 即梦适配器
│       ├── kling.py           # 可灵适配器
│       ├── vidu.py            # Vidu 适配器
│       ├── veo3.py            # Veo3 适配器
│       └── ...
├── batch_processor.py         # 批量处理器 (并发 + 断点续跑)
├── task_scheduler.py          # 任务调度器 (状态机 + 进度追踪)
├── mcp_server.py              # MCP Server (10个工具注册)
├── __main__.py                # 入口文件 (Server/CLI双模式)
└── SKILL.md                   # 本文档
```

### 2.4 支持的输入格式

| 属性 | 说明 |
|------|------|
| **图片格式** | JPG, PNG, WebP, GIF, BMP（单文件 < 20MB） |
| **输入方式** | 本地文件路径、URL |
| **批量输入** | 目录路径（自动扫描支持的图片文件） |

### 2.5 输出规格

| 属性 | 说明 |
|------|------|
| **视频格式** | MP4 (H.264) |
| **分辨率** | 480p / 720p / 1080p / 4K（依模型支持） |
| **时长** | 4-16 秒（默认 5 秒） |
| **音频** | 可选生成（依模型支持） |
| **交付方式** | 本地路径 + Google Drive URL（可选） |

---

## 3. 安装与配置

### 3.1 环境要求

- Python 3.11+
- 操作系统：Linux / macOS / Windows (WSL2 推荐)
- 网络：可访问 Google APIs 和视频模型 APIs
- 磁盘：建议 > 10GB（视频缓存）

### 3.2 安装步骤

```bash
# 步骤 1: 克隆代码仓库
git clone <repository-url> batch-video-skill
cd batch-video-skill

# 步骤 2: 创建虚拟环境
python -m venv .venv
source .venv/bin/activate  # Linux/macOS
# .venv\Scripts\activate   # Windows

# 步骤 3: 安装依赖
pip install -r requirements.txt

# 步骤 4: 安装 gws CLI（Google Workspace 工具）
cp gws /usr/local/bin/
chmod +x /usr/local/bin/gws
```

### 3.3 认证设置

**环境变量**:

```bash
# Gemini Cookie 认证
export GEMINI_SECURE_1PSID="your-cookie-value"
export GEMINI_SECURE_1PSIDTS="your-cookie-ts-value"  # 可选

# Google Workspace Service Account
export GWS_SA_FILE="/path/to/service-account.json"

# 视频提供商 API Key（至少配置一个）
export SEEDANCE_API_KEY="your-seedance-api-key"
export KLING_API_KEY="your-kling-api-key"
export VIDU_API_KEY="your-vidu-api-key"
export VEO_API_KEY="your-veo-api-key"
export RUNWAY_API_KEY="your-runway-api-key"
```

### 3.4 配置分层（优先级）

配置采用**四层叠加模型**，高层覆盖低层同名字段：

```
优先级: 最高 <-------------------------------------------------> 最低

第 4 层: 命令行参数 (CLI Arguments)     --provider kling --concurrency 5
第 3 层: 环境变量 (ENV)                 BVS_PROVIDER=kling
第 2 层: 用户配置文件 (config.yaml)     持久化用户偏好
第 1 层: 默认配置 (代码内置)            出厂默认值、回退基准
```

### 3.5 验证安装

调用 `health_check` 工具验证所有组件状态：

```python
# MCP 工具调用
health_check()

# 预期返回:
{
  "status": "healthy",           # healthy / degraded / unhealthy
  "version": "1.0.0",
  "checks": {
    "gemini_service": {"status": "healthy", "latency_ms": 120},
    "video_seedance": {"status": "healthy", "latency_ms": 450},
    "video_kling": {"status": "healthy", "latency_ms": 380},
    "workspace": {"status": "healthy", "latency_ms": 230}
  },
  "latency_ms": 1180
}
```

**预期结果**：所有 checks 状态为 `healthy`。如有 `degraded` 或 `unhealthy`，根据返回的 `message` 排查对应组件。

---

## 4. 核心概念与术语

### 4.1 术语表

| 术语 | 英文 | 定义 | 示例 |
|------|------|------|------|
| **Gem** | Gem | Google Gemini 的可定制化模型模板，带有预设提示词和微调参数 | "gem_001_超市疯抢模板" |
| **Gem 模板** | Gem Template | 一个 Gem 的完整配置，包含 ID、名称、适用场景、提示词模板、权重等 | Sheets 中的一行配置 |
| **智能路由** | Smart Routing | 根据图片分析结果自动匹配最佳 Gem 模板的过程 | lipstick 图 --> 美妆 Gem |
| **四维度分析** | 4-Dimension Analysis | 对产品图片从产品、人群、场景、风格四个维度的深度分析 | ImageFeatures 对象 |
| **视频提供商** | Video Provider | 提供视频生成 API 的服务商 | "seedance"(即梦)、"kling"(可灵) |
| **适配器** | Adapter | 统一不同视频提供商 API 差异的封装层 | SeedanceAdapter |
| **批次** | Batch Task | 一次提交包含多个产品的处理任务 | batch_20260701_001 |
| **断点续跑** | Checkpoint Resume | 批量任务中断后从上次进度继续执行的能力 | CheckpointManager |
| **三级降级** | Three-Level Degradation | 模型级 --> 提供商级 --> 服务级的故障降级策略 | 即梦失败 --> 可灵 --> 本地 Mock |
| **置信度** | Confidence | 路由匹配结果的可信程度 | 0.92 --> HIGH |
| **提示词** | Prompt | 驱动视频生成模型生成视频的自然语言描述 | "一支口红在樱花树下旋转..." |
| **ImageFeatures** | ImageFeatures | 图片分析输出的结构化特征向量（14 维） | 见 4.2 节 |
| **匹配分数** | Match Score | Gem 模板与产品特征的匹配程度，0-1 之间 | 0.92 |

### 4.2 核心数据结构

#### ImageFeatures（图片特征向量 -- 14 维）

```json
{
  "category": "makeup",
  "product_type": "lipstick",
  "price_tier": "mid_range",
  "brand_tier": "premium",
  "detected_selling_points": ["trending", "premium_packaging"],
  "target_gender": "female",
  "target_age": "gen_z",
  "estimated_spending_power": "middle",
  "inferred_personas": ["fashion_forward", "quality_first"],
  "inferred_marketing_scene": "new_launch",
  "inferred_usage_scenes": ["daily", "gift"],
  "target_platform": "douyin",
  "inferred_video_style": "fast_paced",
  "inferred_visual_tone": "bright_vibrant"
}
```

**字段说明**:

| 字段 | 类型 | 说明 | 可选值 |
|------|------|------|--------|
| `category` | str | 产品品类（25 种） | skincare, makeup, fragrance, phone, shoes, snack, furniture 等 |
| `product_type` | str | 细分产品类型 | 口红, 面膜, 连衣裙, 运动鞋 等 |
| `price_tier` | str | 价格带（5 档） | budget, affordable, mid_range, premium, luxury |
| `brand_tier` | str | 品牌调性（5 档） | mass, mid_market, premium, luxury, d2c |
| `detected_selling_points` | list | 卖点特征（13 种） | trending, natural_ingredients, premium_packaging 等 |
| `target_gender` | str | 目标性别（4 种） | male, female, unisex, not_specified |
| `target_age` | str | 目标年龄（6 档） | gen_z, gen_y, gen_x, silver, senior, all_ages |
| `estimated_spending_power` | str | 消费能力（4 档） | economy, middle, affluent, ultra_high |
| `inferred_personas` | list | 人群画像标签（12 种） | fashion_forward, tech_enthusiast, minimalist 等 |
| `inferred_marketing_scene` | str | 营销场景（8 种） | new_launch, flash_sale, seasonal_promo 等 |
| `inferred_usage_scenes` | list | 使用场景（9 种） | daily, gift, travel, work 等 |
| `target_platform` | str | 目标平台（8 种） | douyin, xiaohongshu, kuaishou, taobao 等 |
| `inferred_video_style` | str | 视频风格（10 种） | fast_paced, product_showcase, tutorial 等 |
| `inferred_visual_tone` | str | 画面调性（10 种） | bright_vibrant, warm_cozy, dark_moody 等 |

#### MatchResult（匹配结果）

```json
{
  "matches": [
    {"gem_id": "gem_005", "gem_name": "美妆快闪模板", "score": 0.92, "breakdown": {"category": 1.0}},
    {"gem_id": "gem_003", "gem_name": "护肤日常模板", "score": 0.78, "breakdown": {"category": 0.8}}
  ],
  "confidence_level": "high",
  "final_score": 0.92,
  "top_candidate_id": "gem_005",
  "reasoning": "品类匹配(美妆)，平台匹配(抖音)，风格匹配(快节奏)"
}
```

### 4.3 权重配置（默认）

智能路由采用**14 维加权评分**，默认权重分配：

```
总分 = 产品维度(40%) + 人群维度(23%) + 场景维度(25%) + 风格维度(12%)

产品维度 (40%):
  - category: 15%        (品类匹配)
  - product_type: 8%     (产品类型)
  - price_tier: 7%       (价格带)
  - brand_tier: 5%       (品牌调性)
  - selling_points: 5%   (卖点特征)

人群维度 (23%):
  - target_gender: 8%    (目标性别)
  - target_age: 7%       (目标年龄)
  - spending_power: 5%   (消费能力)
  - persona_tags: 3%     (人群画像)

场景维度 (25%):
  - marketing_scene: 12% (营销场景)
  - usage_scenes: 8%     (使用场景)
  - target_platform: 5%  (目标平台)

风格维度 (12%):
  - video_style: 8%      (视频风格)
  - visual_tone: 4%      (画面调性)
```

---

## 5. 初始化流程

### 5.1 首次运行检查清单

- [ ] Python 3.11+ 已安装
- [ ] 虚拟环境已创建并激活
- [ ] `requirements.txt` 依赖已安装
- [ ] `gws` CLI 二进制已安装并在 PATH 中
- [ ] 环境变量 `GEMINI_SECURE_1PSID` 已设置
- [ ] 环境变量 `GWS_SA_FILE` 已设置
- [ ] 至少一个视频提供商 API Key 已设置
- [ ] Google Sheets ID 已配置（用于 Gem 元数据和任务队列）
- [ ] 调用 `health_check()` 返回全部 healthy

### 5.2 配置加载顺序

```
启动
  |
  v
加载默认配置 (代码内置)
  |
  v
读取环境变量 (BVS_前缀)
  |
  v
验证配置完整性 (Pydantic Schema 校验)
  |
  v
初始化各模块:
  - ConfigManager       (配置管理)
  - GeminiClient        (Gemini 连接)
  - GemRegistry         (Gem 注册中心: 从缓存恢复 -> 首次同步)
  - VideoGeneratorFacade(视频生成外观: 注册所有适配器)
  - TaskScheduler       (任务调度器)
  |
  v
执行健康检查 (health_check)
  |
  v
就绪
```

### 5.3 连接验证

首次启动后，依次验证以下连接：

1. **Gemini 连接**: `health_check` 检查 `gemini_service` 状态
2. **视频提供商连接**: `health_check` 检查各 `video_*` 状态
3. **Workspace 连接**: `health_check` 检查 `workspace` 状态
4. **Gem 同步**: 调用 `list_gems(refresh=true)` 确认 Gem 列表已加载

---

## 6. 工作流程

### 6.1 WF-001: 单产品视频生成

#### 概述

| 属性 | 说明 |
|------|------|
| **触发条件** | 用户请求为单个产品生成带货视频 |
| **输入** | 产品图片路径 + 可选参数（描述、偏好 Gem、偏好提供商等） |
| **输出** | VideoResult（视频 URL/本地路径、匹配信息、花费、耗时） |
| **使用工具** | `generate_video_single`（T01，一键式）或手动组合 T04+T05+T06+T07 |
| **预计耗时** | 30-180 秒（取决于视频模型） |

#### 完整步骤

```
输入: image_path, [user_description], [preferred_gem_id], [preferred_provider]
  |
  v
[步骤 1] 输入验证
  - 检查图片文件存在且格式支持 (jpg/png/webp)
  - 检查文件大小 < 20MB
  - 失败处理: 返回 C002 错误，提示用户检查文件
  |
  v
[步骤 2] 图片四维度分析 (analyze_image, T04)
  - 调用 VLM 进行 4 维度分析: 产品 / 人群 / 场景 / 风格
  - 输出: ImageFeatures 对象 (14 维特征向量)
  - 缓存: 图片哈希命中缓存则跳过 API 调用
  - 失败处理: 降级为默认特征，继续流程（降低置信度预期）
  |
  v
[步骤 3] 智能路由匹配 (match_gem_template, T05)
  - 如用户指定 preferred_gem_id: 跳过匹配，直接使用
  - 否则: 14 维加权评分，返回 Top-3 匹配结果
  - 置信度 HIGH (>0.85): 直接使用 Top-1
  - 置信度 MEDIUM (0.70-0.85): 推荐 Top-3，用户选择
  - 置信度 LOW (<0.70): 使用默认 Gem，记录反馈
  |
  v
[步骤 4] 提示词生成 (generate_prompt, T06)
  - 调用选定的 Gem 模板生成视频提示词
  - 注入产品特征 (ImageFeatures) 和用户描述
  - 自动校验提示词完整性和合规性
  - 失败处理: 使用模板默认提示词继续
  |
  v
[步骤 5] 视频生成 (generate_video_raw, T07)
  - 如用户指定 preferred_provider: 直接使用
  - 否则: 使用默认提供商 (seedance)
  - 提交视频生成任务，异步轮询等待完成
  - 三级降级链: seedance --> kling --> vidu --> veo --> runway
  - 输出: VideoResult（视频 URL、花费、耗时）
  |
  v
[步骤 6] 结果交付
  - 下载视频到本地 output/videos/ 目录
  - 如配置 upload_to_drive=true: 上传到 Google Drive
  - 生成生成报告（含匹配信息、提示词、花费等）
  |
  v
[步骤 7] 记录日志与返回
  - 记录任务完成日志
  - 返回 VideoResult 给用户
```

#### 代码示例

```python
# 方式 1: 一键生成（推荐）
result = await generate_video_single(
    image_path="/products/lipstick.jpg",
    user_description="玫瑰色哑光口红，面向年轻女性",
    output_resolution="1080p",
    output_duration=5
)
# 返回: {task_id, status, video_url, video_local_path, matched_gem_id, ...}

# 方式 2: 分步调用（用于调试或自定义流程）
# 步骤 2: 图片分析
features = await analyze_image(
    image_path="/products/lipstick.jpg",
    dimensions=["product", "audience", "scene", "style"]
)

# 步骤 3: 智能路由
match = await match_gem_template(
    features=features["features"],
    top_n=3
)

# 步骤 4: 提示词生成
prompt = await generate_prompt(
    image_path="/products/lipstick.jpg",
    gem_id=match["matches"][0]["gem_id"]
)

# 步骤 5: 视频生成
video = await generate_video_raw(
    prompt=prompt["prompt"],
    reference_image="/products/lipstick.jpg",
    provider="seedance",
    resolution="1080p"
)
```

#### 关键决策点

| 决策 | 条件 | 行为 |
|------|------|------|
| 跳过图片分析 | 缓存命中 | 直接返回缓存的 ImageFeatures |
| 跳过路由匹配 | 用户指定 preferred_gem_id | 直接使用指定 Gem |
| 跳过默认提供商 | 用户指定 preferred_provider | 直接使用指定提供商 |
| 降级处理 | 步骤 2 分析失败 | 使用默认特征，降低置信度 |
| 降级处理 | 步骤 3 匹配失败 | 使用 fallback Gem |
| 降级处理 | 步骤 4 提示词失败 | 使用模板默认提示词 |
| 降级处理 | 步骤 5 视频生成失败 | 走三级降级链 |

---

### 6.2 WF-002: 批量视频生成

#### 概述

| 属性 | 说明 |
|------|------|
| **触发条件** | 用户请求处理多个产品 |
| **输入** | 产品图片目录 + 可选参数（批次名称、并发数等） |
| **输出** | BatchResult（批次 ID、统计、报告路径、Drive 链接） |
| **使用工具** | `generate_video_batch` (T02) + `get_batch_progress` (T03) |
| **预计耗时** | 取决于产品数量和并发度，约 (N/concurrency) * 90 秒 |

#### 完整步骤

```
输入: image_dir, [batch_name], [max_concurrency], [...]
  |
  v
[步骤 1] 扫描目录
  - 收集所有支持的图片文件 (.jpg, .jpeg, .png, .webp)
  - 如为空目录: 返回 C002 错误
  |
  v
[步骤 2] 创建批次记录
  - 生成唯一 batch_id (格式: batch_YYYYMMDD_NNN)
  - 写入 Google Sheets batch-summary 表
  - 记录批次名称、总任务数、状态=running
  |
  v
[步骤 3] 初始化并发控制
  - 创建信号量 semaphore(max_concurrency)
  - 初始化 CheckpointManager、ProgressTracker
  |
  v
[步骤 4] 加载断点（如存在）
  - 检查是否有未完成的中断记录
  - 已完成任务跳过，仅处理 pending 任务
  |
  v
[步骤 5] 并发处理每个产品
  - 按 max_concurrency 分批执行
  - 每个产品调用 WF-001 的步骤 2-5（分析-->路由-->提示词-->视频）
  - 错误隔离: 单任务失败不影响其他任务
  - 实时更新进度到 Sheets（每 30 秒）
  |
  v
[步骤 6] 汇总统计
  - 统计: 成功数、失败数、总花费、总耗时
  - 生成批次汇总报告 (Markdown + Google Docs)
  |
  v
[步骤 7] 上传视频到 Drive
  - 创建批次文件夹（命名: batch_{batch_id}_{date}）
  - 批量上传所有成功视频
  |
  v
[步骤 8] 更新批次状态
  - Sheets 中更新状态为 completed
  |
  v
输出: BatchResult
```

#### 代码示例

```python
# 启动批量任务
batch = await generate_video_batch(
    image_dir="/products/summer_campaign/",
    batch_name="夏季_campaign_7月",
    max_concurrency=3,
    output_resolution="1080p",
    output_duration=5,
    continue_on_error=True,
    upload_to_drive=True
)
# 返回: {batch_id, total_tasks, status, message}

# 轮询查询进度
import asyncio
while True:
    progress = await get_batch_progress(batch_id=batch["batch_id"])
    print(f"进度: {progress['progress_percent']}% "
          f"({progress['completed']}/{progress['total']}) "
          f"失败: {progress['failed']}")
    if progress["is_complete"]:
        break
    await asyncio.sleep(10)
```

#### 关键机制

| 机制 | 说明 |
|------|------|
| **并发控制** | asyncio.Semaphore，默认 3 并发，最大 10 |
| **断点续跑** | CheckpointManager 每 60 秒保存进度，中断后自动跳过已完成任务 |
| **错误隔离** | 单个产品失败不影响其他产品处理 |
| **进度追踪** | ProgressTracker 每 30 秒更新 Google Sheets |
| **批次命名** | `batch_YYYYMMDD_NNN` 格式，全局唯一 |
| **任务状态机** | PENDING -> ANALYZING -> ROUTING -> PROMPT_GENERATING -> VIDEO_GENERATING -> COMPLETED/FAILED |

---

### 6.3 WF-003: 添加新 Gem 模板

#### 概述

| 属性 | 说明 |
|------|------|
| **触发条件** | 需要新增视频风格模板 |
| **方式** | 配置驱动，**零代码改动** |
| **预计生效** | 60 秒内自动同步，或手动调用 `list_gems(refresh=true)` 立即生效 |

#### 完整步骤

```
步骤 1: 在 Google Sheets (gem_metadata_sheet) 中新增一行

必填字段:
  - gem_id: 唯一标识 (格式: "gem_NNN_名称")
  - gem_name: 显示名称
  - gem_type: 类型（促销类/品牌类/剧情类/开箱类/测评类）
  - is_active: 是否启用 (1/0)
  - product_categories: 适用品类 (逗号分隔)
  - marketing_scenes: 营销场景 (逗号分隔)
  - target_audiences: 目标人群 (逗号分隔)
  - video_styles: 视频风格 (逗号分隔)
  - platforms: 目标平台 (逗号分隔)

可选字段:
  - notes: 备注说明
  - tones: 画面调性
  - age_ranges: 年龄范围
  - gender: 性别

  |
  v
步骤 2: 系统自动处理（无需人工干预）
  a. 检测新行（每 60 秒自动同步 Sheets）
  b. 验证配置完整性（Schema 校验）
  c. 索引到 GemRegistry（立即可用于路由匹配）
  d. 记录变更日志

  |
  v
步骤 3: 验证新 Gem
  - 调用 list_gems(refresh=true) 确认新模板出现在列表中
  - 调用 match_gem_template 测试匹配效果
  - 可选：调用 generate_prompt + generate_video_raw 生成测试视频
```

#### 代码示例

```python
# 验证新 Gem 是否生效
gems = await list_gems(refresh=True)
print(f"共有 {gems['total']} 个 Gem 模板")

# 测试匹配
match = await match_gem_template(
    features={"category": "makeup", "target_platform": "douyin"},
    top_n=3
)
for m in match["matches"]:
    print(f"  - {m['gem_name']}: {m['score']}")
```

---

### 6.4 WF-004: 故障处理

#### 概述

| 属性 | 说明 |
|------|------|
| **触发条件** | 系统报错或行为异常 |
| **处理原则** | 自动恢复优先，人工干预兜底 |
| **核心机制** | 三级降级 + 自动重试 + 熔断保护 |

#### 通用故障处理流程

```
遇到错误
  |
  v
[步骤 1] 识别错误类型
  - 查看 error_code（C/S/N/T/R/F/E 开头）
  - 查看 retryable 字段
  |
  v
[步骤 2] 按错误分类处理

  +-- 客户端错误 (C001-C499) -->
  |   检查输入参数 --> 查看参数说明和示例 --> 修正后重试
  |
  +-- 服务端错误 (S001-S199) -->
  |   自动重试（指数退避）--> 如 3 次仍失败，触发降级 --> 记录到错误日志
  |
  +-- 网络错误 (N001-N099) -->
  |   自动重试 --> 检查网络连接 --> 如持续失败，切换 Provider
  |
  +-- 任务错误 (T001-T299) -->
  |   查看任务状态 --> 如可重试，自动重试 --> 如不可重试，记录失败原因
  |
  +-- 配置错误 (F001-F099) -->
  |   检查配置文件 --> 验证配置格式 --> 修正后重启或热重载
  |
  +-- 系统错误 (E001-E099) -->
      调用 health_check 查看系统状态 --> 查看日志文件 --> 联系支持
```

#### 三级降级链

```
模型级降级: 同一 Provider 内切换模型版本
  例: seedance-2.0-standard --> seedance-2.0-mini

Provider 级降级: 切换到其他视频提供商
  降级链: seedance --> kling --> vidu --> veo --> runway --> pika

服务级降级: 所有 Provider 失败
  返回 Mock 视频 + 详细错误报告
```

#### 重试策略矩阵

| 错误类型 | 退避策略 | 基础延迟 | 最大延迟 | 最大重试 |
|----------|----------|:--------:|:--------:|:--------:|
| 速率限制 (429) | 固定间隔 | Retry-After | 120s | 10 |
| 服务器过载 (503) | 指数退避 | 1s | 60s | 5 |
| 连接超时 | 指数退避 | 1s | 30s | 5 |
| 读取超时 | 指数退避 | 3s | 120s | 5 |
| 视频生成失败 | 指数退避 | 5s | 120s | 3 |

---

## 7. Gem 模板管理

### 7.1 Gem 模板结构

每个 Gem 模板是一个完整的配置记录：

| 属性 | 类型 | 必填 | 说明 |
|------|------|:----:|------|
| `gem_id` | str | ✅ | 唯一标识，如 `gem_001_美妆快闪` |
| `gem_name` | str | ✅ | 显示名称 |
| `gem_type` | str | ✅ | 类型：促销类/品牌类/剧情类/开箱类/测评类 |
| `is_active` | bool | ✅ | 是否启用 |
| `product_categories` | str | ✅ | 适用品类（逗号分隔） |
| `marketing_scenes` | str | ✅ | 营销场景（逗号分隔） |
| `target_audiences` | str | ✅ | 目标人群（逗号分隔） |
| `video_styles` | str | ✅ | 视频风格（逗号分隔） |
| `platforms` | str | ✅ | 目标平台（逗号分隔） |
| `notes` | str | — | 模板描述 |
| `tones` | str | — | 画面调性 |
| `age_ranges` | str | — | 年龄范围 |
| `gender` | str | — | 性别 |

### 7.2 添加/修改/禁用模板

| 操作 | 方式 | 生效时间 |
|------|------|----------|
| **添加** | 在 Sheets gem-metadata 表新增一行 | 60 秒自动同步 |
| **修改** | 编辑 Sheets 中对应行 | 60 秒自动同步 |
| **禁用** | 将 `is_active` 改为 `0` | 60 秒自动同步 |
| **强制刷新** | 调用 `list_gems(refresh=true)` | 立即 |

---

## 8. 视频生成模型参考

### 8.1 支持的模型列表

| 提供商 | ID | 中文名 | 优势场景 | 最大分辨率 | 音频 | 推荐度 |
|--------|-----|--------|----------|:---------:|:----:|:------:|
| 即梦 | `seedance` | Seedance 2.0 | 电商/产品展示 | 1080p | ✅ | ★★★★★ |
| 可灵 | `kling` | Kling 3.0 | 人物/动作 | 1080p | ✅ | ★★★★☆ |
| 生数 | `vidu` | Vidu Q3 | 创意/艺术 | 1080p | ✅ | ★★★★☆ |
| Google | `veo3` | Veo 3.1 | 通用/多语言 | 4K | ✅ | ★★★☆☆ |
| Runway | `runway` | Gen-4 | 电影感/艺术 | 1080p | ✅ | ★★★☆☆ |
| Pika | `pika` | Pika 2.2 | 快速生成 | 720p | ❌ | ★★☆☆☆ |

### 8.2 模型能力对比

| 能力 | 即梦 | 可灵 | 生数 | Veo | Runway | Pika |
|------|:----:|:----:|:----:|:---:|:------:|:----:|
| 产品保持度 | ★★★★★ | ★★★★☆ | ★★★☆☆ | ★★★☆☆ | ★★★☆☆ | ★★☆☆☆ |
| 人物动作自然 | ★★★☆☆ | ★★★★★ | ★★★★☆ | ★★★☆☆ | ★★★★☆ | ★★☆☆☆ |
| 创意风格丰富 | ★★★☆☆ | ★★★☆☆ | ★★★★★ | ★★★★☆ | ★★★★★ | ★★★☆☆ |
| 生成速度 | ★★★★☆ | ★★★★☆ | ★★★☆☆ | ★★☆☆☆ | ★★☆☆☆ | ★★★★★ |
| 中文支持 | ★★★★★ | ★★★★★ | ★★★★☆ | ★★☆☆☆ | ★★☆☆☆ | ★★★☆☆ |
| 成本性价比 | ★★★★☆ | ★★★★☆ | ★★★☆☆ | ★★☆☆☆ | ★★☆☆☆ | ★★★★★ |

### 8.3 模型选型建议

| 场景 | 首选 | 备选 | 理由 |
|------|------|------|------|
| 电商产品展示 | 即梦(seedance) | 可灵(kling) | 产品保持度最好 |
| 人物出镜/口播 | 可灵(kling) | 生数(vidu) | 人物动作最自然 |
| 创意概念/艺术 | 生数(vidu) | Runway | 艺术风格最丰富 |
| 高分辨率需求 | Veo(veo3) | 即梦(seedance) | 唯一支持 4K |
| 快速大量生成 | Pika(pika) | 即梦(seedance) | 速度最快、单价最低 |
| 中文内容 | 即梦(seedance) | 可灵(kling) | 中文理解最好 |
| 复杂场景 | 可灵(kling) | 生数(vidu) | 物理模拟最好 |

### 8.4 各模型参数范围

| 提供商 | 最小时长 | 最大时长 | 支持分辨率 | 支持比例 |
|--------|:-------:|:-------:|:---------:|:-------:|
| 即梦 | 4s | 16s | 480p-1080p | 16:9, 9:16, 1:1, 4:3, 3:4, 21:9 |
| 可灵 | 4s | 16s | 480p-1080p | 16:9, 9:16, 1:1, 4:3, 3:4 |
| 生数 | 4s | 16s | 480p-1080p | 16:9, 9:16, 1:1 |
| Veo | 4s | 8s | 480p-4K | 16:9, 9:16, 1:1, 4:3, 3:4, 21:9 |
| Runway | 4s | 10s | 480p-1080p | 16:9, 9:16, 1:1, 4:3 |
| Pika | 4s | 5s | 480p-720p | 16:9, 9:16, 1:1 |

---

## 9. MCP 工具参考

### 9.1 工具总览表

| 编号 | 工具名 | 功能 | 常用场景 | 幂等性 | 超时 |
|:----:|--------|------|----------|:------:|:----:|
| T01 | `generate_video_single` | 单产品完整流程 | 生成单个视频 | 否 | 180s |
| T02 | `generate_video_batch` | 批量完整流程 | 批量生成视频 | 否 | 无上限 |
| T03 | `get_batch_progress` | 查询批量进度 | 查看批次状态 | 是 | 10s |
| T04 | `analyze_image` | 图片 4 维度分析 | 了解产品特征 | 是 | 30s |
| T05 | `match_gem_template` | 智能路由匹配 | 查找最佳模板 | 是 | 10s |
| T06 | `generate_prompt` | 生成视频提示词 | 获取专业提示词 | 否 | 60s |
| T07 | `generate_video_raw` | 原始视频生成 | 直接生成视频 | 否 | 600s |
| T08 | `list_gems` | 列出 Gem 模板 | 查看可用模板 | 是 | 10s |
| T09 | `health_check` | 健康检查 | 排查系统问题 | 是 | 10s |
| T10 | `get_config` | 获取配置 | 查看当前配置 | 是 | 5s |

### 9.2 T01: generate_video_single -- 单产品视频生成

| 属性 | 说明 |
|------|------|
| **描述** | 为单个产品图片生成完整带货视频（一站式流程：分析 --> 路由 --> 提示词 --> 视频） |
| **幂等性** | 否（每次调用生成新视频） |
| **超时** | 180 秒 |

**输入参数**:

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|:----:|--------|------|
| `image_path` | `str` | ✅ | — | 产品图片路径（本地路径或 URL） |
| `user_description` | `str` | — | `""` | 用户对产品的补充描述 |
| `preferred_gem_id` | `str` | — | `null` | 指定 Gem 模板 ID（跳过自动匹配） |
| `preferred_provider` | `str` | — | `null` | 指定视频提供商（如 "seedance"/"kling"） |
| `output_resolution` | `str` | — | `"1080p"` | 输出分辨率: 480p/720p/1080p/4k |
| `output_duration` | `int` | — | `5` | 视频时长（秒），4-16 |
| `generate_audio` | `bool` | — | `true` | 是否生成音频 |

**返回值**:

| 字段 | 类型 | 说明 |
|------|------|------|
| `task_id` | `str` | 任务唯一标识 |
| `status` | `str` | 最终状态: completed/failed |
| `video_url` | `str \| null` | 视频下载 URL |
| `video_local_path` | `str \| null` | 本地保存路径 |
| `matched_gem_id` | `str` | 匹配的 Gem 模板 ID |
| `matched_gem_name` | `str` | 匹配的 Gem 模板名称 |
| `match_score` | `float` | 匹配分数（0-1） |
| `generated_prompt` | `str` | 生成的视频提示词 |
| `video_provider` | `str` | 实际使用的视频提供商 |
| `cost_usd` | `float` | 花费（美元） |
| `generation_time_sec` | `float` | 生成耗时（秒） |

**使用示例**:

```json
{
  "image_path": "/products/lipstick.jpg",
  "user_description": "玫瑰色哑光口红，面向年轻女性",
  "output_resolution": "1080p",
  "output_duration": 5
}
```

---

### 9.3 T02: generate_video_batch -- 批量视频生成

| 属性 | 说明 |
|------|------|
| **描述** | 为多个产品图片批量生成带货视频（并发 + 断点续跑 + 进度追踪） |
| **幂等性** | 否 |
| **超时** | 无上限（后台异步执行） |

**输入参数**:

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|:----:|--------|------|
| `image_dir` | `str` | ✅ | — | 产品图片目录路径 |
| `batch_name` | `str` | — | `"未命名批次"` | 批次名称 |
| `user_description` | `str` | — | `""` | 通用产品描述 |
| `max_concurrency` | `int` | — | `3` | 最大并发数（1-10） |
| `preferred_provider` | `str` | — | `null` | 指定视频提供商 |
| `output_resolution` | `str` | — | `"1080p"` | 输出分辨率 |
| `output_duration` | `int` | — | `5` | 视频时长（秒） |
| `continue_on_error` | `bool` | — | `true` | 单个失败是否继续 |
| `upload_to_drive` | `bool` | — | `true` | 完成后上传 Google Drive |

**返回值**:

| 字段 | 类型 | 说明 |
|------|------|------|
| `batch_id` | `str` | 批次唯一标识 |
| `total_tasks` | `int` | 总任务数 |
| `status` | `str` | 批次状态: running/paused/completed/failed |
| `message` | `str` | 状态说明 |

---

### 9.4 T03: get_batch_progress -- 查询批量任务进度

| 属性 | 说明 |
|------|------|
| **描述** | 查询指定批次的处理进度 |
| **幂等性** | 是 |
| **超时** | 10 秒 |

**输入参数**:

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|:----:|------|
| `batch_id` | `str` | ✅ | 批次 ID |

**返回值**:

| 字段 | 类型 | 说明 |
|------|------|------|
| `batch_id` | `str` | 批次 ID |
| `batch_name` | `str` | 批次名称 |
| `total` | `int` | 总任务数 |
| `completed` | `int` | 已完成数 |
| `failed` | `int` | 失败数 |
| `pending` | `int` | 待处理数 |
| `analyzing` | `int` | 分析中数 |
| `routing` | `int` | 路由中数 |
| `prompt_generating` | `int` | 提示词生成中数 |
| `video_generating` | `int` | 视频生成中数 |
| `progress_percent` | `float` | 进度百分比（0-100） |
| `eta_seconds` | `float \| null` | 预计剩余时间（秒） |
| `total_cost_usd` | `float` | 累计花费（美元） |

---

### 9.5 T04: analyze_image -- 图片深度分析

| 属性 | 说明 |
|------|------|
| **描述** | 对产品图片进行 4 维度深度分析（产品/人群/场景/风格） |
| **幂等性** | 是（缓存结果，相同图片哈希直接返回缓存） |
| **超时** | 30 秒 |

**输入参数**:

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|:----:|--------|------|
| `image_path` | `str` | ✅ | — | 图片路径或 URL |
| `dimensions` | `list[str]` | — | `["product","audience","scene","style"]` | 分析维度 |
| `platform_hint` | `str` | — | `null` | 平台偏好 |

**返回值**:

| 字段 | 类型 | 说明 |
|------|------|------|
| `features` | `dict` | 14 维标准化特征向量 |
| `confidence_score` | `float` | 整体置信度（0-1） |
| `completeness_score` | `float` | 特征完整度（0-1） |
| `reliability` | `str` | 可靠性: reliable/moderate/unreliable |
| `cache_hit` | `bool` | 是否命中缓存 |
| `analysis_time_ms` | `int` | 分析耗时（毫秒） |

---

### 9.6 T05: match_gem_template -- 智能路由匹配

| 属性 | 说明 |
|------|------|
| **描述** | 根据图片分析结果匹配最佳 Gem 模板 |
| **幂等性** | 是 |
| **超时** | 10 秒 |

**输入参数**:

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|:----:|--------|------|
| `features` | `dict` | ✅ | — | 图片特征（analyze_image 的返回值） |
| `top_n` | `int` | — | `3` | 返回 Top N 匹配 |
| `min_score` | `float` | — | `0.5` | 最低匹配分数（0-1） |
| `platform_hint` | `str` | — | `null` | 平台偏好 |

**返回值**:

| 字段 | 类型 | 说明 |
|------|------|------|
| `matches` | `list[MatchItem]` | 匹配结果列表（按分数降序） |
| `confidence_level` | `str` | 置信度: high/medium/low |
| `reasoning` | `str` | 匹配推理说明 |

**MatchItem 结构**:

```json
{
  "gem_id": "gem_001",
  "gem_name": "美妆快闪模板",
  "score": 0.92,
  "breakdown": {
    "category": 1.0,
    "target_gender": 0.8,
    "price_tier": 0.5,
    "marketing_scene": 1.0
  }
}
```

---

### 9.7 T06: generate_prompt -- 提示词生成

| 属性 | 说明 |
|------|------|
| **描述** | 调用指定 Gem 生成视频提示词 |
| **幂等性** | 否（每次调用可能生成不同提示词） |
| **超时** | 60 秒 |

**输入参数**:

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|:----:|------|
| `image_path` | `str` | ✅ | 产品图片路径 |
| `gem_id` | `str` | ✅ | Gem 模板 ID |
| `features` | `dict` | — | 图片特征（可选，不提供则自动分析） |
| `user_description` | `str` | — | 用户补充描述 |

**返回值**:

| 字段 | 类型 | 说明 |
|------|------|------|
| `prompt` | `str` | 生成的视频提示词 |
| `gem_used` | `str` | 使用的 Gem ID |
| `is_valid` | `bool` | 是否通过校验 |

---

### 9.8 T07: generate_video_raw -- 原始视频生成

| 属性 | 说明 |
|------|------|
| **描述** | 直接调用视频生成 API（不包含分析和提示词生成） |
| **幂等性** | 否 |
| **超时** | 600 秒（10 分钟） |

**输入参数**:

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|:----:|--------|------|
| `prompt` | `str` | ✅ | — | 视频生成提示词 |
| `reference_image` | `str` | — | `null` | 参考图片路径 |
| `provider` | `str` | — | `"seedance"` | 视频提供商 |
| `resolution` | `str` | — | `"1080p"` | 分辨率: 480p/720p/1080p/4k |
| `duration` | `int` | — | `5` | 时长（秒），4-16 |
| `generate_audio` | `bool` | — | `true` | 是否生成音频 |
| `aspect_ratio` | `str` | — | `"9:16"` | 宽高比: 16:9/9:16/1:1/4:3/3:4/21:9 |

---

### 9.9 T08: list_gems -- 列出 Gem 模板

| 属性 | 说明 |
|------|------|
| **描述** | 列出所有可用的 Gem 模板 |
| **幂等性** | 是 |
| **超时** | 10 秒 |

**输入参数**:

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|:----:|--------|------|
| `category` | `str` | — | `null` | 按分类筛选 |
| `active_only` | `bool` | — | `true` | 仅返回启用的 Gem |
| `refresh` | `bool` | — | `false` | 强制刷新缓存 |

---

### 9.10 T09: health_check -- 系统健康检查

| 属性 | 说明 |
|------|------|
| **描述** | 检查系统各组件健康状态 |
| **幂等性** | 是 |
| **超时** | 10 秒 |

**输入参数**: 无

**返回值**:

| 字段 | 类型 | 说明 |
|------|------|------|
| `status` | `str` | 整体状态: healthy/degraded/unhealthy |
| `version` | `str` | Skill 版本号 |
| `checks` | `dict` | 各组件检查结果 |
| `latency_ms` | `int` | 检查耗时（毫秒） |

---

### 9.11 T10: get_config -- 获取配置

| 属性 | 说明 |
|------|------|
| **描述** | 获取当前系统配置（敏感值已脱敏） |
| **幂等性** | 是 |
| **超时** | 5 秒 |

**输入参数**:

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|:----:|--------|------|
| `section` | `str` | — | `null` | 配置分组（如 gemini/video/router） |

### 9.12 工具组合模式

| 模式 | 场景 | 工具序列 |
|------|------|----------|
| **一键生成** | 用户只想拿到视频 | T01（一步到位） |
| **分析优先** | 用户想先了解产品 | T04 --> T05 --> T06 --> T07 |
| **批量生产** | 大量产品处理 | T02（自动编排全部）+ T03 轮询 |
| **调试验证** | 排查问题 | T09 --> T10 --> T04 --> T05 |
| **模板探索** | 查找合适模板 | T08 --> T05 --> T06 |
| **进度监控** | 等待批量完成 | T03（轮询） |
| **视频直出** | 已有提示词，快速生成 | T07 |
| **Gem 管理** | 查看/验证 Gem 模板 | T08 |

---

## 10. 故障排查

### 10.1 错误码速查表

| 错误码 | 名称 | 说明 | 解决方案 |
|:------:|------|------|----------|
| **C001** | INVALID_REQUEST | 请求格式错误 | 检查 JSON 格式 |
| **C002** | VALIDATION_ERROR | 参数验证失败 | 检查必填参数和格式 |
| **C003** | UNSUPPORTED_FORMAT | 不支持的格式 | 使用 jpg/png/webp |
| **C004** | UNSUPPORTED_MODEL | 不支持的模型 | 检查提供商/模型 ID |
| **C101** | AUTH_ERROR | 认证失败 | 检查 Cookie/API Key |
| **C102** | PERMISSION_DENIED | 权限不足 | 检查 Service Account 权限 |
| **C201** | QUOTA_EXCEEDED | 配额超限 | 等待配额重置或升级 |
| **C202** | RATE_LIMITED | 速率限制 | 等待后自动重试 |
| **S001** | SERVER_OVERLOADED | 服务器过载 | 自动重试，指数退避 |
| **S002** | SERVICE_UNAVAILABLE | 服务不可用 | 自动降级到其他提供商 |
| **S003** | CIRCUIT_BREAKER_OPEN | 熔断器打开 | 等待自动恢复 |
| **N001** | CONNECTION_TIMEOUT | 连接超时 | 检查网络，自动重试 |
| **N002** | READ_TIMEOUT | 读取超时 | 检查网络，自动重试 |
| **N003** | CONNECTION_RESET | 连接重置 | 自动重试 |
| **T001** | TASK_TIMEOUT | 任务超时 | 增加超时时间或简化请求 |
| **T002** | TASK_FAILED | 任务失败 | 查看具体错误原因 |
| **T004** | SAFETY_BLOCKED | 安全拦截 | 修改提示词，避免敏感内容 |
| **T005** | GENERATION_ERROR | 生成失败 | 自动重试或更换模型 |
| **T201** | ALL_PROVIDERS_FAILED | 全部提供商失败 | 检查所有 API Key |
| **R001** | RESOURCE_NOT_FOUND | 资源不存在 | 检查任务/文件 ID |
| **R002** | STORAGE_FULL | 存储已满 | 清理磁盘或 Drive 空间 |
| **F001** | CONFIG_INVALID | 配置无效 | 检查配置文件格式 |
| **F002** | CREDENTIAL_EXPIRED | 凭证过期 | 更新 Cookie/API Key |
| **F003** | ENV_VAR_MISSING | 环境变量缺失 | 设置必需的环境变量 |
| **E001** | INTERNAL_ERROR | 内部错误 | 联系支持团队 |

### 10.2 排查流程图

```
遇到问题
  |
  v
+-------------------------+
| 1. 查看错误码            |
|    - 错误码在错误消息中   |
|    - 参考 10.1 错误码表   |
+-------------------------+
  |
  v
+-------------------------+
| 2. 调用 health_check()   |
|    - 查看各组件状态       |
|    - 确认问题范围         |
+-------------------------+
  |
  +-- 组件状态异常 -->
  |   查看对应组件日志
  |   检查认证/配置
  |
  +-- 全部正常 -->
  |   查看详细日志
  |   检查输入参数
  |
  v
+-------------------------+
| 3. 查看日志              |
|    - ./logs/skill.log    |
|    - 搜索 trace_id       |
+-------------------------+
  |
  v
+-------------------------+
| 4. 常见问题排查          |
|    - 参考第 11 章 FAQ    |
+-------------------------+
  |
  v
+-------------------------+
| 5. 联系支持              |
|    - 提供 trace_id       |
|    - 提供日志片段         |
+-------------------------+
```

---

## 11. 常见问题解答

### Q1: 视频生成一直失败怎么办？

**A**: 按以下步骤排查：

1. 调用 `health_check()` 检查各组件状态
2. 检查对应提供商的 API Key 是否有效（是否过期/被吊销）
3. 查看错误码：如为 `C202`(速率限制)，等待后自动重试；如为 `T004`(安全拦截)，修改提示词
4. 尝试切换到其他视频提供商（使用 `preferred_provider` 参数）
5. 查看日志文件，搜索 `trace_id` 获取详细错误信息
6. 如所有提供商都失败（错误码 `T201`），联系支持团队

### Q2: 批量任务中断了怎么恢复？

**A**: 本系统支持**断点续跑**：

1. 重新调用 `generate_video_batch`，使用相同的 `image_dir`
2. 系统会自动检测之前的批次记录
3. 已完成的任务会被自动跳过，仅处理未完成的任务
4. 通过 `get_batch_progress(batch_id)` 查看恢复后的进度
5. 如需要全新批次（不跳过已完成的任务），使用不同的批次名称

### Q3: 如何更换默认视频提供商？

**A**: 有三种方式：

1. **单次调用**: 在调用 `generate_video_single` 或 `generate_video_batch` 时传入 `preferred_provider` 参数
2. **修改配置**: 在配置中调整对应提供商的 `weight` 值（值越大优先级越高）
3. **禁用提供商**: 在配置中将不需要的提供商 `enabled` 设为 `false`

### Q4: Gem 模板匹配不准怎么办？

**A**: 可尝试以下方法：

1. 提供更详细的 `user_description`，帮助系统更准确理解产品
2. 直接使用 `preferred_gem_id` 参数指定模板，跳过自动匹配
3. 调整配置中 `router.confidence_threshold` 阈值
4. 检查 Gem 模板的 `product_categories` 和 `platforms` 字段是否覆盖你的产品类型
5. 如需要新模板，参考 WF-003 流程添加新 Gem

### Q5: 如何添加自定义的 Gem 模板？

**A**: 本系统采用**零代码**方式添加 Gem：

1. 在 Google Sheets 的 `gem-metadata` 工作表中新增一行
2. 填写所有必填字段（`gem_id`, `gem_name`, `gem_type`, `product_categories` 等）
3. 系统每 60 秒自动同步 Sheets 数据
4. 或调用 `list_gems(refresh=true)` 立即刷新
5. 新 Gem 立即可用于路由匹配
6. 调用 `match_gem_template` 测试匹配效果

### Q6: 支持哪些图片格式？有什么限制？

**A**:

| 格式 | 支持 | 大小限制 |
|------|:----:|:--------:|
| JPG/JPEG | ✅ | < 20MB |
| PNG | ✅ | < 20MB |
| WebP | ✅ | < 20MB |
| GIF | ✅ | < 20MB |
| BMP | ✅ | < 20MB |

建议：产品图片分辨率不低于 512x512，主体清晰，背景干净。

### Q7: 批量生成的视频会保存在哪里？

**A**: 视频默认保存到 `./output/videos/` 目录。同时如果配置了 `upload_to_drive=true`，会自动上传到 Google Drive 的批次文件夹中。批量任务完成后会生成一份汇总报告，包含所有视频的本地路径和 Drive 链接。

### Q8: 如何提高视频生成速度？

**A**:

1. **增加并发数**: 批量任务时调高 `max_concurrency`（建议 3-5）
2. **选择快速模型**: 使用 `preferred_provider="pika"` 或选择模型的 fast 版本
3. **降低分辨率**: 使用 `output_resolution="720p"` 代替 1080p
4. **缩短时长**: 使用 `output_duration=4`（最短）
5. **跳过音频**: 设置 `generate_audio=false`
6. **使用缓存**: 相同的图片会命中缓存，跳过重复分析

### Q9: 为什么路由匹配的置信度是 LOW？

**A**: 置信度 LOW 表示匹配分数 < 0.70，可能原因：

1. 产品品类较冷门，Gem 模板覆盖不足
2. 图片质量较差，分析出的特征不完整
3. 可用的 Gem 模板数量太少
4. 权重配置可能需要调整

解决方案：添加更多 Gem 模板、提供更详细的 `user_description`、或直接指定 `preferred_gem_id`。

### Q10: 如何监控批量任务的实时进度？

**A**: 使用 `get_batch_progress` 工具轮询：

```python
import asyncio

async def monitor_batch(batch_id: str):
    while True:
        progress = await get_batch_progress(batch_id=batch_id)
        print(f"[{progress['progress_percent']:.1f}%] "
              f"完成:{progress['completed']} "
              f"失败:{progress['failed']} "
              f"待处理:{progress['pending']}")
        if progress["is_complete"]:
            print("批量任务完成！")
            break
        await asyncio.sleep(10)
```

进度信息每 30 秒自动同步到 Google Sheets，也可通过 Sheets 直接查看。

### Q11: 系统支持哪些视频风格？

**A**: 支持 10 种视频风格：

| 风格 | 说明 | 适用场景 |
|------|------|----------|
| `fast_paced` | 快节奏 | 促销、闪购 |
| `slow_living` | 慢生活 | 家居、生活方式 |
| `story_driven` | 故事驱动 | 品牌故事 |
| `product_showcase` | 产品展示 | 电商主图视频 |
| `unboxing` | 开箱 | 新品发布 |
| `tutorial` | 教程 | 使用教学 |
| `lifestyle` | 生活方式 | 场景展示 |
| `dramatic` | 戏剧性 | 高端产品 |
| `minimal` | 极简 | 设计感产品 |
| `ugc_style` | UGC 风格 | 真实感内容 |

### Q12: 如何修改路由匹配的权重？

**A**: 权重配置在 `RouteMatchWeights` 模型中定义，默认总和为 1.0：

```python
# 当前权重配置（可热更新）
weights = {
    "product_category": 0.30,
    "marketing_scene": 0.20,
    "target_audience": 0.15,
    "price_range": 0.10,
    "video_style": 0.10,
    "history_data": 0.10,
    "other": 0.05,
}
```

如需调整，修改配置文件中的 `routing.weights` 部分，或调用配置管理接口。注意所有权重之和必须约等于 1.0（容差 0.01）。

### Q13: 图片分析缓存多久有效？

**A**: 图片分析结果默认缓存 24 小时（86400 秒），基于图片感知哈希（pHash）。缓存命中时直接返回结果，跳过 VLM API 调用，可显著减少分析时间和 Token 消耗。如需强制重新分析，可在调用 `analyze_image` 时设置 `use_cache=false`。

### Q14: 如何为不同平台生成不同风格的视频？

**A**: 在 `analyze_image` 中传入 `platform_hint` 参数：

```python
# 为抖音生成
features = await analyze_image(image_path="product.jpg", platform_hint="douyin")

# 为小红书生成
features = await analyze_image(image_path="product.jpg", platform_hint="xiaohongshu")
```

不同平台会自动调整权重配置：
- **抖音**: 提升 `video_style` 权重（重视觉节奏）
- **小红书**: 提升 `visual_tone` + `selling_points` 权重（重颜值和种草）
- **淘宝**: 提升 `category` + `price_tier` 权重（重品类和价格）

---

## 12. 提示词工程指南

### 12.1 提示词模板占位符

Prompt 模板支持以下占位符，在生成提示词时会被自动替换：

| 占位符 | 替换内容 | 示例 |
|--------|----------|------|
| `{product}` | 产品类型 | "口红" |
| `{category}` | 产品品类 | "美妆" |
| `{scene}` | 场景描述 | "在樱花树下" |
| `{style}` | 风格描述 | "快节奏、光影流动" |
| `{audience}` | 目标人群 | "年轻女性" |
| `{brand_tier}` | 品牌调性 | "高端品牌" |
| `{platform}` | 目标平台 | "抖音" |

### 12.2 优质提示词示例

```
一支{product}在{scene}中旋转展示，{style}，
面向{audience}，{brand_tier}质感，
适合{platform}平台的带货视频风格。

# 渲染后:
# 一支口红在樱花树下旋转展示，快节奏、光影流动，
# 面向年轻女性，高端品牌质感，
# 适合抖音平台的带货视频风格。
```

### 12.3 提示词最佳实践

1. **主体明确**: 提示词中必须明确产品主体
2. **动作描述**: 描述产品的动作或变化（旋转、展示、使用等）
3. **场景具体**: 具体场景比抽象场景效果更好
4. **风格统一**: 保持视觉风格的一致性
5. **平台适配**: 针对不同平台调整节奏和风格
6. **避免敏感词**: 避免触发安全拦截的内容

---

## 13. 附录

### 13.1 枚举值完整列表

#### 产品品类 (ProductCategory) -- 25 种

```
skincare, makeup, fragrance, personal_care,
womens_clothing, mens_clothing, shoes, bags, accessories,
phone, computer, digital_accessory, home_appliance,
snack, beverage, fresh_food, health_food,
furniture, home_decor, daily_necessities,
maternity_baby, pet,
sports, outdoor,
jewelry, watch
```

#### 价格带 (PriceTier) -- 5 档

```
budget(0-50元), affordable(50-200元), mid_range(200-1000元),
premium(1000-5000元), luxury(5000+元)
```

#### 目标平台 (TargetPlatform) -- 8 种

```
douyin, xiaohongshu, taobao, kuaishou, wechat, tiktok, instagram, universal
```

#### 视频风格 (VideoStyle) -- 10 种

```
fast_paced, slow_living, story_driven, product_showcase,
unboxing, tutorial, lifestyle, dramatic, minimal, ugc_style
```

### 13.2 版本历史

| 版本 | 日期 | 变更内容 |
|------|------|----------|
| v1.0 | 2026-07 | 初始版本，完整功能 |

### 13.3 联系方式

- **项目**: 300 Agent 融合项目
- **文档**: 本文档 (SKILL.md)
- **问题反馈**: 提供 trace_id 和日志片段

---

> **文档结束** -- 本文件为 AI 核心参考文档，使用时请确保阅读对应章节。
