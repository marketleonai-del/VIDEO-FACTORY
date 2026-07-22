# 带货视频批量生产 Skill — 部署架构设计

> **文档版本**: v1.0 (Stage-3 部署架构)
> **生成日期**: 2026-07
> **设计输入**: architecture-system.md + fusion-master-matrix.md
> **设计范围**: 完整部署架构、安装流程、Docker化、CI/CD

---

## 目录

1. [部署架构概述](#1-部署架构概述)
2. [部署模式总览](#2-部署模式总览)
3. [部署架构图](#3-部署架构图)
4. [环境要求](#4-环境要求)
5. [目录结构设计](#5-目录结构设计)
6. [安装流程设计](#6-安装流程设计)
7. [Docker 支持](#7-docker-支持)
8. [CI/CD 配置](#8-cicd-配置)
9. [MCP Server 部署模式](#9-mcp-server-部署模式)
10. [完整部署指南](#10-完整部署指南)
11. [运维手册](#11-运维手册)
12. [附录](#12-附录)

---

## 1. 部署架构概述

### 1.1 设计目标

| 目标 | 说明 | 实现方式 |
|------|------|----------|
| **一键安装** | 用户5分钟内完成安装 | pip install + 自动初始化脚本 |
| **零服务端依赖** | 无需自建服务器 | 纯客户端架构，直接调用第三方API |
| **多模式运行** | 支持CLI/MCP Server两种入口 | 统一核心，不同表现层 |
| **容器化就绪** | 支持Docker部署 | Dockerfile + docker-compose |
| **自动化运维** | 测试/发布全自动 | GitHub Actions工作流 |

### 1.2 部署原则

- **纯客户端优先**: 无服务端组件（除AGPL隔离的Gemini服务外），所有逻辑在本地运行
- **配置即代码**: 所有配置通过文件管理，支持版本控制
- **凭证分离**: 认证信息与代码严格分离，绝不硬编码
- **渐进式部署**: 从本地pip安装开始，按需升级到Docker部署

---

## 2. 部署模式总览

### 2.1 三种部署模式

```
+========================================================================+
|                        三种部署模式对比                                |
+========================================================================+
|                                                                         |
|  +-------------------+  +-------------------+  +---------------------+ |
|  |   模式A: pip安装   |  |  模式B: Docker    |  |  模式C: MCP Server  | |
|  |   (推荐开发/个人)  |  |  (推荐生产/团队)   |  |  (推荐AI Agent集成)  | |
|  +-------------------+  +-------------------+  +---------------------+ |
|                                                                         |
|  适用场景:               适用场景:              适用场景:                |
|  - 个人开发者            - 团队协作            - AI Agent集成           |
|  - 快速体验              - 环境一致性           - 自动化工作流           |
|  - 本地调试              - 生产部署             - 多工具编排             |
|                                                                         |
|  复杂度: ★☆☆☆☆         复杂度: ★★★☆☆        复杂度: ★★☆☆☆         |
|  安装时间: ~3分钟        安装时间: ~5分钟       安装时间: ~5分钟        |
|  维护成本: 低            维护成本: 中           维护成本: 低             |
|                                                                         |
+========================================================================+
```

### 2.2 模式对比矩阵

| 维度 | 模式A: pip安装 | 模式B: Docker | 模式C: MCP Server |
|------|:-------------:|:-------------:|:-----------------:|
| **安装方式** | `pip install` | `docker-compose up` | `pip install` + MCP配置 |
| **运行环境** | 用户本机 | Docker容器 | 用户本机 |
| **Python版本** | 3.11+ | 容器内固定 | 3.11+ |
| **持久化** | `~/.batch-video-skill/` | Docker Volume | `~/.batch-video-skill/` |
| **Gemini服务** | 子进程/独立容器 | 独立容器 | 子进程/独立容器 |
| **监控** | 日志文件 | Prometheus + Grafana | 日志文件 |
| **更新方式** | `pip install -U` | `docker pull` | `pip install -U` |
| **适用平台** | macOS/Linux/Windows | 支持Docker的所有平台 | macOS/Linux/Windows |
| **多实例** | 不支持 | 支持 | 不支持 |

---

## 3. 部署架构图

### 3.1 总体部署架构

```
+========================================================================+
|                        总体部署架构图 (v1.0)                           |
+========================================================================+
|                                                                         |
|  用户环境 (Host Machine)                                               |
|  +===================================================================+ |
|  |                                                                   | |
|  |  方式1: pip安装                                                    | |
|  |  +------------------+      +------------------+                  | |
|  |  |  Python 3.11+   |      |  MCP Client      |                  | |
|  |  |  (虚拟环境)      |      |  (Claude/Cursor) |                  | |
|  |  +--------+---------+      +--------+---------+                  | |
|  |           |                         |                            | |
|  |           v                         v                            | |
|  |  +------+---------------------------------------------+--------+ | |
|  |  |            batch_video_skill 主包                    |        | |
|  |  |  +----------------+  +----------------+  +---------+ |       | |
|  |  |  | CLI Interface  |  | MCP Server     |  | Core    | |       | |
|  |  |  | (Typer/Click)  |  | (FastMCP)      |  | Engine  | |       | |
|  |  |  +-------+--------+  +-------+--------+  +----+----+ |       | |
|  |  |          |                  |                |       |       | |
|  |  |          +------------------+                |       |       | |
|  |  |                         |                     |       |       | |
|  |  |                         v                     v       |       | |
|  |  |  +------------------------------------------------+  |       | |
|  |  |  |            业务引擎 (异步协程)                    |  |       | |
|  |  |  |  ImageAnalyzer -> SmartRouter -> PromptBuilder   |  |       | |
|  |  |  |  -> UnifiedVideoGenerator -> WorkspaceClient     |  |       | |
|  |  |  +----------------------+---------------------------+  |       | |
|  |  |                         |                              |       | |
|  |  +-------------------------|------------------------------+       | |
|  |                            |                                      | |
|  |  +-------------------------v------------------------------+       | |
|  |  |  Gemini Service (AGPL-3.0 隔离容器)                     |       | |
|  |  |  - 独立进程运行                                         |       | |
|  |  |  - HTTP接口通信                                         |       | |
|  |  |  - Unix Socket/localhost:8001                          |       | |
|  |  +-------------------------------------------------------+       | |
|  |                                                                   | |
|  +===================================================================+ |
|                            |                                            |
|                            v                                            |
|  +===================================================================+ |
|  |                        外部服务 (云端)                              | |
|  |  +---------------+  +---------------+  +------------------------+ | |
|  |  | Gemini WebAPI |  | 视频生成API   |  | Google Workspace       | | |
|  |  | (Cookie认证)  |  | Kling/Veo/    |  | Sheets/Drive/Gmail     | | |
|  |  |               |  | Seedance/Vidu |  | (Service Account)      | | |
|  |  +---------------+  +---------------+  +------------------------+ | |
|  +===================================================================+ |
|                                                                         |
|  数据持久化 (本地文件系统)                                               |
|  +===================================================================+ |
|  |  ~/.batch-video-skill/                                            | |
|  |  ├── config.json          主配置                                   | |
|  |  ├── credentials/         Service Account JSON                     | |
|  |  ├── cache/               缓存数据                                 | |
|  |  ├── output/              生成物输出                               | |
|  |  │   ├── videos/          MP4视频文件                              | |
|  |  │   └── reports/         JSON/CSV报告                             | |
|  |  └── logs/                轮转日志                                 | |
|  +===================================================================+ |
|                                                                         |
+========================================================================+
```

### 3.2 组件部署关系图

```
+========================================================================+
|                     组件部署关系图 (运行时视角)                         |
+========================================================================+
|                                                                         |
|  Host OS (macOS/Linux/Windows)                                         |
|  +---------------------------------------------------------------+    |
|  |                                                               |    |
|  |  ~/.batch-video-skill/                Python 3.11+ venv       |    |
|  |  +----------------+                   +------------------+    |    |
|  |  | config.json    |<------------------| 主进程 (Main)    |    |    |
|  |  | 配置中心       |                   | - CLI/MCP入口    |    |    |
|  |  +----------------+                   | - 业务编排        |    |    |
|  |  | credentials/   |<------------------| - 状态管理        |    |    |
|  |  | 安全凭证       |                   +--------+---------+    |    |
|  |  +----------------+                            |                |    |
|  |  | cache/         |<--------------------+     | HTTP/gRPC      |    |
|  |  | 缓存数据       |                     |     |                |    |
|  |  +----------------+                     v     v                |    |
|  |  | output/        |<----------+  +-----+---------+             |    |
|  |  | 视频/报告      |            |  | Gemini服务    |             |    |
|  |  +----------------+            |  | (AGPL隔离)   |             |    |
|  |  | logs/          |<-------+   |  | :8001       |             |    |
|  |  | 轮转日志       |        |   |  +-------------+             |    |
|  |  +----------------+        |   |                             |    |
|  |                            |   |  第三方API (HTTPS)            |    |
|  |                            |   |  +-------------+  +--------+ |    |
|  |                            |   +->| Gemini Web  |  | Kling  | |    |
|  |                            |      | API         |  | Veo    | |    |
|  |                            |      +-------------+  | Seed.. | |    |
|  |                            |                       +--------+ |    |
|  |                            |      +-------------+  +--------+ |    |
|  |                            +----->| 本地视频    |  | Google | |    |
|  |                                   | 文件        |  | Sheets | |    |
|  |                                   +-------------+  +--------+ |    |
|  |                                                      +--------+ |    |
|  |                                                      | Drive  | |    |
|  |                                                      +--------+ |    |
|  +---------------------------------------------------------------+    |
|                                                                         |
+========================================================================+
```

### 3.3 数据流与部署映射

```
+========================================================================+
|                      数据流与部署位置映射                               |
+========================================================================+
|                                                                         |
|  处理阶段        部署位置              数据流向                          |
|  ─────────────────────────────────────────────────                     |
|                                                                         |
|  图片分析    →   主进程(本地)    →   本地图片 → Gemini服务 → 分析结果   |
|  智能路由    →   主进程(本地)    →   分析结果 → Sheets API → Gem匹配    |
|  提示词生成   →   主进程(本地)   →   图片+分析 → Gemini服务 → 提示词    |
|  视频生成    →   主进程(本地)    →   提示词 → 视频API → task_id        |
|  轮询查询    →   主进程(本地)    →   task_id → 视频API → video_url     |
|  视频下载    →   主进程(本地)    →   video_url → 本地文件系统          |
|  上传Drive   →   主进程(本地)    →   本地文件 → Drive API → file_id    |
|  记录任务    →   主进程(本地)    →   任务记录 → Sheets API → 表格      |
|  发送通知    →   主进程(本地)    →   邮件内容 → Gmail API → 用户邮箱   |
|                                                                         |
|  【关键结论】所有处理逻辑均在本地运行，云端仅作为存储和API提供方        |
|                                                                         |
+========================================================================+
```

---

## 4. 环境要求

### 4.1 系统环境

| 要求 | 版本/规格 | 说明 |
|------|:---------:|------|
| **操作系统** | macOS 12+ / Ubuntu 20.04+ / Windows 10+ | 主流桌面系统 |
| **Python** | 3.11 - 3.13 | 3.11+为必需（asyncio TaskGroup等特性） |
| **内存** | >= 4GB RAM | 批量处理时建议8GB+ |
| **磁盘** | >= 2GB 可用空间 | 含视频缓存和输出 |
| **网络** | 稳定互联网连接 | 需访问Google API和视频API |
| **Docker** | 24.0+ (可选) | Docker模式需要 |

### 4.2 Python 依赖清单

```txt
# === 核心运行时依赖 ===

# 异步HTTP客户端
aiohttp>=3.9.0            # 异步HTTP请求，所有API调用的基础
aiofiles>=23.0.0          # 异步文件操作

# 数据验证与配置
pydantic>=2.0.0           # 数据模型、配置验证
pydantic-settings>=2.0.0  # 基于Pydantic的配置管理

# Google生态
google-auth>=2.22.0       # Google认证库
google-auth-oauthlib>=1.0.0  # OAuth2流程
google-api-python-client>=2.95.0  # Sheets/Drive/Gmail API
google-auth-httplib2>=0.1.1

# 重试与容错
tenacity>=8.2.0           # 重试策略、指数退避

# 日志与监控
loguru>=0.7.0             # 结构化日志
prometheus-client>=0.17.0 # Prometheus指标（可选）

# 环境配置
python-dotenv>=1.0.0      # .env文件加载
pyyaml>=6.0.1             # YAML配置文件解析

# CLI界面
typer>=0.9.0              # CLI框架（基于Click）
rich>=13.0.0              # 终端富文本输出

# MCP协议
mcp>=1.12.0               # MCP SDK（FastMCP）

# 工具库
pillow>=10.0.0            # 图片处理（哈希计算、格式转换）
python-magic>=0.4.27      # 文件类型检测（可选）

# === 开发依赖（dev-dependencies）===
pytest>=7.4.0             # 测试框架
pytest-asyncio>=0.21.0    # 异步测试支持
pytest-cov>=4.1.0         # 覆盖率报告
pytest-mock>=3.11.0       # Mock支持
respx>=0.20.0             # HTTPX/aiohttp模拟
ruff>=0.1.0               # 代码格式化和Linting
mypy>=1.6.0               # 静态类型检查
pre-commit>=3.5.0         # Git钩子管理

# === 可选依赖（optional-dependencies）===
browser-cookie3>=0.19.0   # 从浏览器自动提取Cookie（P1认证恢复）
httpx>=0.25.0             # 备选HTTP客户端
```

### 4.3 pyproject.toml 完整配置

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "batch-video-skill"
version = "0.1.0"
description = "带货视频批量生产 Skill — AI驱动的视频自动化生成工具"
readme = "README.md"
license = { text = "MIT" }
requires-python = ">=3.11"
authors = [
    { name = "300 Agent Team" },
]
keywords = ["video-generation", "ai", "batch", "ecommerce", "mcp"]
classifiers = [
    "Development Status :: 3 - Alpha",
    "Intended Audience :: Developers",
    "License :: OSI Approved :: MIT License",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Programming Language :: Python :: 3.13",
    "Topic :: Multimedia :: Video",
]

dependencies = [
    "aiohttp>=3.9.0",
    "aiofiles>=23.0.0",
    "pydantic>=2.0.0",
    "pydantic-settings>=2.0.0",
    "google-auth>=2.22.0",
    "google-auth-oauthlib>=1.0.0",
    "google-api-python-client>=2.95.0",
    "google-auth-httplib2>=0.1.1",
    "tenacity>=8.2.0",
    "loguru>=0.7.0",
    "prometheus-client>=0.17.0",
    "python-dotenv>=1.0.0",
    "pyyaml>=6.0.1",
    "typer>=0.9.0",
    "rich>=13.0.0",
    "mcp>=1.12.0",
    "pillow>=10.0.0",
]

[project.optional-dependencies]
auth = ["browser-cookie3>=0.19.0"]       # 浏览器Cookie自动提取
dev = [
    "pytest>=7.4.0",
    "pytest-asyncio>=0.21.0",
    "pytest-cov>=4.1.0",
    "pytest-mock>=3.11.0",
    "respx>=0.20.0",
    "ruff>=0.1.0",
    "mypy>=1.6.0",
    "pre-commit>=3.5.0",
]
monitoring = ["prometheus-client>=0.17.0"]  # 监控指标
all = ["batch-video-skill[auth,dev,monitoring]"]

[project.scripts]
# CLI入口
batch-video = "batch_video_skill.cli:main"
bvs = "batch_video_skill.cli:main"
# MCP Server入口
batch-video-mcp = "batch_video_skill.mcp_server:main"

[project.urls]
Homepage = "https://github.com/300-agent-team/batch-video-skill"
Documentation = "https://github.com/300-agent-team/batch-video-skill/blob/main/README.md"
Repository = "https://github.com/300-agent-team/batch-video-skill"
Issues = "https://github.com/300-agent-team/batch-video-skill/issues"

[tool.hatch.build.targets.wheel]
packages = ["src/batch_video_skill"]

# === 代码质量工具配置 ===

[tool.ruff]
target-version = "py311"
line-length = 100
select = ["E", "F", "W", "I", "N", "UP", "B", "C4", "SIM"]

[tool.mypy]
python_version = "3.11"
warn_return_any = true
warn_unused_configs = true
disallow_untyped_defs = true
disallow_incomplete_defs = true

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
python_files = ["test_*.py", "*_test.py"]
python_classes = ["Test*"]
python_functions = ["test_*"]
addopts = "-v --tb=short --cov=batch_video_skill --cov-report=term-missing --cov-report=html"

[tool.coverage.run]
source = ["src/batch_video_skill"]
omit = ["*/tests/*", "*/test_*"]

[tool.coverage.report]
exclude_lines = [
    "pragma: no cover",
    "def __repr__",
    "raise AssertionError",
    "raise NotImplementedError",
    "if __name__ == .__main__.:",
]
```

### 4.4 环境变量清单

#### 4.4.1 必需环境变量

| 变量名 | 类型 | 说明 | 获取方式 | 示例 |
|--------|:----:|------|----------|------|
| `GEMINI_SECURE_1PSID` | string | Gemini主Cookie | 浏览器开发者工具提取 | `g.a000gAAxYZ...` |
| `GEMINI_SECURE_1PSIDTS` | string | Gemini辅助Cookie | 同上 | `bdchxyz123...` |
| `GWS_SERVICE_ACCOUNT_FILE` | path | Service Account JSON文件路径 | Google Cloud Console创建 | `/path/to/sa.json` |
| `KLING_API_KEY` | string | 可灵视频API Key | 可灵开放平台 | `sk_abc123...` |
| `VEO_API_KEY` | string | Veo视频API Key | Google AI Studio | `AIzaSyABC...` |
| `SEEDANCE_API_KEY` | string | 即梦视频API Key | 即梦开放平台 | `sk-abc123...` |

#### 4.4.2 可选环境变量

| 变量名 | 类型 | 默认值 | 说明 |
|--------|:----:|:------:|------|
| `BVS_HOME` | path | `~/.batch-video-skill` | 配置和数据主目录 |
| `BVS_CONFIG_FILE` | path | `$BVS_HOME/config.yaml` | 主配置文件路径 |
| `BVS_LOG_LEVEL` | string | `INFO` | 日志级别 (DEBUG/INFO/WARNING/ERROR) |
| `BVS_LOG_FILE` | path | `$BVS_HOME/logs/skill.log` | 日志文件路径 |
| `BVS_MAX_CONCURRENCY` | int | `3` | 最大并发数 (1-10) |
| `BVS_GEM_SHEET_ID` | string | (从config.yaml读取) | Gem元数据Sheet ID |
| `BVS_TASK_SHEET_ID` | string | (从config.yaml读取) | 任务队列Sheet ID |
| `BVS_OUTPUT_DIR` | path | `$BVS_HOME/output` | 输出目录 |
| `BVS_CACHE_DIR` | path | `$BVS_HOME/cache` | 缓存目录 |
| `BVS_GEMINI_SERVICE_URL` | url | `http://localhost:8001` | Gemini服务地址 |
| `BVS_REQUEST_TIMEOUT` | int | `300` | 请求超时(秒) |
| `BVS_RETRY_MAX_ATTEMPTS` | int | `3` | 最大重试次数 |
| `BVS_PROMETHEUS_PORT` | int | `9090` | Prometheus指标端口 |
| `VIDU_API_KEY` | string | - | Vidu API Key (P1) |
| `RUNWAY_API_KEY` | string | - | Runway API Key (P1) |
| `PIKA_API_KEY` | string | - | Pika API Key (P2) |

#### 4.4.3 环境变量加载优先级

```
+========================================================================+
|                    配置加载优先级 (从高到低)                            |
+========================================================================+
|                                                                         |
|  1. 环境变量 (export VAR=value)                                         |
|       ↓                                                                 |
|  2. .env 文件 (项目根目录或 $BVS_HOME/.env)                             |
|       ↓                                                                 |
|  3. config.yaml 文件 ($BVS_HOME/config.yaml)                            |
|       ↓                                                                 |
|  4. 内置默认值 (代码中定义)                                             |
|                                                                         |
|  规则: 高优先级覆盖低优先级，环境变量中的敏感信息优先                     |
|                                                                         |
+========================================================================+
```

---

## 5. 目录结构设计

### 5.1 用户数据目录

```
~/.batch-video-skill/                           # 主目录 ($BVS_HOME)
│
├── config.yaml                                  # 主配置文件
├── .env                                         # 本地环境变量（不提交git）
│
├── credentials/                                 # 认证信息目录 (权限: 700)
│   ├── service-account.json                     # Google Service Account
│   ├── gemini-cookies.json                      # Gemini Cookie备份
│   └── .gitignore                               # 忽略所有文件
│
├── cache/                                       # 缓存目录 (权限: 755)
│   ├── gem-metadata/                            # Gem元数据缓存
│   │   ├── gems.json                            # 最新Gem列表缓存
│   │   └── gems.json.etag                       # 缓存版本标记
│   │
│   ├── image-analysis/                          # 图片分析缓存
│   │   ├── aa/                                  # 按哈希前缀分目录
│   │   │   └── bbccdd...json                    # 图片哈希 → 分析结果
│   │   └── ee/
│   │
│   └── http/                                    # HTTP响应缓存
│       └── ...                                  # API响应缓存文件
│
├── output/                                      # 输出目录 (权限: 755)
│   ├── videos/                                  # 生成的视频
│   │   ├── 2026-07/                             # 按月分目录
│   │   │   ├── batch-20260715-143022/           # 批次目录
│   │   │   │   ├── img1-productA.mp4            # 视频文件
│   │   │   │   ├── img2-productB.mp4
│   │   │   │   └── ...
│   │   │   └── single-20260715-150000/
│   │   └── 2026-08/
│   │
│   └── reports/                                 # 生成的报告
│       ├── 2026-07/
│       │   ├── batch-20260715-143022-report.json   # 批次报告
│       │   └── batch-20260715-143022-report.csv    # CSV摘要
│       └── 2026-08/
│
├── logs/                                        # 日志目录 (权限: 755)
│   ├── skill.log                                # 当前日志
│   ├── skill.log.2026-07-14                     # 历史日志 (按日轮转)
│   ├── skill.log.2026-07-13
│   └── error.log                                # 错误日志 (单独记录)
│
└── temp/                                        # 临时文件 (权限: 755)
    ├── downloads/                               # 视频下载临时目录
    └── uploads/                                 # 上传临时目录
```

### 5.2 配置文件模板 (config.yaml)

```yaml
# ============================================================
# 带货视频批量生产 Skill — 主配置文件
# ============================================================
# 配置文件加载优先级:
#   1. 环境变量 (export BVS_XXX=xxx)
#   2. .env 文件
#   3. 本文件 (config.yaml)
#   4. 内置默认值
# ============================================================

# --- Gemini配置 ---
gemini:
  # Gemini服务地址 (AGPL隔离服务)
  service_url: "http://localhost:8001"
  # Cookie文件路径 (默认在credentials目录)
  cookie_file: "${BVS_HOME}/credentials/gemini-cookies.json"
  # Cookie健康检查间隔(秒)
  health_check_interval: 600
  # 认证恢复策略: none | auto_refresh | browser_cookie3 | multi_backup
  recovery_strategy: "browser_cookie3"

# --- Google Workspace配置 ---
workspace:
  # Service Account JSON文件路径
  service_account_file: "${BVS_HOME}/credentials/service-account.json"
  # 管理员邮箱 (域授权需要)
  admin_email: ""
  # Gem元数据Sheet ID
  gem_sheet_id: ""
  # 任务队列Sheet ID
  task_sheet_id: ""
  # 默认Drive文件夹ID (视频上传目标)
  drive_folder_id: ""
  # Sheets API调用间隔(秒) — 防止触发限流
  api_call_interval: 1.0

# --- 视频生成配置 ---
video:
  # 默认视频提供商优先级列表 (按优先级排序)
  provider_priority:
    - "seedance_2_0"
    - "kling_3_0"
    - "veo_3_1"
  # 默认视频参数
  default_params:
    duration: 5           # 视频时长(秒): 5, 8, 10, 12
    resolution: "1080p"   # 分辨率: 720p, 1080p, 4k
    aspect_ratio: "9:16"  # 宽高比: 9:16, 16:9, 1:1, 3:4
  # 各提供商API端点 (可选，默认使用内置端点)
  providers:
    kling:
      api_base: "https://api.klingai.com"
    veo:
      api_base: "https://api.generativeai.google"
    seedance:
      api_base: "https://api.seedance.io"

# --- 智能路由配置 ---
router:
  # 匹配权重 (总计100%)
  match_weights:
    product_category: 30   # 产品品类
    marketing_scene: 20    # 营销场景
    target_audience: 15    # 目标人群
    price_tier: 10         # 价格带
    video_style: 10        # 视频风格
    historical_effect: 10  # 历史效果
    other: 5               # 其他
  # 置信度阈值
  confidence_thresholds:
    high: 0.85    # >0.85 直接使用Top1
    medium: 0.70  # >0.70 Top3供选择
    low: 0.50     # <0.70 人工确认
  # TopN推荐数量
  top_n: 3

# --- 任务配置 ---
task:
  # 最大并发数
  max_concurrency: 3
  # 单任务超时(秒)
  task_timeout: 600
  # 批量任务全局超时(秒)
  batch_timeout: 3600
  # 重试次数
  max_retries: 3
  # 失败任务处理: continue | stop | retry_later
  on_failure: "continue"
  # 自动上传Drive
  auto_upload_drive: true
  # 自动发送通知邮件
  auto_notify: false

# --- 日志配置 ---
logging:
  level: "INFO"                    # DEBUG/INFO/WARNING/ERROR
  format: "{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} | {message}"
  # 日志轮转
  rotation: "1 day"                # 按天轮转
  retention: "30 days"             # 保留30天
  compression: "zip"               # 压缩历史日志
  # 控制台输出
  console_output: true
  # 结构化JSON日志 (用于日志分析)
  json_format: false

# --- 监控配置 (P1) ---
monitoring:
  # 是否启用Prometheus指标
  enabled: false
  # 指标监听端口
  port: 9090
  # 指标前缀
  metrics_prefix: "bvs_"

# --- 通知配置 (P1) ---
notification:
  # 通知方式: none | gmail | webhook
  method: "none"
  # 通知邮箱
  email: ""
  # Webhook URL
  webhook_url: ""
```

### 5.3 权限设计

| 目录/文件 | 权限 | 说明 |
|-----------|:----:|------|
| `~/.batch-video-skill/` | `700` (drwx------) | 仅所有者可访问 |
| `credentials/` | `700` (drwx------) | 敏感凭证目录 |
| `credentials/*` | `600` (-rw-------) | 凭证文件仅所有者可读写 |
| `config.yaml` | `600` (-rw-------) | 配置文件含敏感信息 |
| `.env` | `600` (-rw-------) | 环境变量文件 |
| `cache/` | `755` (drwxr-xr-x) | 缓存目录 |
| `output/` | `755` (drwxr-xr-x) | 输出目录 |
| `logs/` | `755` (drwxr-xr-x) | 日志目录 |
| `temp/` | `755` (drwxr-xr-x) | 临时目录 |

---

## 6. 安装流程设计

### 6.1 安装流程图

```
+========================================================================+
|                       安装流程图 (5步完成)                              |
+========================================================================+
|                                                                         |
|  Step 1        Step 2         Step 3          Step 4        Step 5     |
|  环境检查  →   pip安装    →   配置初始化   →   认证设置   →   验证安装  |
|                                                                         |
|  +------+    +------+     +---------+     +---------+    +--------+  |
|  |检查   |    |pip   |     |生成     |     |放置     |    |运行   |  |
|  |Python | →  |install|  →  |config   |  →  |凭证文件 | →  |验证   |  |
|  |3.11+  |    |包     |     |.yaml    |     |        |    |脚本   |  |
|  +------+    +------+     +---------+     +---------+    +--------+  |
|     |            |              |              |              |       |
|     v            v              v              v              v       |
|  python3     pip install    bvs init      手动放置/     bvs doctor   |
|  --version   batch-video    (交互式)       自动生成                  |
|              -skill                                      (健康检查)   |
|                                                                         |
|  耗时: ~10s    耗时: ~60s   耗时: ~30s     耗时: ~5min    耗时: ~20s   |
|                                                                         |
+========================================================================+
```

### 6.2 Step 1: 环境检查脚本

```python
#!/usr/bin/env python3
"""
环境检查脚本 — 验证系统是否满足安装要求
用法: python3 check_env.py
"""

import sys
import subprocess
from pathlib import Path


def check_python_version() -> bool:
    """检查Python版本 >= 3.11"""
    version = sys.version_info
    if version.major == 3 and version.minor >= 11:
        print(f"✅ Python版本: {version.major}.{version.minor}.{version.micro}")
        return True
    print(f"❌ Python版本: {version.major}.{version.minor}.{version.micro} (需要 >= 3.11)")
    return False


def check_pip() -> bool:
    """检查pip是否可用"""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "--version"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            print(f"✅ pip: {result.stdout.strip()}")
            return True
    except Exception as e:
        print(f"❌ pip检查失败: {e}")
    return False


def check_network() -> bool:
    """检查网络连接"""
    import urllib.request
    hosts = [
        ("Google", "https://www.google.com"),
        ("PyPI", "https://pypi.org"),
    ]
    all_ok = True
    for name, url in hosts:
        try:
            urllib.request.urlopen(url, timeout=5)
            print(f"✅ 网络连接 ({name}): 正常")
        except Exception:
            print(f"⚠️ 网络连接 ({name}): 不可用")
            all_ok = False
    return all_ok


def check_disk_space() -> bool:
    """检查磁盘空间"""
    import shutil
    home = Path.home()
    stat = shutil.disk_usage(home)
    free_gb = stat.free / (1024**3)
    if free_gb >= 2:
        print(f"✅ 磁盘空间: {free_gb:.1f}GB 可用 (需要 >= 2GB)")
        return True
    print(f"❌ 磁盘空间: {free_gb:.1f}GB 可用 (需要 >= 2GB)")
    return False


def main() -> int:
    """主函数: 运行所有检查"""
    print("=" * 60)
    print("  带货视频批量生产 Skill — 环境检查")
    print("=" * 60)

    checks = [
        ("Python版本", check_python_version),
        ("pip", check_pip),
        ("网络连接", check_network),
        ("磁盘空间", check_disk_space),
    ]

    results = []
    for name, check_fn in checks:
        print(f"\n--- 检查: {name} ---")
        try:
            results.append(check_fn())
        except Exception as e:
            print(f"❌ 检查异常: {e}")
            results.append(False)

    print("\n" + "=" * 60)
    passed = sum(results)
    total = len(results)
    print(f"检查结果: {passed}/{total} 项通过")

    if passed == total:
        print("🎉 环境检查全部通过，可以开始安装!")
        print(f"\n下一步: pip install batch-video-skill")
        return 0
    else:
        print("⚠️ 环境检查未完全通过，请修复上述问题后重试")
        return 1


if __name__ == "__main__":
    sys.exit(main())
```

### 6.3 Step 2: pip 安装

```bash
# === 标准安装 ===
pip install batch-video-skill

# === 开发模式安装 (推荐开发者) ===
git clone https://github.com/300-agent-team/batch-video-skill.git
cd batch-video-skill
pip install -e ".[all]"

# === 带浏览器Cookie支持 ===
pip install batch-video-skill[auth]

# === 仅核心功能 (最小安装) ===
pip install batch-video-skill

# === 升级 ===
pip install -U batch-video-skill
```

### 6.4 Step 3: 配置初始化 (交互式)

```python
#!/usr/bin/env python3
"""
配置初始化脚本 — 交互式引导用户完成初始配置
用法: bvs init
"""

import os
import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.prompt import Prompt, Confirm
from rich.panel import Panel

console = Console()
app = typer.Typer()

# 默认配置模板
DEFAULT_CONFIG = """\
# 带货视频批量生产 Skill 配置文件
# 生成时间: {timestamp}
#
# 配置加载优先级:
#   1. 环境变量 (export BVS_XXX=xxx)
#   2. .env 文件
#   3. 本文件
#   4. 内置默认值

gemini:
  service_url: "http://localhost:8001"
  cookie_file: "{home}/credentials/gemini-cookies.json"
  health_check_interval: 600
  recovery_strategy: "browser_cookie3"

workspace:
  service_account_file: "{home}/credentials/service-account.json"
  admin_email: "{admin_email}"
  gem_sheet_id: "{gem_sheet_id}"
  task_sheet_id: "{task_sheet_id}"
  drive_folder_id: "{drive_folder_id}"

video:
  provider_priority:
    - "seedance_2_0"
    - "kling_3_0"
    - "veo_3_1"
  default_params:
    duration: 5
    resolution: "1080p"
    aspect_ratio: "9:16"

router:
  match_weights:
    product_category: 30
    marketing_scene: 20
    target_audience: 15
    price_tier: 10
    video_style: 10
    historical_effect: 10
    other: 5
  confidence_thresholds:
    high: 0.85
    medium: 0.70
    low: 0.50
  top_n: 3

task:
  max_concurrency: 3
  task_timeout: 600
  batch_timeout: 3600
  max_retries: 3
  on_failure: "continue"
  auto_upload_drive: true
  auto_notify: false

logging:
  level: "INFO"
  rotation: "1 day"
  retention: "30 days"
  console_output: true
"""


@app.command()
def init(
    home_dir: Optional[Path] = typer.Option(
        None, "--home", "-h", help="配置主目录 (默认: ~/.batch-video-skill)"
    ),
    non_interactive: bool = typer.Option(
        False, "--yes", "-y", help="非交互模式，使用默认值"
    ),
):
    """初始化配置文件和目录结构"""
    console.print(Panel.fit(
        "🎬 带货视频批量生产 Skill — 配置初始化\n"
        "本向导将帮助您完成初始配置。",
        title="欢迎", border_style="blue"
    ))

    # 确定主目录
    if home_dir is None:
        home_dir = Path.home() / ".batch-video-skill"
    else:
        home_dir = home_dir.expanduser().resolve()

    console.print(f"📁 配置主目录: {home_dir}")

    # 创建目录结构
    dirs_to_create = [
        home_dir,
        home_dir / "credentials",
        home_dir / "cache" / "gem-metadata",
        home_dir / "cache" / "image-analysis",
        home_dir / "cache" / "http",
        home_dir / "output" / "videos",
        home_dir / "output" / "reports",
        home_dir / "logs",
        home_dir / "temp" / "downloads",
        home_dir / "temp" / "uploads",
    ]

    for d in dirs_to_create:
        d.mkdir(parents=True, exist_ok=True)
        console.print(f"  ✅ {d.relative_to(home_dir.parent)}")

    # 设置目录权限
    os.chmod(home_dir / "credentials", 0o700)

    # 交互式收集配置
    if non_interactive:
        config_data = {
            "timestamp": "auto",
            "home": str(home_dir),
            "admin_email": "",
            "gem_sheet_id": "",
            "task_sheet_id": "",
            "drive_folder_id": "",
        }
    else:
        console.print("\n[bold]请提供以下配置信息:[/] (直接回车跳过)")
        config_data = {
            "timestamp": "auto",
            "home": str(home_dir),
            "admin_email": Prompt.ask("Google Workspace管理员邮箱", default=""),
            "gem_sheet_id": Prompt.ask("Gem元数据Sheet ID", default=""),
            "task_sheet_id": Prompt.ask("任务队列Sheet ID", default=""),
            "drive_folder_id": Prompt.ask("Drive上传文件夹ID", default=""),
        }

    # 生成配置文件
    config_path = home_dir / "config.yaml"
    config_content = DEFAULT_CONFIG.format(**config_data)
    config_path.write_text(config_content, encoding="utf-8")
    config_path.chmod(0o600)
    console.print(f"\n✅ 配置文件已生成: {config_path}")

    # 生成.env模板
    env_template = home_dir / ".env.example"
    env_content = """\
# 环境变量配置文件
# 复制为 .env 并填写实际值

# === Gemini认证 (必需) ===
GEMINI_SECURE_1PSID=your_cookie_here
GEMINI_SECURE_1PSIDTS=your_cookie_ts_here

# === Google Service Account (必需) ===
GWS_SERVICE_ACCOUNT_FILE={home}/credentials/service-account.json

# === 视频API Keys (至少填一个) ===
KLING_API_KEY=your_kling_key
VEO_API_KEY=your_veo_key
SEEDANCE_API_KEY=your_seedance_key

# === 可选配置 ===
BVS_LOG_LEVEL=INFO
BVS_MAX_CONCURRENCY=3
""".format(home=str(home_dir))
    env_template.write_text(env_content, encoding="utf-8")
    console.print(f"✅ .env模板已生成: {env_template}")

    # 生成 credentials/.gitignore
    gitignore = home_dir / "credentials" / ".gitignore"
    gitignore.write_text("*\n!.gitignore\n", encoding="utf-8")

    console.print(Panel.fit(
        "🎉 初始化完成!\n\n"
        f"下一步:\n"
        f"1. 将Service Account JSON放到: {home_dir}/credentials/service-account.json\n"
        f"2. 复制 {home_dir}/.env.example 为 {home_dir}/.env 并填写认证信息\n"
        f"3. 运行 `bvs doctor` 验证安装",
        title="完成", border_style="green"
    ))


if __name__ == "__main__":
    app()
```

### 6.5 Step 4: 认证设置

#### 6.5.1 Google Service Account 设置

```bash
# 方式1: 手动放置
# 从 Google Cloud Console 下载 Service Account JSON
# 复制到 ~/.batch-video-skill/credentials/service-account.json

# 方式2: 使用命令行工具
bvs auth setup-gws --json-file /path/to/downloaded.json

# 验证Service Account
bvs auth verify-gws
```

#### 6.5.2 Gemini Cookie 设置

```bash
# 方式1: 手动设置环境变量
export GEMINI_SECURE_1PSID="your_cookie_value"
export GEMINI_SECURE_1PSIDTS="your_cookie_ts_value"

# 方式2: 写入 .env 文件
bvs auth setup-gemini --interactive

# 方式3: 从浏览器自动提取 (需安装browser-cookie3)
bvs auth extract-cookies --browser chrome

# 验证Cookie
bvs auth verify-gemini
```

#### 6.5.3 视频API Key 设置

```bash
# 一次性设置所有Key
bvs auth setup-video \
  --kling "sk_xxx" \
  --veo "AIzaSy_xxx" \
  --seedance "sk-xxx"

# 或写入 .env 文件
# ~/.batch-video-skill/.env
KLING_API_KEY=sk_xxx
VEO_API_KEY=AIzaSy_xxx
SEEDANCE_API_KEY=sk-xxx
```

### 6.6 Step 5: 验证安装

```python
#!/usr/bin/env python3
"""
安装验证脚本 — 全面检查安装状态和配置正确性
用法: bvs doctor
"""

import sys
import os
import asyncio
from pathlib import Path
from dataclasses import dataclass, field
from typing import List

import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn

console = Console()


@dataclass
class CheckResult:
    """单项检查结果"""
    name: str
    status: str  # pass / fail / warning / skip
    message: str = ""
    fix_hint: str = ""


class InstallationDoctor:
    """安装诊断器"""

    def __init__(self):
        self.home_dir = Path(os.environ.get("BVS_HOME", Path.home() / ".batch-video-skill"))
        self.results: List[CheckResult] = []

    async def run_all_checks(self) -> List[CheckResult]:
        """运行所有检查"""
        checks = [
            self.check_home_directory,
            self.check_config_file,
            self.check_credentials,
            self.check_service_account,
            self.check_gemini_cookies,
            self.check_video_api_keys,
            self.check_workspace_sheets,
            self.check_network_connectivity,
            self.check_gemini_service,
        ]

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            for check_fn in checks:
                task = progress.add_task(description=check_fn.__name__, total=None)
                try:
                    result = await check_fn()
                    self.results.append(result)
                except Exception as e:
                    self.results.append(CheckResult(
                        name=check_fn.__name__,
                        status="fail",
                        message=f"检查异常: {e}",
                    ))
                progress.update(task, completed=True)

        return self.results

    async def check_home_directory(self) -> CheckResult:
        """检查主目录是否存在"""
        if self.home_dir.exists():
            return CheckResult(
                name="主目录",
                status="pass",
                message=f"{self.home_dir}",
            )
        return CheckResult(
            name="主目录",
            status="fail",
            message=f"目录不存在: {self.home_dir}",
            fix_hint="运行 `bvs init` 初始化目录结构",
        )

    async def check_config_file(self) -> CheckResult:
        """检查配置文件"""
        config_path = self.home_dir / "config.yaml"
        if not config_path.exists():
            return CheckResult(
                name="配置文件",
                status="fail",
                message=f"config.yaml 不存在",
                fix_hint="运行 `bvs init` 生成配置文件",
            )
        # 尝试解析YAML
        try:
            import yaml
            with open(config_path) as f:
                config = yaml.safe_load(f)
            return CheckResult(
                name="配置文件",
                status="pass",
                message=f"{config_path} (有效YAML)",
            )
        except Exception as e:
            return CheckResult(
                name="配置文件",
                status="fail",
                message=f"YAML解析失败: {e}",
                fix_hint="检查config.yaml格式",
            )

    async def check_credentials(self) -> CheckResult:
        """检查凭证目录"""
        cred_dir = self.home_dir / "credentials"
        if not cred_dir.exists():
            return CheckResult(
                name="凭证目录",
                status="fail",
                message="credentials/ 不存在",
                fix_hint="运行 `bvs init`",
            )
        # 检查权限
        mode = cred_dir.stat().st_mode
        if mode & 0o077:
            return CheckResult(
                name="凭证目录",
                status="warning",
                message=f"权限过于开放: {oct(mode & 0o777)}",
                fix_hint=f"chmod 700 {cred_dir}",
            )
        return CheckResult(
            name="凭证目录",
            status="pass",
            message="存在且权限正确",
        )

    async def check_service_account(self) -> CheckResult:
        """检查Service Account"""
        sa_file = self.home_dir / "credentials" / "service-account.json"
        if not sa_file.exists():
            return CheckResult(
                name="Service Account",
                status="fail",
                message="service-account.json 不存在",
                fix_hint="从Google Cloud Console下载并放置到credentials目录",
            )
        try:
            import json
            with open(sa_file) as f:
                sa = json.load(f)
            # 验证必要字段
            required = ["type", "project_id", "private_key", "client_email"]
            missing = [k for k in required if k not in sa]
            if missing:
                return CheckResult(
                    name="Service Account",
                    status="fail",
                    message=f"缺少字段: {missing}",
                    fix_hint="下载完整的Service Account JSON",
                )
            return CheckResult(
                name="Service Account",
                status="pass",
                message=f"项目: {sa.get('project_id', 'unknown')}",
            )
        except Exception as e:
            return CheckResult(
                name="Service Account",
                status="fail",
                message=f"JSON解析失败: {e}",
            )

    async def check_gemini_cookies(self) -> CheckResult:
        """检查Gemini Cookie"""
        psid = os.environ.get("GEMINI_SECURE_1PSID")
        psidts = os.environ.get("GEMINI_SECURE_1PSIDTS")

        if psid and psidts:
            return CheckResult(
                name="Gemini Cookie",
                status="pass",
                message="环境变量已设置",
            )

        # 检查cookie文件
        cookie_file = self.home_dir / "credentials" / "gemini-cookies.json"
        if cookie_file.exists():
            return CheckResult(
                name="Gemini Cookie",
                status="pass",
                message=f"Cookie文件: {cookie_file}",
            )

        return CheckResult(
            name="Gemini Cookie",
            status="fail",
            message="Cookie未设置",
            fix_hint="运行 `bvs auth setup-gemini` 或设置环境变量",
        )

    async def check_video_api_keys(self) -> CheckResult:
        """检查视频API Keys"""
        keys = {
            "Kling": os.environ.get("KLING_API_KEY"),
            "Veo": os.environ.get("VEO_API_KEY"),
            "Seedance": os.environ.get("SEEDANCE_API_KEY"),
        }
        available = [name for name, key in keys.items() if key]
        if available:
            return CheckResult(
                name="视频API Keys",
                status="pass",
                message=f"已配置: {', '.join(available)} ({len(available)}/{len(keys)})",
            )
        return CheckResult(
            name="视频API Keys",
            status="fail",
            message="未配置任何视频API Key",
            fix_hint="设置 KLING_API_KEY / VEO_API_KEY / SEEDANCE_API_KEY 环境变量",
        )

    async def check_workspace_sheets(self) -> CheckResult:
        """检查Sheets配置"""
        import yaml
        config_path = self.home_dir / "config.yaml"
        if not config_path.exists():
            return CheckResult(name="Sheets配置", status="skip", message="无配置文件")

        try:
            with open(config_path) as f:
                config = yaml.safe_load(f)
            ws = config.get("workspace", {})
            gem_sheet = ws.get("gem_sheet_id", "")
            task_sheet = ws.get("task_sheet_id", "")

            if gem_sheet and task_sheet:
                return CheckResult(
                    name="Sheets配置",
                    status="pass",
                    message=f"Gem: {gem_sheet[:8]}..., Task: {task_sheet[:8]}...",
                )
            return CheckResult(
                name="Sheets配置",
                status="warning",
                message="Sheet ID未配置，部分功能不可用",
                fix_hint="运行 `bvs init` 重新配置或编辑config.yaml",
            )
        except Exception as e:
            return CheckResult(name="Sheets配置", status="skip", message=str(e))

    async def check_network_connectivity(self) -> CheckResult:
        """检查网络连通性"""
        import aiohttp
        endpoints = [
            ("Google API", "https://sheets.googleapis.com/v4/"),
            ("Kling API", "https://api.klingai.com"),
        ]
        reachable = []
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            for name, url in endpoints:
                try:
                    async with session.get(url) as resp:
                        reachable.append(name)
                except Exception:
                    pass

        if reachable:
            return CheckResult(
                name="网络连通性",
                status="pass",
                message=f"可达: {', '.join(reachable)}",
            )
        return CheckResult(
            name="网络连通性",
            status="warning",
            message="无法连接到外部API",
            fix_hint="检查网络连接和代理设置",
        )

    async def check_gemini_service(self) -> CheckResult:
        """检查Gemini服务"""
        import aiohttp
        service_url = os.environ.get("BVS_GEMINI_SERVICE_URL", "http://localhost:8001")
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
                async with session.get(f"{service_url}/health") as resp:
                    if resp.status == 200:
                        return CheckResult(
                            name="Gemini服务",
                            status="pass",
                            message=f"{service_url} 运行中",
                        )
        except Exception as e:
            return CheckResult(
                name="Gemini服务",
                status="warning",
                message=f"{service_url} 未响应: {e}",
                fix_hint="运行 `bvs gemini-service start` 启动服务",
            )
        return CheckResult(name="Gemini服务", status="warning", message="未知状态")

    def print_report(self):
        """打印检查报告"""
        table = Table(title="安装诊断报告")
        table.add_column("检查项", style="cyan")
        table.add_column("状态", style="bold")
        table.add_column("详情", style="white")
        table.add_column("修复建议", style="yellow")

        status_style = {
            "pass": "[green]✅ 通过[/]",
            "fail": "[red]❌ 失败[/]",
            "warning": "[yellow]⚠️ 警告[/]",
            "skip": "[dim]⏭️ 跳过[/]",
        }

        for r in self.results:
            table.add_row(
                r.name,
                status_style.get(r.status, r.status),
                r.message,
                r.fix_hint,
            )

        console.print()
        console.print(table)

        # 统计
        passed = sum(1 for r in self.results if r.status == "pass")
        failed = sum(1 for r in self.results if r.status == "fail")
        warnings = sum(1 for r in self.results if r.status == "warning")

        console.print()
        if failed == 0:
            console.print(Panel(
                f"🎉 检查结果: {passed}通过 {warnings}警告 {failed}失败\n"
                "系统可以正常运行!",
                border_style="green"
            ))
        else:
            console.print(Panel(
                f"⚠️ 检查结果: {passed}通过 {warnings}警告 [bold red]{failed}失败[/]\n"
                "请根据修复建议处理失败项后重新运行 `bvs doctor`",
                border_style="red"
            ))


async def main():
    doctor = InstallationDoctor()
    await doctor.run_all_checks()
    doctor.print_report()


if __name__ == "__main__":
    asyncio.run(main())
```

### 6.7 安装脚本汇总

```bash
#!/bin/bash
# ============================================================
# 一键安装脚本 — install.sh
# 用法: curl -fsSL https://.../install.sh | bash
# 或: bash install.sh [--dev] [--home /custom/path]
# ============================================================

set -euo pipefail

# 配置
REPO_URL="https://github.com/300-agent-team/batch-video-skill"
PYPI_PACKAGE="batch-video-skill"
DEFAULT_HOME="${HOME}/.batch-video-skill"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_ok() { echo -e "${GREEN}[OK]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# 解析参数
DEV_MODE=false
CUSTOM_HOME=""
while [[ $# -gt 0 ]]; do
    case $1 in
        --dev) DEV_MODE=true; shift ;;
        --home) CUSTOM_HOME="$2"; shift 2 ;;
        *) log_error "未知参数: $1"; exit 1 ;;
    esac
done

# 打印欢迎信息
cat << 'EOF'
============================================================
  🎬 带货视频批量生产 Skill — 一键安装脚本
============================================================
EOF

# Step 1: 环境检查
log_info "Step 1/5: 检查环境..."
python3 -c "import sys; v=sys.version_info; exit(0 if v>=(3,11) else 1)" || {
    log_error "需要 Python 3.11+"
    exit 1
}
log_ok "Python版本检查通过"

# Step 2: pip安装
log_info "Step 2/5: 安装Python包..."
if $DEV_MODE; then
    log_info "开发模式: 从源码安装"
    pip install -e ".[all]"
else
    pip install "${PYPI_PACKAGE}"
fi
log_ok "Python包安装完成"

# Step 3: 初始化配置
log_info "Step 3/5: 初始化配置..."
if [[ -n "${CUSTOM_HOME}" ]]; then
    bvs init --home "${CUSTOM_HOME}" --yes
else
    bvs init --yes
fi
log_ok "配置初始化完成"

# Step 4: 提示认证设置
log_info "Step 4/5: 等待认证设置..."
cat << EOF

${YELLOW}============================================================
  请完成以下认证设置:
============================================================${NC}

1. Google Service Account:
   从 https://console.cloud.google.com/ 下载JSON
   复制到: ${CUSTOM_HOME:-${DEFAULT_HOME}}/credentials/service-account.json

2. Gemini Cookie:
   export GEMINI_SECURE_1PSID="your_cookie"
   export GEMINI_SECURE_1PSIDTS="your_cookie_ts"

3. 视频API Keys:
   export KLING_API_KEY="your_key"
   export VEO_API_KEY="your_key"
   export SEEDANCE_API_KEY="your_key"

${YELLOW}============================================================${NC}

EOF

# 询问是否已准备好认证信息
read -p "是否已完成认证设置? (y/N): " ready
if [[ "${ready}" =~ ^[Yy]$ ]]; then
    # Step 5: 验证
    log_info "Step 5/5: 验证安装..."
    bvs doctor
    log_ok "安装完成!"
else
    log_warn "认证设置未完成，稍后请运行 'bvs doctor' 验证"
fi

cat << EOF

${GREEN}============================================================
  🎉 安装完成!
============================================================

快速开始:
  bvs --help              # 查看所有命令
  bvs doctor              # 检查安装状态
  bvs process image.png   # 处理单张图片
  bvs batch ./images/     # 批量处理目录

文档: ${REPO_URL}/blob/main/README.md
问题: ${REPO_URL}/issues

============================================================${NC}
EOF
```

---

## 7. Docker 支持

### 7.1 Dockerfile

```dockerfile
# ============================================================
# 主应用 Dockerfile — 多阶段构建
# ============================================================

# ---- 阶段1: 构建依赖 ----
FROM python:3.12-slim AS builder

WORKDIR /build

# 安装构建依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# 复制项目文件
COPY pyproject.toml README.md ./
COPY src/ ./src/

# 构建wheel包
RUN pip install --no-cache-dir build && \
    python -m build --wheel

# ---- 阶段2: 运行时 ----
FROM python:3.12-slim AS runtime

LABEL maintainer="300 Agent Team"
LABEL description="带货视频批量生产 Skill"
LABEL version="0.1.0"

WORKDIR /app

# 创建非root用户
RUN groupadd -r skill && useradd -r -g skill -s /bin/bash skill

# 安装运行时系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 从构建阶段复制wheel包
COPY --from=builder /build/dist/*.whl /tmp/

# 安装Python包
RUN pip install --no-cache-dir /tmp/*.whl && \
    rm -rf /tmp/*.whl

# 创建数据目录
ENV BVS_HOME=/app/data
RUN mkdir -p /app/data/{credentials,cache,output,logs,temp} && \
    chown -R skill:skill /app/data

# 切换用户
USER skill

# 健康检查
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import batch_video_skill; print('OK')" || exit 1

# 暴露端口 (MCP Server / Metrics)
EXPOSE 8000 9090

# 入口点
ENTRYPOINT ["batch-video"]
CMD ["--help"]
```

### 7.2 Gemini 服务 Dockerfile (AGPL隔离)

```dockerfile
# ============================================================
# Gemini 服务 Dockerfile — AGPL-3.0 隔离容器
# ============================================================
# 本容器独立运行gemini-webapi，通过HTTP与主容器通信
# 本容器代码以AGPL-3.0授权，与主项目许可证分离

FROM python:3.12-slim

LABEL maintainer="300 Agent Team"
LABEL description="Gemini WebAPI 服务 (AGPL-3.0)"
LABEL license="AGPL-3.0"

WORKDIR /app

# 安装依赖
RUN pip install --no-cache-dir \
    fastapi>=0.104.0 \
    uvicorn[standard]>=0.24.0 \
    gemini-webapi \
    pydantic>=2.0.0 \
    loguru>=0.7.0

# 复制服务代码
COPY gemini-service/main.py ./

# 非root用户
RUN useradd -m -s /bin/bash gemini && \
    mkdir -p /home/gemini/.local/share && \
    chown -R gemini:gemini /app /home/gemini
USER gemini

# 环境变量
ENV GEMINI_SERVICE_HOST=0.0.0.0
ENV GEMINI_SERVICE_PORT=8000

# 健康检查
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--log-level", "info"]
```

### 7.3 docker-compose.yml (完整版)

```yaml
# ============================================================
# Docker Compose — 完整部署配置
# ============================================================
# 用法:
#   docker-compose up -d              # 基础模式
#   docker-compose --profile monitoring up -d  # 含监控
#   docker-compose -f docker-compose.yml -f docker-compose.prod.yml up -d  # 生产模式

version: "3.8"

services:
  # === 主应用服务 ===
  video-skill:
    build:
      context: .
      dockerfile: Dockerfile
      target: runtime
    image: batch-video-skill:latest
    container_name: bvs-main
    restart: unless-stopped
    environment:
      # 核心配置
      - BVS_HOME=/app/data
      - BVS_LOG_LEVEL=${LOG_LEVEL:-INFO}
      - BVS_MAX_CONCURRENCY=${MAX_CONCURRENCY:-3}
      # Gemini连接 (连接到隔离服务)
      - BVS_GEMINI_SERVICE_URL=http://gemini-service:8000
      # Google认证 (从secrets挂载)
      - GWS_SERVICE_ACCOUNT_FILE=/secrets/service-account.json
      # 视频API Keys (从.env加载)
      - KLING_API_KEY=${KLING_API_KEY:-}
      - VEO_API_KEY=${VEO_API_KEY:-}
      - SEEDANCE_API_KEY=${SEEDANCE_API_KEY:-}
      - VIDU_API_KEY=${VIDU_API_KEY:-}
      - RUNWAY_API_KEY=${RUNWAY_API_KEY:-}
    volumes:
      # 凭证 (只读)
      - type: bind
        source: ./secrets
        target: /secrets
        read_only: true
      # 数据持久化
      - bvs-data:/app/data
      # 输出目录 (可选: 绑定到主机方便访问)
      - type: bind
        source: ./output
        target: /app/data/output
    ports:
      # MCP Server端口
      - "${MCP_PORT:-8000}:8000"
      # Prometheus指标端口 (可选)
      - "${METRICS_PORT:-9090}:9090"
    depends_on:
      gemini-service:
        condition: service_healthy
    networks:
      - bvs-network
    # 资源限制
    deploy:
      resources:
        limits:
          cpus: "2.0"
          memory: 1G
        reservations:
          cpus: "0.5"
          memory: 256M

  # === Gemini 服务 (AGPL-3.0 隔离) ===
  gemini-service:
    build:
      context: .
      dockerfile: gemini-service/Dockerfile
    image: bvs-gemini-service:latest
    container_name: bvs-gemini
    restart: unless-stopped
    environment:
      - GEMINI_SECURE_1PSID=${GEMINI_SECURE_1PSID}
      - GEMINI_SECURE_1PSIDTS=${GEMINI_SECURE_1PSIDTS}
    volumes:
      # Cookie持久化
      - gemini-cookies:/home/gemini/.local/share
    expose:
      - "8000"  # 仅内网暴露，不映射到主机
    networks:
      - bvs-network
    deploy:
      resources:
        limits:
          cpus: "1.0"
          memory: 512M

  # === Prometheus 监控 (可选) ===
  prometheus:
    image: prom/prometheus:v2.48.0
    container_name: bvs-prometheus
    restart: unless-stopped
    profiles:
      - monitoring
    volumes:
      - ./monitoring/prometheus.yml:/etc/prometheus/prometheus.yml:ro
      - prometheus-data:/prometheus
    ports:
      - "${PROMETHEUS_PORT:-9091}:9090"
    networks:
      - bvs-network
    command:
      - "--config.file=/etc/prometheus/prometheus.yml"
      - "--storage.tsdb.path=/prometheus"
      - "--storage.tsdb.retention.time=30d"

  # === Grafana 可视化 (可选) ===
  grafana:
    image: grafana/grafana:10.2.0
    container_name: bvs-grafana
    restart: unless-stopped
    profiles:
      - monitoring
    environment:
      - GF_SECURITY_ADMIN_USER=${GRAFANA_USER:-admin}
      - GF_SECURITY_ADMIN_PASSWORD=${GRAFANA_PASSWORD:-admin}
      - GF_USERS_ALLOW_SIGN_UP=false
    volumes:
      - grafana-data:/var/lib/grafana
      - ./monitoring/grafana/dashboards:/etc/grafana/provisioning/dashboards:ro
      - ./monitoring/grafana/datasources:/etc/grafana/provisioning/datasources:ro
    ports:
      - "${GRAFANA_PORT:-3000}:3000"
    networks:
      - bvs-network
    depends_on:
      - prometheus

networks:
  bvs-network:
    driver: bridge
    ipam:
      config:
        - subnet: 172.20.0.0/16

volumes:
  bvs-data:
    driver: local
  gemini-cookies:
    driver: local
  prometheus-data:
    driver: local
  grafana-data:
    driver: local
```

### 7.4 docker-compose.override.yml (开发模式)

```yaml
# ============================================================
# Docker Compose Override — 开发模式配置
# ============================================================
# 自动被 docker-compose 加载，覆盖生产默认值

version: "3.8"

services:
  video-skill:
    build:
      target: runtime
    image: batch-video-skill:dev
    environment:
      - BVS_LOG_LEVEL=DEBUG
    volumes:
      # 源码挂载 (开发热重载)
      - ./src:/app/src:ro
    # 覆盖命令: 保持运行便于调试
    command: ["tail", "-f", "/dev/null"]
    # 开发模式资源限制放宽
    deploy:
      resources:
        limits:
          cpus: "4.0"
          memory: 2G

  gemini-service:
    image: bvs-gemini-service:dev
    environment:
      - LOG_LEVEL=debug
```

### 7.5 .env 模板 (Docker)

```bash
# ============================================================
# Docker环境变量配置
# 复制为 .env 并填写实际值
# ============================================================

# --- 端口配置 ---
MCP_PORT=8000
METRICS_PORT=9090
PROMETHEUS_PORT=9091
GRAFANA_PORT=3000

# --- 日志级别 ---
LOG_LEVEL=INFO

# --- 并发配置 ---
MAX_CONCURRENCY=3

# --- Gemini Cookie (必需) ---
GEMINI_SECURE_1PSID=
GEMINI_SECURE_1PSIDTS=

# --- 视频API Keys (至少一个) ---
KLING_API_KEY=
VEO_API_KEY=
SEEDANCE_API_KEY=
VIDU_API_KEY=
RUNWAY_API_KEY=

# --- Grafana (可选) ---
GRAFANA_USER=admin
GRAFANA_PASSWORD=change_me_in_production
```

### 7.6 Docker 使用命令

```bash
# === 基础操作 ===

# 构建镜像
docker-compose build

# 启动服务 (后台)
docker-compose up -d

# 查看日志
docker-compose logs -f video-skill
docker-compose logs -f gemini-service

# 停止服务
docker-compose down

# 停止并删除数据卷
docker-compose down -v

# === 监控模式 ===

# 启动含Prometheus + Grafana
docker-compose --profile monitoring up -d

# 访问监控面板
# Prometheus: http://localhost:9091
# Grafana: http://localhost:3000 (admin/admin)

# === 容器内执行命令 ===

# 运行CLI命令
docker-compose exec video-skill batch-video --help

# 运行诊断
docker-compose exec video-skill batch-video doctor

# 进入容器调试
docker-compose exec video-skill bash

# === 更新部署 ===

# 拉取最新代码并重建
docker-compose pull
docker-compose up -d --build

# 仅重启特定服务
docker-compose restart video-skill
```

---

## 8. CI/CD 配置

### 8.1 GitHub Actions 工作流概览

```
+========================================================================+
|                    CI/CD 工作流架构                                     |
+========================================================================+
|                                                                         |
|  +----------------+    +----------------+    +---------------------+  |
|  | CI: 代码质量   |    | CI: 测试       |    | CD: 发布            |  |
|  | (PR触发)       |    | (PR/定时)      |    | (tag推送)           |  |
|  +----------------+    +----------------+    +---------------------+  |
|                                                                         |
|  Trigger:              Trigger:              Trigger:                  |
|  pull_request          push to main          push tag v*               |
|  paths: src/**         schedule: 每周一       branches: main           |
|                                                                         |
|  Jobs:                 Jobs:                 Jobs:                     |
|  ├ lint (ruff)        ├ unit-test           ├ build-package          |
|  ├ type-check (mypy)  ├ integration-test    ├ publish-pypi           |
|  ├ format-check       ├ coverage-report     ├ build-docker           |
|  └ security-scan      └ compatibility-test  └ publish-ghcr           |
|                                                                         |
+========================================================================+
```

### 8.2 工作流1: CI — 代码质量

```yaml
# .github/workflows/ci-quality.yml
# ============================================================
# CI: 代码质量检查
# 触发: Pull Request, push到main分支
# ============================================================

name: CI — Code Quality

on:
  pull_request:
    branches: [main, develop]
    paths:
      - "src/**"
      - "tests/**"
      - "pyproject.toml"
      - ".github/workflows/**"
  push:
    branches: [main]

jobs:
  lint:
    name: Lint (ruff)
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install ruff
        run: pip install ruff

      - name: Run ruff linter
        run: ruff check src/ tests/

      - name: Run ruff formatter check
        run: ruff format --check src/ tests/

  type-check:
    name: Type Check (mypy)
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install dependencies
        run: |
          pip install -e ".[all]"
          pip install mypy

      - name: Run mypy
        run: mypy src/batch_video_skill --ignore-missing-imports

  security-scan:
    name: Security Scan
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Run Bandit
        uses: PyCQA/bandit@main
        with:
          args: "-r src/ -f json -o bandit-report.json || true"

      - name: Check for secrets
        uses: trufflesecurity/trufflehog@main
        with:
          path: ./
          base: main
          head: HEAD
```

### 8.3 工作流2: CI — 测试

```yaml
# .github/workflows/ci-test.yml
# ============================================================
# CI: 自动化测试
# 触发: Pull Request, push到main分支, 每周一定时
# ============================================================

name: CI — Test

on:
  pull_request:
    branches: [main, develop]
    paths:
      - "src/**"
      - "tests/**"
      - "pyproject.toml"
  push:
    branches: [main]
  schedule:
    # 每周一上午8点运行
    - cron: "0 8 * * 1"

jobs:
  unit-test:
    name: Unit Tests
    runs-on: ${{ matrix.os }}
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest, macos-latest, windows-latest]
        python-version: ["3.11", "3.12", "3.13"]
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python ${{ matrix.python-version }}
        uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -e ".[dev]"

      - name: Run unit tests
        run: pytest tests/unit -v --tb=short

      - name: Generate coverage report
        run: pytest tests/unit --cov=batch_video_skill --cov-report=xml --cov-report=html

      - name: Upload coverage to Codecov
        uses: codecov/codecov-action@v3
        with:
          files: ./coverage.xml
          flags: unittests
          name: codecov-${{ matrix.os }}-py${{ matrix.python-version }}

  integration-test:
    name: Integration Tests
    runs-on: ubuntu-latest
    needs: unit-test
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install dependencies
        run: |
          pip install -e ".[all]"
          pip install pytest-asyncio respx

      - name: Run integration tests
        run: pytest tests/integration -v --tb=short
        env:
          # 使用mock服务，无需真实凭证
          BVS_TEST_MODE: "1"

  compatibility-test:
    name: Compatibility Test
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Test pip install
        run: |
          pip install -e ".[all]"
          python -c "import batch_video_skill; print(batch_video_skill.__version__)"

      - name: Test CLI
        run: |
          batch-video --help
          batch-video doctor || true  # 无凭证会失败，但命令可用

      - name: Test MCP Server startup
        run: |
          timeout 5 batch-video-mcp || true  # 测试能启动
```

### 8.4 工作流3: CD — 发布

```yaml
# .github/workflows/cd-release.yml
# ============================================================
# CD: 自动发布
# 触发: push tag v*
# ============================================================

name: CD — Release

on:
  push:
    tags:
      - "v*"  # v0.1.0, v1.0.0 等

jobs:
  # === 构建并发布到 PyPI ===
  publish-pypi:
    name: Publish to PyPI
    runs-on: ubuntu-latest
    environment:
      name: pypi
      url: https://pypi.org/p/batch-video-skill
    permissions:
      id-token: write  # OIDC用于PyPI信任发布
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install build tools
        run: pip install build

      - name: Build package
        run: python -m build

      - name: Publish to PyPI
        uses: pypa/gh-action-pypi-publish@release/v1

  # === 构建并发布 Docker 镜像 ===
  publish-docker:
    name: Publish Docker Image
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write
    steps:
      - uses: actions/checkout@v4

      - name: Set up QEMU
        uses: docker/setup-qemu-action@v3

      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3

      - name: Login to GitHub Container Registry
        uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Extract metadata
        id: meta
        uses: docker/metadata-action@v5
        with:
          images: ghcr.io/${{ github.repository }}
          tags: |
            type=semver,pattern={{version}}
            type=semver,pattern={{major}}.{{minor}}
            type=semver,pattern={{major}}

      - name: Build and push main image
        uses: docker/build-push-action@v5
        with:
          context: .
          push: true
          tags: ${{ steps.meta.outputs.tags }}
          labels: ${{ steps.meta.outputs.labels }}
          platforms: linux/amd64,linux/arm64
          cache-from: type=gha
          cache-to: type=gha,mode=max

      - name: Build and push Gemini service
        uses: docker/build-push-action@v5
        with:
          context: .
          file: ./gemini-service/Dockerfile
          push: true
          tags: |
            ghcr.io/${{ github.repository }}/gemini-service:${{ github.ref_name }}
          platforms: linux/amd64,linux/arm64
          cache-from: type=gha
          cache-to: type=gha,mode=max

  # === 创建 GitHub Release ===
  create-release:
    name: Create GitHub Release
    runs-on: ubuntu-latest
    permissions:
      contents: write
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - name: Generate changelog
        id: changelog
        run: |
          # 提取tag对应的CHANGELOG
          VERSION=${GITHUB_REF_NAME#v}
          echo "version=$VERSION" >> $GITHUB_OUTPUT

      - name: Create Release
        uses: softprops/action-gh-release@v1
        with:
          name: "Release ${{ github.ref_name }}"
          body: |
            ## 安装

            ### PyPI
            ```bash
            pip install -U batch-video-skill==${{ steps.changelog.outputs.version }}
            ```

            ### Docker
            ```bash
            docker pull ghcr.io/${{ github.repository }}:${{ github.ref_name }}
            ```
          draft: false
          prerelease: ${{ contains(github.ref_name, 'alpha') || contains(github.ref_name, 'beta') || contains(github.ref_name, 'rc') }}
```

### 8.5 工作流4: 定时任务

```yaml
# .github/workflows/ci-scheduled.yml
# ============================================================
# CI: 定时健康检查
# 触发: 每天上午8点
# 用途: 检测外部API兼容性变化
# ============================================================

name: CI — Scheduled Health Check

on:
  schedule:
    - cron: "0 8 * * *"
  workflow_dispatch:  # 支持手动触发

jobs:
  api-health-check:
    name: External API Health Check
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.12"

      - name: Install package
        run: pip install -e "."

      - name: Check API availability
        run: python scripts/health_check_external_apis.py
        env:
          # 使用加密存储的测试凭证
          KLING_API_KEY: ${{ secrets.TEST_KLING_API_KEY }}
          VEO_API_KEY: ${{ secrets.TEST_VEO_API_KEY }}

      - name: Notify on failure
        if: failure()
        uses: slackapi/slack-github-action@v1
        with:
          payload: |
            {
              "text": "⚠️ batch-video-skill 外部API健康检查失败: ${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}"
            }
        env:
          SLACK_WEBHOOK_URL: ${{ secrets.SLACK_WEBHOOK_URL }}
```

### 8.6 监控配置

#### 8.6.1 Prometheus 配置

```yaml
# monitoring/prometheus.yml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: "batch-video-skill"
    static_configs:
      - targets: ["video-skill:9090"]
    metrics_path: /metrics
    scrape_interval: 15s

  - job_name: "gemini-service"
    static_configs:
      - targets: ["gemini-service:8000"]
    metrics_path: /metrics
    scrape_interval: 30s

  - job_name: "prometheus"
    static_configs:
      - targets: ["localhost:9090"]
```

#### 8.6.2 Grafana 数据源配置

```yaml
# monitoring/grafana/datasources/prometheus.yml
apiVersion: 1

datasources:
  - name: Prometheus
    type: prometheus
    access: proxy
    url: http://prometheus:9090
    isDefault: true
    editable: false
```

---

## 9. MCP Server 部署模式

### 9.1 MCP Server 架构

```
+========================================================================+
|                     MCP Server 部署架构                                 |
+========================================================================+
|                                                                         |
|  MCP Client (Host)              MCP Server (本系统)                    |
|  +----------------+             +----------------------------------+  |
|  | Claude Desktop |◄───────────►| FastMCP Server                   |  |
|  | Cursor         |  stdio/sse  | - 10 Tools暴露                   |  |
|  | Windsurf       |             | - 自动Tool Discovery             |  |
|  +----------------+             +----------------------------------+  |
|                                                                         |
|  MCP传输协议:                                                            |
|  - stdio (默认, 本地进程通信)                                            |
|  - sse (HTTP Server-Sent Events, 远程)                                   |
|                                                                         |
|  Tool清单:                                                               |
|  1. analyze_image      — 分析商品图片                                    |
|  2. route_gem          — 匹配最佳Gem模板                                 |
|  3. build_prompt       — 生成视频提示词                                  |
|  4. generate_video     — 生成视频                                        |
|  5. check_status       — 查询任务状态                                    |
|  6. batch_process      — 批量处理                                        |
|  7. list_gems          — 列出可用Gem模板                                 |
|  8. get_progress       — 获取批量进度                                    |
|  9. retry_task         — 重试失败任务                                    |
|  10. upload_drive      — 上传视频到Drive                                 |
|                                                                         |
+========================================================================+
```

### 9.2 MCP Server 配置

#### 9.2.1 Claude Desktop 配置

```json
// ~/Library/Application Support/Claude/claude_desktop_config.json (macOS)
// %APPDATA%/Claude/claude_desktop_config.json (Windows)
{
  "mcpServers": {
    "batch-video-skill": {
      "command": "batch-video-mcp",
      "args": [],
      "env": {
        "BVS_HOME": "${HOME}/.batch-video-skill",
        "GEMINI_SECURE_1PSID": "${GEMINI_SECURE_1PSID}",
        "GEMINI_SECURE_1PSIDTS": "${GEMINI_SECURE_1PSIDTS}",
        "GWS_SERVICE_ACCOUNT_FILE": "${HOME}/.batch-video-skill/credentials/service-account.json",
        "KLING_API_KEY": "${KLING_API_KEY}",
        "VEO_API_KEY": "${VEO_API_KEY}",
        "SEEDANCE_API_KEY": "${SEEDANCE_API_KEY}"
      },
      "description": "带货视频批量生产 Skill — AI视频生成",
      "timeout": 300
    }
  }
}
```

#### 9.2.2 Cursor 配置

```json
// ~/.cursor/mcp.json
{
  "mcpServers": {
    "batch-video-skill": {
      "command": "batch-video-mcp",
      "args": [],
      "env": {
        "BVS_HOME": "${HOME}/.batch-video-skill"
      }
    }
  }
}
```

#### 9.2.3 SSE 模式 (远程服务)

```yaml
# docker-compose.mcp-sse.yml
version: "3.8"

services:
  mcp-server:
    build: .
    command: ["batch-video-mcp", "--transport", "sse", "--port", "8000"]
    environment:
      - BVS_HOME=/app/data
    ports:
      - "8000:8000"
    volumes:
      - ./data:/app/data
```

```json
// MCP Client SSE配置
{
  "mcpServers": {
    "batch-video-skill": {
      "url": "http://localhost:8000/sse",
      "timeout": 300
    }
  }
}
```

### 9.3 MCP Tool 定义

```python
"""
MCP Server 入口 — 10个Tool暴露
基于 FastMCP 框架
"""

from mcp.server.fastmcp import FastMCP
from loguru import logger

# 创建MCP服务器
mcp = FastMCP("batch-video-skill")

# ============================================================
# Tool 1: analyze_image — 分析商品图片
# ============================================================
@mcp.tool()
async def analyze_image(
    image_path: str,
    user_description: str = "",
) -> dict:
    """
    分析商品图片，提取4维度特征(产品/人群/场景/风格)。
    
    Args:
        image_path: 商品图片的本地路径或URL
        user_description: 用户对产品的补充描述(可选)
    
    Returns:
        {
            "product": {"category": "", "features": [], "price_tier": ""},
            "audience": {"gender": "", "age_range": "", "persona": ""},
            "scene": {"marketing": "", "usage": "", "platform": ""},
            "style": {"video_style": "", "tone": "", "tempo": ""},
            "confidence": 0.95
        }
    """
    pass

# ============================================================
# Tool 2: route_gem — 匹配最佳Gem模板
# ============================================================
@mcp.tool()
async def route_gem(
    analysis_result: dict,
    top_n: int = 3,
) -> dict:
    """
    根据图片分析结果匹配最佳Gem模板。
    
    Args:
        analysis_result: analyze_image的输出结果
        top_n: 返回推荐数量 (1-5)
    
    Returns:
        {
            "recommendations": [
                {"gem_id": "", "name": "", "match_score": 0.92, "reason": ""}
            ],
            "confidence": "HIGH"
        }
    """
    pass

# ============================================================
# Tool 3: build_prompt — 生成视频提示词
# ============================================================
@mcp.tool()
async def build_prompt(
    gem_id: str,
    image_path: str,
    analysis_result: dict,
) -> dict:
    """
    调用Gem生成视频生成提示词。
    
    Args:
        gem_id: 选中的Gem模板ID
        image_path: 商品图片路径
        analysis_result: 图片分析结果
    
    Returns:
        {
            "prompt": "详细的视频生成提示词...",
            "negative_prompt": "",
            "params": {"duration": 5, "resolution": "1080p"}
        }
    """
    pass

# ============================================================
# Tool 4: generate_video — 生成视频
# ============================================================
@mcp.tool()
async def generate_video(
    prompt: str,
    image_path: str,
    provider: str = "auto",
    duration: int = 5,
    resolution: str = "1080p",
    aspect_ratio: str = "9:16",
) -> dict:
    """
    生成带货视频。
    
    Args:
        prompt: 视频生成提示词
        image_path: 参考图片路径
        provider: 视频提供商 (auto/kling/veo/seedance/vidu/runway)
        duration: 视频时长(秒)
        resolution: 分辨率 (720p/1080p/4k)
        aspect_ratio: 宽高比 (9:16/16:9/1:1/3:4)
    
    Returns:
        {
            "task_id": "任务ID",
            "status": "PENDING",
            "provider": "kling",
            "estimated_time": 120
        }
    """
    pass

# ============================================================
# Tool 5: check_status — 查询任务状态
# ============================================================
@mcp.tool()
async def check_status(
    task_id: str,
) -> dict:
    """
    查询视频生成任务的状态。
    
    Args:
        task_id: 任务ID
    
    Returns:
        {
            "task_id": "",
            "status": "COMPLETED",
            "video_url": "",
            "progress": 100,
            "elapsed_time": 125
        }
    """
    pass

# ============================================================
# Tool 6: batch_process — 批量处理
# ============================================================
@mcp.tool()
async def batch_process(
    image_paths: list[str],
    max_concurrency: int = 3,
    auto_upload: bool = True,
) -> dict:
    """
    批量处理多张商品图片生成视频。
    
    Args:
        image_paths: 商品图片路径列表
        max_concurrency: 最大并发数
        auto_upload: 是否自动上传到Drive
    
    Returns:
        {
            "batch_id": "",
            "total": 10,
            "completed": 0,
            "failed": 0,
            "in_progress": 3,
            "estimated_completion": "2026-07-15T14:30:00Z"
        }
    """
    pass

# ============================================================
# Tool 7: list_gems — 列出可用Gem模板
# ============================================================
@mcp.tool()
async def list_gems(
    category: str = "",
) -> dict:
    """
    列出所有可用的Gem模板。
    
    Args:
        category: 按品类过滤 (可选)
    
    Returns:
        {
            "gems": [
                {"id": "", "name": "", "category": "", "description": ""}
            ],
            "total": 50
        }
    """
    pass

# ============================================================
# Tool 8: get_progress — 获取批量进度
# ============================================================
@mcp.tool()
async def get_progress(
    batch_id: str,
) -> dict:
    """
    获取批量任务的详细进度。
    
    Args:
        batch_id: 批次ID
    
    Returns:
        {
            "batch_id": "",
            "total": 10,
            "completed": 7,
            "failed": 1,
            "in_progress": 2,
            "tasks": [
                {"task_id": "", "status": "", "image": "", "gem": ""}
            ]
        }
    """
    pass

# ============================================================
# Tool 9: retry_task — 重试失败任务
# ============================================================
@mcp.tool()
async def retry_task(
    task_id: str,
) -> dict:
    """
    重试失败的任务。
    
    Args:
        task_id: 失败的任务ID
    
    Returns:
        {
            "task_id": "",
            "new_status": "PENDING",
            "message": "任务已重新提交"
        }
    """
    pass

# ============================================================
# Tool 10: upload_drive — 上传视频到Drive
# ============================================================
@mcp.tool()
async def upload_drive(
    video_path: str,
    folder_id: str = "",
) -> dict:
    """
    上传视频到Google Drive。
    
    Args:
        video_path: 视频文件路径
        folder_id: Drive文件夹ID (可选，使用默认文件夹)
    
    Returns:
        {
            "file_id": "",
            "file_url": "https://drive.google.com/...",
            "status": "uploaded"
        }
    """
    pass


# ============================================================
# MCP Server 入口
# ============================================================
def main():
    """启动MCP Server"""
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--transport", choices=["stdio", "sse"], default="stdio")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    if args.transport == "sse":
        mcp.run(transport="sse", port=args.port)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
```

---

## 10. 完整部署指南

### 10.1 部署方式A: pip安装 (推荐个人用户)

```bash
# === 前置要求 ===
# - Python 3.11+
# - pip
# - 稳定的网络连接

# 1. 环境检查
curl -fsSL https://raw.githubusercontent.com/300-agent-team/batch-video-skill/main/scripts/check_env.py | python3

# 2. 安装包
pip install batch-video-skill

# 3. 初始化配置
bvs init

# 4. 设置认证
# 4.1 Service Account
# 从 https://console.cloud.google.com/iam-admin/serviceaccounts 创建
# 下载JSON密钥文件
# 启用 Sheets API, Drive API, Gmail API
cp /path/to/service-account.json ~/.batch-video-skill/credentials/

# 4.2 Gemini Cookie
# 打开 Chrome 开发者工具 → Application → Cookies → google.com
# 复制 __Secure-1PSID 和 __Secure-1PSIDTS
export GEMINI_SECURE_1PSID="g.a000..."
export GEMINI_SECURE_1PSIDTS="bdch..."
# 建议写入 ~/.batch-video-skill/.env

# 4.3 视频API Keys
# 可灵: https://www.klingai.com/
# Veo: https://aistudio.google.com/
# 即梦: https://jimeng.jianying.com/
export KLING_API_KEY="sk_..."
export VEO_API_KEY="AIzaSy..."
export SEEDANCE_API_KEY="sk-..."

# 5. 验证安装
bvs doctor

# 6. 测试运行
bvs process /path/to/product-image.png --description "一款高端蓝牙耳机"
```

### 10.2 部署方式B: Docker (推荐团队/生产)

```bash
# === 前置要求 ===
# - Docker 24.0+
# - Docker Compose

# 1. 克隆仓库
git clone https://github.com/300-agent-team/batch-video-skill.git
cd batch-video-skill

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env 填写所有认证信息

# 3. 放置Service Account
mkdir -p secrets
cp /path/to/service-account.json secrets/

# 4. 启动服务
docker-compose up -d

# 5. 验证
docker-compose exec video-skill batch-video doctor

# 6. 使用
docker-compose exec video-skill batch-video process /data/input/image.png

# 7. 查看输出
docker-compose exec video-skill batch-video list-output
# 或直接在主机的 ./output 目录查看
```

### 10.3 部署方式C: MCP Server (推荐AI Agent集成)

```bash
# === 前置要求 ===
# - 已完成pip安装或Docker部署
# - MCP Client (Claude Desktop / Cursor / Windsurf)

# 1. 确认MCP Server命令可用
which batch-video-mcp

# 2. 配置MCP Client
# 根据使用的Client编辑配置文件:

# Claude Desktop (macOS):
# ~/Library/Application Support/Claude/claude_desktop_config.json
# {
#   "mcpServers": {
#     "batch-video-skill": {
#       "command": "batch-video-mcp",
#       "env": { "BVS_HOME": "..." }
#     }
#   }
# }

# Cursor:
# ~/.cursor/mcp.json
# (同上格式)

# 3. 重启MCP Client

# 4. 验证
# 在Claude/Cursor中输入:
# "请使用batch-video-skill分析这张产品图片"
# 应该能看到Tool调用
```

### 10.4 部署检查清单

| 检查项 | pip模式 | Docker模式 | MCP模式 |
|--------|:-------:|:----------:|:-------:|
| Python 3.11+ | ✅ | N/A | ✅ |
| Docker安装 | N/A | ✅ | N/A |
| `bvs init`完成 | ✅ | N/A | ✅ |
| Service Account JSON | ✅ | ✅ | ✅ |
| Gemini Cookie设置 | ✅ | ✅ | ✅ |
| 至少1个视频API Key | ✅ | ✅ | ✅ |
| `bvs doctor`通过 | ✅ | ✅ | ✅ |
| MCP Client配置 | N/A | N/A | ✅ |
| 测试单图片处理 | ✅ | ✅ | ✅ |

---

## 11. 运维手册

### 11.1 日志管理

```bash
# 查看实时日志 (pip模式)
tail -f ~/.batch-video-skill/logs/skill.log

# 查看实时日志 (Docker模式)
docker-compose logs -f video-skill

# 查看历史日志
bvs logs --date 2026-07-15 --level ERROR

# 清理旧日志
bvs logs cleanup --retention 30days
```

### 11.2 更新维护

```bash
# === 更新到最新版本 ===

# pip模式
pip install -U batch-video-skill
bvs doctor  # 验证更新

# Docker模式
docker-compose pull
docker-compose up -d

# 查看当前版本
bvs --version
```

### 11.3 备份与恢复

```bash
# === 备份 ===
# 备份配置和凭证
tar czvf bvs-backup-$(date +%Y%m%d).tar.gz ~/.batch-video-skill/

# 备份Sheets数据 (导出到本地)
bvs workspace export --sheet gems --format csv
bvs workspace export --sheet tasks --format csv

# === 恢复 ===
# 恢复配置和凭证
tar xzvf bvs-backup-20260715.tar.gz -C ~/

# 重新验证
bvs doctor
```

### 11.4 故障排查

| 症状 | 可能原因 | 解决方案 |
|------|----------|----------|
| `bvs doctor` Cookie检查失败 | Cookie过期 | 重新设置环境变量或运行`bvs auth setup-gemini` |
| 视频生成超时 | 提供商服务慢 | 增加`BVS_REQUEST_TIMEOUT`，检查提供商状态 |
| Sheets写入失败 | API限流 | 减少并发数，检查Service Account权限 |
| Gemini服务无响应 | 服务未启动 | `bvs gemini-service start` 或检查Docker容器 |
| 所有API都失败 | 网络问题 | 检查代理设置，运行`bvs doctor`网络检查 |
| 内存不足 | 并发数过高 | 降低`BVS_MAX_CONCURRENCY` |
| 磁盘空间不足 | 视频文件堆积 | 清理旧输出，设置自动清理 |

### 11.5 健康检查端点

```python
# 主进程健康检查
GET http://localhost:8000/health
Response:
{
    "status": "healthy",
    "version": "0.1.0",
    "timestamp": "2026-07-15T14:30:00Z",
    "checks": {
        "gemini_service": "ok",
        "cookie_valid": true,
        "sheets_access": true,
        "video_providers": {
            "kling": "ok",
            "veo": "ok",
            "seedance": "rate_limited"
        }
    }
}

# Gemini服务健康检查
GET http://localhost:8001/health
Response:
{
    "status": "healthy",
    "cookie_valid": true,
    "last_refresh": "2026-07-15T14:00:00Z"
}

# Prometheus指标
GET http://localhost:9090/metrics
Response: (Prometheus格式文本)
# HELP bvs_tasks_total Total number of tasks
# TYPE bvs_tasks_total counter
bvs_tasks_total{status="completed"} 42
bvs_tasks_total{status="failed"} 3
...
```

---

## 12. 附录

### 附录A: 部署相关ADR

#### ADR-DEP-001: 纯客户端架构

| 属性 | 内容 |
|------|------|
| **状态** | 已接受 |
| **背景** | 系统需要部署在用户本机，不应依赖云端服务 |
| **决策** | 采用纯客户端架构，所有业务逻辑在本地运行，云端仅作为API提供方 |
| **替代方案** | SaaS服务 / 客户端-服务器架构 |
| **影响** | 正面: 零服务器成本，数据不出本地，无单点故障。负面: 依赖用户机器性能，无法多设备共享状态 |
| **风险** | 用户环境差异 → mitigate: Docker标准化，环境检查脚本 |

#### ADR-DEP-002: pip作为首选分发方式

| 属性 | 内容 |
|------|------|
| **状态** | 已接受 |
| **背景** | Python开发者偏好pip安装，非技术用户偏好Docker |
| **决策** | pip为默认推荐安装方式，Docker为备选，两者同时支持 |
| **替代方案** | 仅Docker / 仅pip / Homebrew / conda |
| **影响** | 正面: 覆盖最大用户群体，符合Python生态习惯。负面: 需要处理Python版本兼容性 |
| **风险** | Python版本冲突 → mitigate: 版本检查，虚拟环境推荐 |

#### ADR-DEP-003: AGPL隔离容器

| 属性 | 内容 |
|------|------|
| **状态** | 已接受 |
| **背景** | gemini-webapi使用AGPL-3.0，与主项目MIT许可证不兼容 |
| **决策** | gemini-webapi运行在独立Docker容器，通过HTTP通信 |
| **替代方案** | 不使用gemini-webapi / 主项目也使用AGPL |
| **影响** | 正面: 许可证隔离，主项目保持MIT。负面: 部署多一个容器 |
| **风险** | 通信延迟 → mitigate: 同机部署，Unix Socket连接 |

#### ADR-DEP-004: MCP stdio作为默认传输

| 属性 | 内容 |
|------|------|
| **状态** | 已接受 |
| **背景** | MCP支持stdio和sse两种传输协议 |
| **决策** | stdio为默认（本地进程通信），sse为可选（远程服务） |
| **替代方案** | 仅sse / 仅stdio |
| **影响** | 正面: 零网络配置，安全。负面: 只能本机使用 |
| **风险** | 某些Client不支持stdio → mitigate: sse作为备选 |

### 附录B: 文件清单

| 文件 | 路径 | 说明 |
|------|------|------|
| pyproject.toml | 项目根目录 | Python包配置 |
| Dockerfile | 项目根目录 | 主应用容器 |
| docker-compose.yml | 项目根目录 | 完整编排配置 |
| docker-compose.override.yml | 项目根目录 | 开发模式覆盖 |
| .env.example | 项目根目录 | 环境变量模板 |
| check_env.py | scripts/ | 环境检查脚本 |
| install.sh | scripts/ | 一键安装脚本 |
| ci-quality.yml | .github/workflows/ | 代码质量CI |
| ci-test.yml | .github/workflows/ | 测试CI |
| cd-release.yml | .github/workflows/ | 发布CD |
| prometheus.yml | monitoring/ | 监控配置 |
| grafana/ | monitoring/grafana/ | 仪表盘配置 |

### 附录C: 版本兼容性矩阵

| 版本 | Python | 支持的模型 | 支持的Client |
|------|:------:|----------|-------------|
| v0.1.0 | 3.11-3.13 | Kling, Veo, Seedance | Claude, Cursor |
| v0.2.0 | 3.11-3.13 | +Vidu, Runway | +Windsurf |
| v1.0.0 | 3.12+ | +Pika | SSE传输 |

---

> **文档结束**
>
> 本文档为 300 Agent 融合项目 Stage-3 的部署架构设计，定义了完整的安装流程、Docker支持和CI/CD配置。
> 下一步: Stage-4 详细设计和代码实现。
>
> 生成时间: 2026-07
> 部署架构版本: v1.0
> 覆盖部署模式: 3种 (pip/Docker/MCP)
> CI/CD工作流: 4个
