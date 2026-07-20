# Batch Video Skill — 批量视频生成服务

> 基于多智能体融合架构的带货视频批量生产系统，支持多提供商智能路由、图片分析和自动化工作流。

---

## 目录

- [项目简介](#项目简介)
- [核心功能](#核心功能)
- [系统架构](#系统架构)
- [安装指南](#安装指南)
- [快速开始](#快速开始)
- [配置指南](#配置指南)
  - [主配置文件](#主配置文件)
  - [MCP 配置](#mcp-配置)
  - [Gem 元数据模板](#gem-元数据模板)
- [使用方式](#使用方式)
  - [命令行模式](#命令行模式)
  - [MCP Server 模式](#mcp-server-模式)
  - [Python API 模式](#python-api-模式)
- [数据格式说明](#数据格式说明)
- [提供商支持](#提供商支持)
- [智能路由](#智能路由)
- [监控与告警](#监控与告警)
- [故障排查](#故障排查)
- [开发指南](#开发指南)
- [项目结构](#项目结构)
- [贡献指南](#贡献指南)
- [许可证](#许可证)

---

## 项目简介

**Batch Video Skill** 是一个面向电商营销场景的智能视频批量生成系统。它能够根据产品图片和元数据自动分析产品特征，智能选择最佳视频生成提供商，批量产出高质量的营销视频。

### 主要特性

| 特性 | 说明 |
|------|------|
| **多提供商支持** | 同时支持即梦、可灵、Vidu、Veo 3 等主流视频生成平台 |
| **智能路由** | 基于产品图片特征自动匹配最佳提供商，持续自学习优化 |
| **批量处理** | 支持大规模并发任务，具备断点续传能力 |
| **图片分析** | 通过 Gemini AI 分析产品图片，自动提取品类、风格、受众等特征 |
| **Prompt 自动生成** | 根据产品特征自动撰写视频生成提示词 |
| **Google Workspace 集成** | 无缝读取 Google Sheets 数据，上传结果到 Google Drive |
| **MCP 协议支持** | 可作为 MCP Server 被任何 MCP 客户端调用 |
| **完整监控** | 实时成本追踪、性能指标、健康检查 |

### 适用场景

- 电商平台商品营销视频批量制作
- 社交媒体广告投放素材快速生成
- 品牌宣传视频自动化生产
- 产品展示、促销活动、品牌故事等视频内容创作

---

## 核心功能

### 1. 智能路由决策

系统通过分析产品图片特征（品类、风格、场景等），结合各提供商的能力画像，自动选择最适合的视频生成提供商：

```
产品图片 → Gemini 分析 → 特征标签 → 路由匹配 → 最佳提供商
```

### 2. 多提供商管理

统一管理多个视频生成提供商，支持：
- 多 API Key 轮询负载均衡
- 独立限流控制（RPM/RPD/并发）
- 动态权重调整（热更新）
- 失败自动回退

### 3. 批量任务处理

- 从 Google Sheets 或本地 CSV 读取任务队列
- 并发执行，支持断点续传
- 实时进度显示
- 自动重试与错误处理

### 4. 完整数据流

```
Google Sheets (Gem 元数据)
    ↓
图片分析 (Gemini) → 特征提取
    ↓
智能路由 → 选择提供商
    ↓
Prompt 生成 → 视频生成
    ↓
视频下载 → Google Drive 上传
    ↓
邮件通知
```

---

## 系统架构

```
┌─────────────────────────────────────────────────────────────┐
│                      用户交互层                               │
│  ┌──────────┐  ┌──────────┐  ┌──────────────────────────┐  │
│  │ CLI 工具  │  │ MCP 客户端 │  │ Python API / 脚本       │  │
│  └────┬─────┘  └────┬─────┘  └───────────┬──────────────┘  │
└───────┼─────────────┼────────────────────┼─────────────────┘
        │             │                    │
        └─────────────┴────────────────────┘
                          │
┌─────────────────────────┼─────────────────────────────────┐
│                         ▼                                  │
│  ┌─────────────────────────────────────────────────────┐  │
│  │              MCP Server / 服务入口                     │  │
│  └──────────────────────┬──────────────────────────────┘  │
│                         │                                   │
│  ┌──────────────────────┼──────────────────────────────┐  │
│  │                  核心服务层                            │  │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────────────┐  │  │
│  │  │ 批量处理器 │  │ 任务调度器 │  │ 智能路由引擎      │  │  │
│  │  └──────────┘  └──────────┘  └──────────────────┘  │  │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────────────┐  │  │
│  │  │ 图片分析器 │  │Prompt生成 │  │ 监控与日志       │  │  │
│  │  └──────────┘  └──────────┘  └──────────────────┘  │  │
│  └─────────────────────────────────────────────────────┘  │
│                         │                                   │
│  ┌──────────────────────┼──────────────────────────────┐  │
│  │                  适配器层                              │  │
│  │  ┌────────┐  ┌────────┐  ┌────────┐  ┌──────────┐ │  │
│  │  │ 即梦    │  │ 可灵    │  │ Vidu   │  │ Veo 3    │ │  │
│  │  └────────┘  └────────┘  └────────┘  └──────────┘ │  │
│  └─────────────────────────────────────────────────────┘  │
│                         │                                   │
│  ┌──────────────────────┼──────────────────────────────┐  │
│  │                  基础设施层                            │  │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────────────┐  │  │
│  │  │ 认证管理器│  │ 配置管理器│  │ Google Workspace │  │  │
│  │  └──────────┘  └──────────┘  └──────────────────┘  │  │
│  │  ┌──────────┐  ┌──────────┐  ┌──────────────────┐  │  │
│  │  │ 密钥池   │  │ 错误处理  │  │ 自学习引擎        │  │  │
│  │  └──────────┘  └──────────┘  └──────────────────┘  │  │
│  └─────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

---

## 安装指南

### 环境要求

| 依赖项 | 最低版本 | 说明 |
|--------|---------|------|
| Python | 3.11+ | 必需 |
| pip | 23.0+ | 必需 |
| uv（推荐）| 0.4.0+ | 可选，但推荐使用 |
| gws CLI | 最新版 | 可选，用于 Google Sheets 读取 |

### 安装步骤

#### 1. 克隆仓库

```bash
git clone https://github.com/your-org/batch-video-skill.git
cd batch-video-skill
```

#### 2. 创建虚拟环境

```bash
# 使用 uv（推荐）
uv venv .venv
source .venv/bin/activate  # Linux/macOS
# 或 .venv\Scripts\activate  # Windows

# 或使用 venv
python -m venv .venv
source .venv/bin/activate
```

#### 3. 安装依赖

```bash
# 使用 uv
uv pip install -e ".[all]"

# 或使用 pip
pip install -e ".[all]"
```

#### 4. 初始化配置目录

```bash
# 创建配置目录
mkdir -p ~/.batch-video-skill

# 复制配置文件模板
cp config.example.json ~/.batch-video-skill/config.json
cp mcp-config.example.json ~/.batch-video-skill/mcp-config.json

# 设置配置文件权限（仅所有者可读写）
chmod 600 ~/.batch-video-skill/config.json
chmod 600 ~/.batch-video-skill/mcp-config.json
```

#### 5. 配置认证信息

编辑 `~/.batch-video-skill/config.json`，填写各平台的 API Key 和认证信息：

```json
{
  "auth": {
    "jimeng": {
      "credentials": {
        "api_keys": ["your-jimeng-api-key"]
      }
    },
    "kling": {
      "credentials": {
        "api_keys": ["your-kling-api-key"]
      }
    }
  }
}
```

> **安全提示**: API Key 等敏感信息建议通过系统密钥环存储，不在配置文件中明文保存。详见[安全配置](#安全配置)章节。

#### 6. 验证安装

```bash
# 验证配置
python -m batch_video_skill validate-config

# 验证提供商连接
python -m batch_video_skill check-providers

# 运行测试
pytest tests/ -v
```

---

## 快速开始

### 场景一：生成单个视频

```python
import asyncio
from batch_video_skill import VideoGenerator

async def main():
    # 初始化视频生成器
    generator = VideoGenerator(config_path="~/.batch-video-skill/config.json")
    
    # 生成视频（使用智能路由自动选择提供商）
    result = await generator.generate(
        image_path="./input/product_001.jpg",
        prompt="A stylish product showcase video with smooth camera movement",
        resolution="720p",
        aspect_ratio="9:16",
        duration=5
    )
    
    print(f"视频已生成: {result.video_path}")
    print(f"使用提供商: {result.provider}")
    print(f"耗时: {result.duration_sec}秒")

asyncio.run(main())
```

### 场景二：批量生成视频

```bash
# 准备 Gem 元数据 CSV 文件（参考 gem-metadata-template.csv）
# 准备任务队列 CSV 文件（参考 task-queue-template.csv）

# 执行批量生成
python -m batch_video_skill batch \
  --gem-metadata ./input/gem_metadata.csv \
  --task-queue ./input/task_queue.csv \
  --max-concurrency 3 \
  --output-dir ./output/videos
```

### 场景三：作为 MCP Server 使用

```bash
# 启动 MCP Server（stdio 模式）
python -m batch_video_skill.mcp_server \
  --config ~/.batch-video-skill/config.json \
  --mcp-config ~/.batch-video-skill/mcp-config.json

# 在 Claude Desktop 等 MCP 客户端中配置使用
```

### 场景四：分析产品图片

```python
from batch_video_skill import ImageAnalyzer

analyzer = ImageAnalyzer()
analysis = analyzer.analyze("./input/product_001.jpg")

print(f"品类: {analysis.category}")
print(f"风格: {analysis.style}")
print(f"受众: {analysis.target_audience}")
print(f"推荐场景: {analysis.marketing_scene}")
```

---

## 配置指南

### 主配置文件

主配置文件 `config.json` 包含所有可配置项，详见 `config.example.json`。

#### 配置优先级

配置按以下优先级加载（高优先级覆盖低优先级）：

1. 命令行参数
2. 环境变量（以 `BATCH_VIDEO_` 为前缀）
3. 用户配置文件（`~/.batch-video-skill/config.json`）
4. 默认配置

#### 关键配置项

```
config.json
├── auth                          # 认证配置
│   ├── gemini                    # Gemini 认证
│   ├── google_workspace          # Google Workspace 认证
│   ├── jimeng                    # 即梦认证
│   ├── kling                     # 可灵认证
│   ├── vidu                      # Vidu 认证
│   └── veo3                      # Veo 3 认证
├── video_generation              # 视频生成配置
│   ├── default_*                 # 默认参数
│   ├── providers[]               # 提供商列表
│   ├── smart_routing_enabled     # 智能路由开关
│   ├── fallback_enabled          # 失败回退开关
│   └── download_*                # 下载配置
├── sheets                        # Google Sheets 配置
├── drive                         # Google Drive 配置
├── gmail                         # 邮件通知配置
├── routing                       # 智能路由详细配置
├── batch                         # 批量任务配置
├── monitoring                    # 监控配置
├── security                      # 安全配置
└── log_*                         # 日志配置
```

### MCP 配置

MCP 配置文件 `mcp-config.json` 定义 MCP Server 的行为，详见 `mcp-config.example.json`。

#### 传输方式选择

| 传输方式 | 适用场景 | 配置 |
|----------|---------|------|
| `stdio` | 本地进程通信（Claude Desktop） | 默认配置 |
| `sse` | HTTP 流式通信（远程部署） | 配置 host/port/cors |
| `http` | REST API（Web 应用集成） | 配置 host/port/auth |

#### 可用工具列表

| 工具名 | 功能 | 超时 |
|--------|------|------|
| `generate_video` | 单视频生成 | 300s |
| `batch_generate` | 批量视频生成 | 3600s |
| `analyze_image` | 产品图片分析 | 120s |
| `get_providers` | 获取提供商列表 | 30s |
| `upload_to_drive` | 上传到 Google Drive | 300s |
| `get_task_status` | 获取任务状态 | 30s |
| `update_provider_weight` | 更新提供商权重 | 30s |
| `generate_prompt` | 生成视频提示词 | 120s |

### Gem 元数据模板

`gem-metadata-template.csv` 定义了产品元数据的格式，用于批量视频生成。

#### 必填字段

| 字段名 | 说明 | 示例 |
|--------|------|------|
| `gem_id` | 唯一标识符 | GEM001 |
| `product_name` | 产品名称 | Summer Breeze Floral Dress |
| `product_description` | 产品描述 | Lightweight floral print sundress... |
| `category` | 产品品类 | Fashion |
| `main_image_url` | 主图 URL | https://example.com/images/dress.jpg |

#### 可选字段

| 字段名 | 说明 | 示例 |
|--------|------|------|
| `subcategory` | 子品类 | Dresses |
| `additional_image_urls` | 附加图片（逗号分隔） | url1,url2,url3 |
| `target_audience` | 目标受众 | Women 18-35 |
| `price_range` | 价格区间 | 29.99 |
| `currency` | 货币代码 | USD |
| `brand` | 品牌名称 | BreezeFashion |
| `marketing_scene` | 营销场景 | promotion |
| `style_preference` | 风格偏好 | bright,vibrant |
| `language` | 视频语言 | en |
| `tags` | 标签（逗号分隔） | summer,floral,dress |
| `notes` | 备注 | Highlight flowy fabric |

#### 填写示例

```csv
gem_id,product_name,product_description,category,main_image_url
GEM001,"Summer Dress","Lightweight floral dress","Fashion","https://example.com/dress.jpg"
GEM002,"Wireless Earbuds","ANC earbuds 30h battery","Electronics","https://example.com/earbuds.jpg"
```

---

## 使用方式

### 命令行模式

#### 全局选项

```bash
python -m batch_video_skill [OPTIONS] COMMAND [ARGS]

Options:
  --config PATH          配置文件路径 [默认: ~/.batch-video-skill/config.json]
  --log-level TEXT       日志级别 [默认: INFO]
  --verbose, -v          详细输出
  --help                 显示帮助信息
```

#### 生成单个视频

```bash
python -m batch_video_skill generate \
  --image ./input/product.jpg \
  --prompt "Showcase video with smooth camera movement" \
  --provider kling_3_0 \
  --resolution 720p \
  --aspect-ratio 9:16 \
  --duration 5 \
  --output ./output/video.mp4
```

#### 批量生成

```bash
python -m batch_video_skill batch \
  --gem-metadata ./input/gem_metadata.csv \
  --task-queue ./input/task_queue.csv \
  --max-concurrency 3 \
  --output-dir ./output/videos \
  --resume
```

#### 分析图片

```bash
python -m batch_video_skill analyze \
  --image ./input/product.jpg \
  --type full
```

#### 管理提供商

```bash
# 列出所有提供商
python -m batch_video_skill providers list

# 更新提供商权重
python -m batch_video_skill providers set-weight kling_3_0 8

# 检查提供商健康状态
python -m batch_video_skill providers health-check
```

#### 查看任务状态

```bash
python -m batch_video_skill status \
  --task-id BATCH_20260115 \
  --format detailed
```

### MCP Server 模式

#### 启动 Server

```bash
# stdio 模式（用于本地 MCP 客户端）
python -m batch_video_skill.mcp_server \
  --config ~/.batch-video-skill/config.json \
  --mcp-config ~/.batch-video-skill/mcp-config.json

# SSE 模式
python -m batch_video_skill.mcp_server \
  --transport sse \
  --host 0.0.0.0 \
  --port 8765

# HTTP 模式
python -m batch_video_skill.mcp_server \
  --transport http \
  --host 0.0.0.0 \
  --port 8766 \
  --api-key your-api-key
```

#### Claude Desktop 配置

在 Claude Desktop 配置文件中添加：

```json
{
  "mcpServers": {
    "batch-video-skill": {
      "command": "python",
      "args": ["-m", "batch_video_skill.mcp_server"],
      "env": {
        "BATCH_VIDEO_CONFIG_PATH": "~/.batch-video-skill/config.json"
      }
    }
  }
}
```

### Python API 模式

#### 基础使用

```python
import asyncio
from batch_video_skill import VideoGenerator, BatchProcessor, ImageAnalyzer

async def main():
    # 初始化组件
    config = ConfigManager.load("~/.batch-video-skill/config.json")
    generator = VideoGenerator(config)
    
    # 单视频生成
    result = await generator.generate(
        image_path="./product.jpg",
        resolution="720p",
        aspect_ratio="9:16"
    )
    
    # 批量处理
    processor = BatchProcessor(config)
    results = await processor.process(
        gem_metadata="./gem_metadata.csv",
        task_queue="./task_queue.csv",
        max_concurrency=3
    )

asyncio.run(main())
```

#### 高级使用

```python
# 自定义路由决策
from batch_video_skill import SmartRouter

router = SmartRouter(config)
recommendations = await router.route(
    image_path="./product.jpg",
    top_n=3  # 返回前 3 个推荐
)

for rec in recommendations:
    print(f"{rec.provider}: 置信度 {rec.confidence:.2f}")

# 动态更新权重
await router.update_weight("kling_3_0", 8)

# 图片分析 + Prompt 生成
analyzer = ImageAnalyzer(config)
analysis = await analyzer.analyze("./product.jpg")

prompt = await analyzer.generate_prompt(
    image_path="./product.jpg",
    product_name="Summer Dress",
    marketing_scene="promotion"
)
```

---

## 数据格式说明

### Gem 元数据 CSV

用于描述产品信息，是批量生成的输入数据。

**文件**: `gem-metadata-template.csv`

| 列 | 必填 | 格式 | 说明 |
|----|------|------|------|
| gem_id | 是 | GEM + 数字 | 唯一标识符 |
| product_name | 是 | 字符串 | 产品名称 |
| product_description | 是 | 字符串 | 产品描述 |
| category | 是 | 字符串 | 产品品类 |
| subcategory | 否 | 字符串 | 子品类 |
| main_image_url | 是 | URL | 主图 URL |
| additional_image_urls | 否 | URL列表 | 附加图片（逗号分隔） |
| target_audience | 否 | 字符串 | 目标受众描述 |
| price_range | 否 | 数字 | 价格区间 |
| currency | 否 | ISO代码 | 货币代码 |
| brand | 否 | 字符串 | 品牌名称 |
| marketing_scene | 否 | 枚举 | 营销场景 |
| style_preference | 否 | 逗号分隔 | 风格偏好 |
| language | 否 | ISO代码 | 视频语言 |
| tags | 否 | 逗号分隔 | 标签 |
| notes | 否 | 字符串 | 备注 |

### 任务队列 CSV

用于定义批量生成任务。

**文件**: `task-queue-template.csv`

| 列 | 必填 | 说明 |
|----|------|------|
| task_id | 是 | 任务唯一标识 |
| gem_id | 是 | 关联的 Gem ID |
| status | 是 | 状态: pending/running/completed/failed |
| prompt | 否 | 视频提示词（空则自动生成） |
| provider | 否 | 指定提供商（空则智能路由） |
| preferred_resolution | 否 | 首选分辨率 |
| preferred_aspect_ratio | 否 | 首选宽高比 |
| duration | 否 | 视频时长(秒) |
| generate_audio | 否 | 是否生成音频 |
| output_filename | 否 | 输出文件名 |
| priority | 否 | 优先级: high/medium/low |

---

## 提供商支持

### 支持的提供商

| 提供商 | 标识 | 特点 | 适用场景 |
|--------|------|------|---------|
| **可灵 3.0** | `kling_3_0` | 高质量，产品细节出色 | 产品展示、品牌宣传 |
| **Vidu Q3 Turbo** | `vidu_q3_turbo` | 快速生成，成本低 | 社交媒体、快速迭代 |
| **Veo 3** | `veo3` | Google 出品，画面精美 | 高端广告、品牌故事 |
| **即梦 2.0** | `jimeng_2_0` | 字节跳动，国内优化 | 国内平台、中文内容 |

### 提供商配置参数

每个提供商可配置以下参数：

```json
{
  "provider": "kling_3_0",
  "enabled": true,
  "model": "kling-v3",
  "api_keys": ["key1", "key2"],
  "weight": 5,
  "priority": 2,
  "rate_limit_rpm": 10,
  "rate_limit_rpd": 1000,
  "concurrency_limit": 3,
  "cost_tier": "standard",
  "max_cost_per_video_usd": 1.0
}
```

### 成本层级

| 层级 | 说明 | 典型价格区间 |
|------|------|-------------|
| `premium` | 最高质量，价格最高 | $1.0 - $5.0/视频 |
| `standard` | 平衡质量与成本 | $0.3 - $1.0/视频 |
| `budget` | 低成本快速生成 | $0.1 - $0.5/视频 |

---

## 智能路由

### 工作原理

智能路由系统通过以下步骤选择最佳提供商：

1. **图片分析**: Gemini 分析产品图片，提取特征标签
2. **特征匹配**: 将产品特征与各提供商能力画像匹配
3. **加权评分**: 基于多维度权重计算匹配分数
4. **置信度判断**: 根据置信度阈值决定策略
5. **结果返回**: 返回 Top-N 推荐提供商

### 匹配维度与权重

| 维度 | 权重 | 说明 |
|------|------|------|
| 产品品类 | 0.30 | 服装、电子、食品等 |
| 营销场景 | 0.20 | 促销、品牌展示、教程等 |
| 目标受众 | 0.15 | 年龄、性别、地域等 |
| 价格区间 | 0.10 | 高端/中端/低端 |
| 视频风格 | 0.10 | 写实、动画、抽象等 |
| 历史数据 | 0.10 | 过往生成效果反馈 |
| 其他 | 0.05 | 补充因素 |

### 置信度策略

| 置信度 | 策略 |
|--------|------|
| > 0.85 (高) | 直接选择最佳匹配 |
| 0.70 - 0.85 (中) | 选择 Top-2，首选失败后回退 |
| < 0.70 (低) | 多提供商并行，选择最优结果 |

### 自学习

启用自学习后，系统会根据视频生成效果反馈自动调整权重：

```python
# 提交反馈
await router.submit_feedback(
    provider="kling_3_0",
    gem_id="GEM001",
    rating=4.5,  # 1-5 分
    metrics={"quality": 4, "speed": 5, "cost": 3}
)
```

---

## 监控与告警

### 性能指标

系统收集以下性能指标：

| 指标 | 说明 |
|------|------|
| `success_rate` | 视频生成成功率 |
| `avg_latency_ms` | 平均生成延迟 |
| `throughput_videos_per_min` | 每分钟生成视频数 |
| `cost_per_video_usd` | 单个视频平均成本 |
| `provider_health` | 提供商健康状态 |

### 成本追踪

```bash
# 查看成本报告
python -m batch_video_skill cost-report

# 输出示例:
# 总调用次数: 150
# 总成本: $45.50 USD
# 平均成本/视频: $0.30 USD
# 成本告警阈值: $50.00 USD (已使用 91%)
```

### 邮件通知

配置邮件通知以接收任务完成/失败提醒：

```json
{
  "gmail": {
    "enabled": true,
    "notification_email": "your-email@example.com",
    "notify_on_complete": true,
    "notify_on_error": true,
    "notify_on_batch_summary": true
  }
}
```

---

## 故障排查

### 常见问题

#### Q: 视频生成失败，提示 "Provider unavailable"

**解决方案**:
1. 检查提供商 API Key 是否正确
2. 运行 `python -m batch_video_skill check-providers` 验证连接
3. 检查是否超过限流限制（RPM/RPD）
4. 确认提供商服务状态是否正常

#### Q: 智能路由选择的提供商效果不好

**解决方案**:
1. 手动指定提供商：`--provider kling_3_0`
2. 调整路由权重配置
3. 提交反馈以优化自学习模型
4. 检查图片分析结果是否准确

#### Q: Google Sheets 读取失败

**解决方案**:
1. 确认服务账号 JSON 文件路径正确
2. 检查 Spreadsheet ID 是否正确
3. 确认服务账号有 Sheets API 访问权限
4. 尝试使用 gws CLI 替代直接 API 调用

#### Q: 下载的视频文件损坏

**解决方案**:
1. 增加下载超时时间：`download_timeout_sec: 600`
2. 检查网络连接稳定性
3. 启用自动重试机制
4. 检查磁盘空间是否充足

### 日志查看

```bash
# 查看日志
tail -f ~/.batch-video-skill/skill.log

# 查看详细日志（DEBUG 级别）
python -m batch_video_skill --log-level DEBUG generate --image ./product.jpg

# 查看指标
python -m batch_video_skill metrics
```

### 调试模式

```bash
# 启用调试模式
export BATCH_VIDEO_LOG_LEVEL=DEBUG

# 查看完整请求/响应
export BATCH_VIDEO_DEBUG_HTTP=true
```

---

## 开发指南

### 项目结构

```
batch-video-skill/
├── pyproject.toml              # 项目配置和依赖
├── README.md                   # 本文档
├── config.example.json         # 主配置模板
├── mcp-config.example.json     # MCP 配置模板
├── gem-metadata-template.csv   # Gem 元数据模板
├── task-queue-template.csv     # 任务队列模板
│
├── src/
│   └── batch_video_skill/
│       ├── __init__.py
│       ├── __main__.py         # CLI 入口
│       │
│       ├── auth/               # 认证管理模块
│       │   ├── __init__.py
│       │   ├── manager.py      # 认证管理器
│       │   └── keyring_store.py # 密钥环存储
│       │
│       ├── config/             # 配置管理模块
│       │   ├── __init__.py
│       │   ├── manager.py      # 配置管理器
│       │   └── schema.py       # 配置 Schema (Pydantic)
│       │
│       ├── routing/            # 智能路由模块
│       │   ├── __init__.py
│       │   ├── engine.py       # 路由引擎
│       │   ├── analyzer.py     # 图片分析器
│       │   └── self_learning.py # 自学习引擎
│       │
│       ├── providers/          # 视频提供商适配器
│       │   ├── __init__.py
│       │   ├── base.py         # 适配器基类
│       │   ├── jimeng.py       # 即梦适配器
│       │   ├── kling.py        # 可灵适配器
│       │   ├── vidu.py         # Vidu 适配器
│       │   └── veo3.py         # Veo 3 适配器
│       │
│       ├── batch/              # 批量处理模块
│       │   ├── __init__.py
│       │   ├── processor.py    # 批量处理器
│       │   ├── scheduler.py    # 任务调度器
│       │   └── checkpoint.py   # 断点续传
│       │
│       ├── workspace/          # Google Workspace 集成
│       │   ├── __init__.py
│       │   ├── sheets.py       # Sheets 管理器
│       │   ├── drive.py        # Drive 上传器
│       │   └── gmail.py        # 邮件通知
│       │
│       ├── mcp_server/         # MCP Server 实现
│       │   ├── __init__.py
│       │   ├── server.py       # MCP Server 主类
│       │   ├── tools.py        # 工具注册
│       │   └── resources.py    # 资源注册
│       │
│       ├── monitoring/         # 监控和日志
│       │   ├── __init__.py
│       │   ├── metrics.py      # 指标收集
│       │   └── logger.py       # 日志配置
│       │
│       ├── gemini/             # Gemini 客户端
│       │   ├── __init__.py
│       │   └── client.py       # Gemini API 客户端
│       │
│       └── utils/              # 工具函数
│           ├── __init__.py
│           ├── errors.py       # 错误定义
│           └── helpers.py      # 辅助函数
│
├── tests/                      # 单元测试
│   ├── test_config.py
│   ├── test_routing.py
│   ├── test_providers.py
│   └── test_batch.py
│
└── docs/                       # 详细文档
    ├── architecture.md
    ├── providers.md
    └── api-reference.md
```

### 开发环境设置

```bash
# 安装开发依赖
pip install -e ".[dev]"

# 运行代码检查
ruff check src/
ruff format src/
mypy src/

# 运行测试
pytest tests/ -v --cov=batch_video_skill --cov-report=html

# 构建文档
cd docs && mkdocs serve
```

### 添加新的提供商

1. 在 `src/batch_video_skill/providers/` 创建新的适配器文件
2. 继承 `BaseVideoProvider` 基类
3. 实现必要的方法：`generate()`, `check_health()`, `get_rate_limit()`
4. 在配置文件中注册提供商
5. 添加单元测试

### 添加新的 MCP 工具

1. 在 `src/batch_video_skill/mcp_server/tools.py` 中注册新工具
2. 定义参数 Schema
3. 实现工具处理函数
4. 在 `mcp-config.example.json` 中添加工具定义
5. 更新文档

---

## 项目结构（交付物）

本项目的最终交付物包括：

```
final-deliverable/
├── SKILL.md                    # Skill 主文件（功能描述、API 文档）
├── config.example.json         # 完整配置模板
├── mcp-config.example.json     # MCP 配置模板
├── gem-metadata-template.csv   # Gem 元数据模板
├── task-queue-template.csv     # 任务队列模板
├── README.md                   # 本文档
├── scripts/                    # 辅助脚本
│   ├── setup.sh                # 初始化脚本
│   └── validate-config.py      # 配置验证脚本
└── docs/                       # 详细设计文档
    ├── architecture.md         # 架构设计文档
    └── api-reference.md        # API 参考手册
```

---

## 贡献指南

我们欢迎社区贡献！请遵循以下规范：

1. **代码风格**: 遵循 PEP 8，使用 Black 格式化
2. **类型注解**: 所有函数必须添加类型注解
3. **文档**: 新功能必须包含文档和示例
4. **测试**: 代码覆盖率需达到 80% 以上
5. **提交信息**: 使用 [Conventional Commits](https://www.conventionalcommits.org/) 规范

---

## 许可证

本项目采用 MIT 许可证。详见 [LICENSE](LICENSE) 文件。

---

## 技术支持

- **问题反馈**: [GitHub Issues](https://github.com/your-org/batch-video-skill/issues)
- **文档**: [完整文档](https://batch-video-skill.readthedocs.io)
- **更新日志**: [CHANGELOG.md](CHANGELOG.md)

---

> **提示**: 本文档最后更新于 2026-01-15。如发现任何错误或需要改进的地方，欢迎提交 Issue 或 PR。
