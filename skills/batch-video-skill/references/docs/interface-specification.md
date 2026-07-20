# 带货视频批量生产 Skill — 接口规范总设计

> **文档版本**: v1.0
> **生成日期**: 2026-07
> **设计输入**: Stage-3 全部模块设计文档（8大模块）
> **设计范围**: MCP Server / CLI / Python API / 配置 / 事件 五大接口

---

## 目录

1. [概述与设计原则](#1-概述与设计原则)
2. [全局错误码体系](#2-全局错误码体系)
3. [MCP Server 接口](#3-mcp-server-接口)
4. [CLI 接口](#4-cli-接口)
5. [Python API 接口](#5-python-api-接口)
6. [配置接口](#6-配置接口)
7. [事件系统](#7-事件系统)
8. [完整代码示例](#8-完整代码示例)

---

## 1. 概述与设计原则

### 1.1 接口分层架构

```
+========================================================================+
|                        用户交互层 (User Interface)                      |
+========================================================================+
|                                                                        |
|  ┌─────────────────┐  ┌─────────────────┐  ┌──────────────────────┐   |
|  |  MCP Server      |  |  CLI 工具       |  |  Python SDK          |   |
|  |  (协议标准化)     |  |  (命令行交互)    |  |  (编程接口)           |   |
|  |                  |  |                 |  |                      |   |
|  |  FastMCP 框架    |  |  Click 框架     |  |  类型注解 + 异步      |   |
|  |  10 个工具暴露    |  |  5 个子命令     |  |  公共类 + 事件系统    |   |
|  +--------+--------+  +--------+--------+  +----------+-----------+   |
|           |                    |                      |                |
+-----------|--------------------|----------------------|----------------+
|           |                    |                      |                 |
|           ▼                    ▼                      ▼                 |
|  +===============================================================+     |
|  |                    业务编排层 (Orchestration Layer)              |     |
|  |                                                               |     |
|  |  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐|     |
|  |  |TaskController│  | SmartRouter  │  |BatchProcessor        │|     |
|  |  | (任务编排)    |  | (智能路由)    |  | (批量处理)            │|     |
|  |  └──────────────┘  └──────────────┘  └──────────────────────┘|     |
|  +===============================================================+     |
|           |                    |                      |                 |
|           ▼                    ▼                      ▼                 |
|  +===============================================================+     |
|  |                    服务层 (Service Layer)                        |     |
|  |                                                               |     |
|  |  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐|     |
|  |  |GeminiService │  |VideoGenerator│  |WorkspaceClient       │|     |
|  |  | (Gemini服务)  |  | (视频生成)    |  | (Workspace集成)       │|     |
|  |  └──────────────┘  └──────────────┘  └──────────────────────┘|     |
|  +===============================================================+     |
|                                                                        |
+========================================================================+
```

### 1.2 设计原则

| 原则 | 说明 | 实践 |
|------|------|------|
| **一致性** | 所有接口层共享同一业务逻辑 | 三层共用 TaskController |
| **类型安全** | 全链路 Pydantic 类型校验 | 输入/输出/配置全模型化 |
| **错误标准化** | 统一错误码贯穿所有接口 | 7大类40+错误码 |
| **可观测性** | 所有操作可追踪 | trace_id 全链路传递 |
| **向后兼容** | 接口变更不破坏旧客户端 | 版本号 + 废弃标记 |

### 1.3 接口对应关系

| 业务能力 | MCP Tool | CLI 命令 | Python API | 底层模块 |
|----------|----------|----------|------------|----------|
| 单产品处理 | `generate_video_single` | `skill single` | `TaskController.process_single()` | TaskScheduler |
| 批量处理 | `generate_video_batch` | `skill batch` | `TaskController.process_batch()` | BatchProcessor |
| 进度查询 | `get_batch_progress` | `skill status` | `TaskController.get_progress()` | ProgressTracker |
| 图片分析 | `analyze_image` | `skill analyze` | `ImageAnalyzer.analyze()` | ImageAnalyzer |
| 智能路由 | `match_gem_template` | `skill match` | `SmartRouter.route()` | RouteMatcher |
| 提示词生成 | `generate_prompt` | `skill prompt` | `PromptBuilder.build_prompt()` | PromptGenerator |
| 视频生成 | `generate_video_raw` | `skill video` | `VideoGenerator.generate()` | UnifiedVideoGen |
| Gem管理 | `list_gems` | `skill gems` | `GemManager.list_gems()` | GemRegistry |
| 系统健康 | `health_check` | `skill health` | `SkillClient.health_check()` | HealthChecker |
| 配置管理 | `get_config` | `skill config` | `ConfigManager.get()` | ConfigManager |

---

## 2. 全局错误码体系

### 2.1 错误码结构

```
错误码格式: [Category][3位数字]
示例: C002 = Client Error 002 (参数验证错误)
```

| 分类前缀 | 范围 | 说明 | 重试策略 | HTTP映射 |
|----------|------|------|----------|----------|
| `C` | C001-C499 | 客户端错误 | **不重试** | 400 |
| `S` | S001-S199 | 服务端错误 | **可重试** | 500/503 |
| `N` | N001-N099 | 网络错误 | **可重试** | — |
| `T` | T001-T299 | 任务错误 | **部分可重试** | — |
| `R` | R001-R099 | 资源错误 | **部分可重试** | 404 |
| `F` | F001-F099 | 配置错误 | **不重试** | — |
| `E` | E001-E099 | 系统错误 | **视情况** | — |

### 2.2 完整错误码定义表

| 错误码 | 名称 | 说明 | 重试 | 典型场景 |
|--------|------|------|:----:|----------|
| **C001** | `INVALID_REQUEST` | 请求格式错误 | No | JSON解析失败 |
| **C002** | `VALIDATION_ERROR` | 参数验证失败 | No | 必填字段缺失 |
| **C003** | `UNSUPPORTED_FORMAT` | 不支持的格式 | No | 图片格式不支持 |
| **C004** | `UNSUPPORTED_MODEL` | 不支持的模型 | No | 指定了未注册的模型 |
| **C101** | `AUTH_ERROR` | 认证失败 | No | Cookie过期 |
| **C102** | `PERMISSION_DENIED` | 权限不足 | No | 无权访问功能 |
| **C201** | `QUOTA_EXCEEDED` | 配额超限 | No | 月度配额用完 |
| **C202** | `RATE_LIMITED` | 速率限制 | **Yes** | 请求过于频繁 |
| **S001** | `SERVER_OVERLOADED` | 服务器过载 | **Yes** | 服务繁忙 |
| **S002** | `SERVICE_UNAVAILABLE` | 服务不可用 | **Yes** | 维护中 |
| **S003** | `CIRCUIT_BREAKER_OPEN` | 熔断器打开 | **Yes** | 连续失败触发熔断 |
| **N001** | `CONNECTION_TIMEOUT` | 连接超时 | **Yes** | TCP连接失败 |
| **N002** | `READ_TIMEOUT` | 读取超时 | **Yes** | 响应超时 |
| **N003** | `CONNECTION_RESET` | 连接重置 | **Yes** | 网络中断 |
| **T001** | `TASK_TIMEOUT` | 任务超时 | **Yes** | 视频生成超时 |
| **T002** | `TASK_FAILED` | 任务失败 | No | 业务逻辑错误 |
| **T003** | `TASK_CANCELLED` | 任务取消 | No | 用户主动取消 |
| **T004** | `SAFETY_BLOCKED` | 安全拦截 | No | 内容审核不通过 |
| **T005** | `GENERATION_ERROR` | 生成失败 | **Yes** | AI生成异常 |
| **T201** | `ALL_PROVIDERS_FAILED` | 全部Provider失败 | No | 降级链耗尽 |
| **R001** | `RESOURCE_NOT_FOUND` | 资源不存在 | No | 任务ID无效 |
| **R002** | `STORAGE_FULL` | 存储已满 | No | 磁盘/Drive空间不足 |
| **F001** | `CONFIG_INVALID` | 配置无效 | No | YAML格式错误 |
| **F002** | `CREDENTIAL_EXPIRED` | 凭证过期 | No | SA密钥过期 |
| **F003** | `ENV_VAR_MISSING` | 环境变量缺失 | No | 必需变量未设置 |
| **E001** | `INTERNAL_ERROR` | 内部错误 | **视情况** | 未预料的异常 |

### 2.3 错误响应结构

```python
# 所有接口统一错误响应格式（Pydantic模型）
class ErrorResponse(BaseModel):
    """标准化错误响应"""
    error_code: str           # 错误码，如 "C002"
    error_category: str       # 错误分类，如 "client"
    message: str              # 人类可读的错误消息
    retryable: bool           # 是否可重试
    retry_after_sec: int = 0  # 建议重试等待时间（秒）
    trace_id: str             # 追踪ID，用于问题排查
    details: dict[str, Any] = Field(default_factory=dict)  # 扩展信息
    remediation_hint: str = ""  # 修复建议
```

**错误响应示例（JSON）**:
```json
{
  "error_code": "S003",
  "error_category": "server",
  "message": "视频生成服务熔断器已打开，暂时不可用",
  "retryable": true,
  "retry_after_sec": 30,
  "trace_id": "req_abc123xyz",
  "details": {
    "circuit_breaker": "video_gen_seedance",
    "state": "open",
    "failure_rate": 0.65,
    "open_duration_remaining": 18
  },
  "remediation_hint": "等待自动恢复或联系运维团队"
}
```

---

## 3. MCP Server 接口

### 3.1 MCP Server 架构

```
┌─────────────────────────────────────────────────────────────┐
│                    MCP Server (FastMCP)                       │
│                                                               │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │              Transport Layer (stdio/sse)                 │ │
│  └────────────────────────┬────────────────────────────────┘ │
│                           │                                   │
│  ┌────────────────────────▼────────────────────────────────┐ │
│  │              Tool Registry (10 tools)                    │ │
│  │                                                          │ │
│  │  generate_video_single  ●  generate_video_batch         │ │
│  │  get_batch_progress     ●  analyze_image                │ │
│  │  match_gem_template     ●  generate_prompt              │ │
│  │  generate_video_raw     ●  list_gems                    │ │
│  │  health_check           ●  get_config                   │ │
│  └────────────────────────┬────────────────────────────────┘ │
│                           │                                   │
│  ┌────────────────────────▼────────────────────────────────┐ │
│  │              Business Facade                             │ │
│  │              (TaskController)                            │ │
│  └─────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

### 3.2 MCP 工具定义总表

#### T01: generate_video_single — 单产品视频生成

| 属性 | 说明 |
|------|------|
| **描述** | 为单个产品图片生成带货视频（完整流程：分析→路由→提示词→视频） |
| **幂等性** | 否（每次调用生成新视频） |
| **超时** | 180秒 |

**输入参数**:

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|:----:|--------|------|
| `image_path` | `str` | ✅ | — | 产品图片路径（本地路径或URL） |
| `user_description` | `str` | — | `""` | 用户对产品的补充描述 |
| `preferred_gem_id` | `str` | — | `null` | 指定Gem模板ID（可选） |
| `preferred_provider` | `str` | — | `null` | 指定视频提供商（可选） |
| `output_resolution` | `str` | — | `"1080p"` | 输出分辨率: 540p/720p/1080p/4k |
| `output_duration` | `int` | — | `5` | 视频时长（秒），4-16 |
| `generate_audio` | `bool` | — | `true` | 是否生成音频 |

**返回值**:

| 字段 | 类型 | 说明 |
|------|------|------|
| `task_id` | `str` | 任务唯一标识 |
| `status` | `str` | 最终状态: completed/failed |
| `video_url` | `str \| null` | 视频下载URL |
| `video_local_path` | `str \| null` | 本地保存路径 |
| `matched_gem_id` | `str` | 匹配的Gem模板ID |
| `matched_gem_name` | `str` | 匹配的Gem模板名称 |
| `match_score` | `float` | 匹配分数（0-1） |
| `generated_prompt` | `str` | 生成的视频提示词 |
| `video_provider` | `str` | 实际使用的视频提供商 |
| `cost_usd` | `float` | 花费（美元） |
| `generation_time_sec` | `float` | 生成耗时（秒） |
| `error_message` | `str \| null` | 错误信息（失败时） |

---

#### T02: generate_video_batch — 批量视频生成

| 属性 | 说明 |
|------|------|
| **描述** | 为多个产品图片批量生成带货视频 |
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
| `upload_to_drive` | `bool` | — | `true` | 完成后上传Google Drive |

**返回值**:

| 字段 | 类型 | 说明 |
|------|------|------|
| `batch_id` | `str` | 批次唯一标识 |
| `total_tasks` | `int` | 总任务数 |
| `status` | `str` | 批次状态: running/paused/completed/failed |
| `message` | `str` | 状态说明 |

---

#### T03: get_batch_progress — 查询批量任务进度

| 属性 | 说明 |
|------|------|
| **描述** | 查询指定批次的处理进度 |
| **幂等性** | 是 |
| **超时** | 10秒 |

**输入参数**:

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|:----:|------|
| `batch_id` | `str` | ✅ | 批次ID |

**返回值**:

| 字段 | 类型 | 说明 |
|------|------|------|
| `batch_id` | `str` | 批次ID |
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
| `throughput` | `float` | 吞吐量（任务/秒） |
| `is_complete` | `bool` | 是否已完成 |

---

#### T04: analyze_image — 图片深度分析

| 属性 | 说明 |
|------|------|
| **描述** | 对产品图片进行4维度深度分析（产品/人群/场景/风格） |
| **幂等性** | 是（缓存结果） |
| **超时** | 30秒 |

**输入参数**:

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|:----:|--------|------|
| `image_path` | `str` | ✅ | — | 图片路径或URL |
| `dimensions` | `list[str]` | — | `["product","audience","scene","style"]` | 分析维度 |
| `platform_hint` | `str` | — | `null` | 平台偏好（如douyin/xiaohongshu） |

**返回值**:

| 字段 | 类型 | 说明 |
|------|------|------|
| `features` | `ImageFeatures` | 标准化特征向量（见下方） |
| `confidence_score` | `float` | 整体置信度（0-1） |
| `completeness_score` | `float` | 特征完整度（0-1） |
| `reliability` | `str` | 可靠性: reliable/moderate/unreliable |
| `cache_hit` | `bool` | 是否命中缓存 |
| `analysis_time_ms` | `int` | 分析耗时（毫秒） |

**ImageFeatures 结构**:
```json
{
  "category": "makeup",
  "product_type": "lipstick",
  "price_tier": "mid_range",
  "brand_tier": "premium",
  "selling_points": ["trending", "premium_packaging"],
  "target_gender": "female",
  "target_age": "gen_z",
  "spending_power": "middle",
  "personas": ["fashion_forward", "quality_first"],
  "marketing_scene": "new_launch",
  "usage_scenes": ["daily", "gift"],
  "target_platform": "douyin",
  "video_style": "fast_paced",
  "visual_tone": "bright_vibrant",
  "dominant_colors": ["#FF6B9D", "#FFD93D"]
}
```

---

#### T05: match_gem_template — 智能路由匹配

| 属性 | 说明 |
|------|------|
| **描述** | 根据图片分析结果匹配最佳Gem模板 |
| **幂等性** | 是 |
| **超时** | 10秒 |

**输入参数**:

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|:----:|--------|------|
| `features` | `dict` | ✅ | — | 图片特征（analyze_image的返回值） |
| `top_n` | `int` | — | `3` | 返回Top N匹配 |
| `min_score` | `float` | — | `0.5` | 最低匹配分（0-1） |
| `platform_hint` | `str` | — | `null` | 平台偏好 |

**返回值**:

| 字段 | 类型 | 说明 |
|------|------|------|
| `matches` | `list[MatchItem]` | 匹配结果列表 |
| `confidence_level` | `str` | 置信度: high/medium/low |
| `reasoning` | `str` | 匹配推理说明 |
| `elapsed_ms` | `int` | 匹配耗时（毫秒） |

**MatchItem 结构**:
```json
{
  "gem_id": "gem_001",
  "gem_name": "超市疯抢模板",
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

#### T06: generate_prompt — 提示词生成

| 属性 | 说明 |
|------|------|
| **描述** | 调用Gem生成视频提示词 |
| **幂等性** | 否 |
| **超时** | 60秒 |

**输入参数**:

| 参数名 | 类型 | 必填 | 说明 |
|--------|------|:----:|------|
| `image_path` | `str` | ✅ | 产品图片路径 |
| `gem_id` | `str` | ✅ | Gem模板ID |
| `features` | `dict` | — | 图片特征（可选，不提供则自动分析） |
| `user_description` | `str` | — | 用户补充描述 |

**返回值**:

| 字段 | 类型 | 说明 |
|------|------|------|
| `prompt` | `str` | 生成的视频提示词 |
| `gem_used` | `str` | 使用的Gem ID |
| `model_used` | `str` | 使用的Gemini模型 |
| `is_valid` | `bool` | 是否通过校验 |
| `validation_details` | `dict` | 校验详情 |
| `generation_time_ms` | `int` | 生成耗时（毫秒） |

---

#### T07: generate_video_raw — 原始视频生成

| 属性 | 说明 |
|------|------|
| **描述** | 直接调用视频生成API（不包含分析和提示词生成） |
| **幂等性** | 否 |
| **超时** | 600秒 |

**输入参数**:

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|:----:|--------|------|
| `prompt` | `str` | ✅ | — | 视频生成提示词 |
| `reference_image` | `str` | — | `null` | 参考图片路径 |
| `provider` | `str` | — | `"seedance"` | 视频提供商 |
| `model_version` | `str` | — | `null` | 模型版本 |
| `resolution` | `str` | — | `"1080p"` | 分辨率 |
| `duration` | `int` | — | `5` | 时长（秒） |
| `generate_audio` | `bool` | — | `true` | 是否生成音频 |

**返回值**:

| 字段 | 类型 | 说明 |
|------|------|------|
| `task_id` | `str` | 视频任务ID |
| `provider` | `str` | 实际使用的提供商 |
| `status` | `str` | 状态: succeeded/failed |
| `video_url` | `str \| null` | 视频URL |
| `video_local_path` | `str \| null` | 本地路径 |
| `cost_usd` | `float` | 花费（美元） |
| `generation_time_sec` | `float` | 生成耗时（秒） |

---

#### T08: list_gems — 列出Gem模板

| 属性 | 说明 |
|------|------|
| **描述** | 列出所有可用的Gem模板 |
| **幂等性** | 是 |
| **超时** | 10秒 |

**输入参数**:

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|:----:|--------|------|
| `category` | `str` | — | `null` | 按分类筛选 |
| `active_only` | `bool` | — | `true` | 仅返回启用的Gem |
| `refresh` | `bool` | — | `false` | 强制刷新缓存 |

**返回值**:

| 字段 | 类型 | 说明 |
|------|------|------|
| `gems` | `list[GemInfo]` | Gem列表 |
| `total` | `int` | 总数 |
| `last_sync_at` | `str \| null` | 最后同步时间 |

---

#### T09: health_check — 系统健康检查

| 属性 | 说明 |
|------|------|
| **描述** | 检查系统各组件健康状态 |
| **幂等性** | 是 |
| **超时** | 10秒 |

**输入参数**: 无

**返回值**:

| 字段 | 类型 | 说明 |
|------|------|------|
| `status` | `str` | 整体状态: healthy/degraded/unhealthy |
| `version` | `str` | Skill版本号 |
| `checks` | `dict[str, HealthCheck]` | 各组件检查结果 |
| `latency_ms` | `int` | 检查耗时（毫秒） |

**HealthCheck 结构**:
```json
{
  "gemini_service": {
    "status": "healthy",
    "latency_ms": 120,
    "message": "Gemini WebAPI响应正常"
  },
  "video_seedance": {
    "status": "healthy",
    "latency_ms": 450,
    "message": "即梦API响应正常"
  },
  "video_kling": {
    "status": "degraded",
    "latency_ms": 8000,
    "message": "可灵API延迟较高"
  },
  "workspace": {
    "status": "healthy",
    "latency_ms": 230,
    "message": "Google Workspace连接正常"
  }
}
```

---

#### T10: get_config — 获取配置

| 属性 | 说明 |
|------|------|
| **描述** | 获取当前系统配置（脱敏） |
| **幂等性** | 是 |
| **超时** | 5秒 |

**输入参数**:

| 参数名 | 类型 | 必填 | 默认值 | 说明 |
|--------|------|:----:|--------|------|
| `section` | `str` | — | `null` | 配置分组（如 gemini/video/router） |

**返回值**:

| 字段 | 类型 | 说明 |
|------|------|------|
| `config` | `dict` | 配置项字典（敏感值已脱敏） |
| `source` | `str` | 配置来源: env/file/default |
| `last_reload_at` | `str` | 最后重载时间 |

---

### 3.3 MCP 错误处理

```python
# MCP 工具的错误处理规范
class MCPToolError(Exception):
    """MCP 工具错误 — 所有工具抛出的异常必须转换为此格式"""
    
    def __init__(
        self,
        error_code: str,           # 统一错误码
        message: str,              # 人类可读消息
        retryable: bool = False,   # 是否可重试
        details: dict | None = None,
    ):
        ...

# 工具内部异常 → MCP 错误响应转换规则
# Python异常              → MCP错误码
# ValidationError         → C002
# AuthError               → C101
# QuotaExceededError      → C201
# RateLimitError          → C202
# ConnectionTimeoutError  → N001
# TaskTimeoutError        → T001
# TaskFailedError         → T002
# SafetyBlockedError      → T004
# AllProvidersFailedError → T201
# ConfigNotFoundError     → F001
# InternalError           → E001
```

---

## 4. CLI 接口

### 4.1 CLI 命令结构

```
skill                          # 主命令（入口）
├── single                    # 单产品视频生成
├── batch                     # 批量视频生成
├── status                    # 查询任务状态
├── analyze                   # 图片分析
├── match                     # 智能路由匹配
├── prompt                    # 提示词生成
├── video                     # 原始视频生成
├── gems                      # Gem模板管理
├── health                    # 健康检查
├── config                    # 配置管理
└── retry                     # 重试失败任务

Global Options:
  --config PATH               # 指定配置文件
  --env FILE                  # 指定 .env 文件
  --verbose / -v              # 详细输出（可叠加 -vv）
  --quiet / -q                # 静默模式
  --json                      # JSON格式输出
  --trace-id ID               # 指定追踪ID
  --version                   # 显示版本
```

### 4.2 CLI 命令定义表

#### CMD01: skill single — 单产品视频生成

```bash
# 基本用法
skill single IMAGE_PATH [OPTIONS]

# 示例
skill single /path/to/product.jpg --description "高端护肤品" --provider seedance
skill single https://example.com/img.jpg --gem gem_001 --duration 8

# 参数定义
Arguments:
  IMAGE_PATH                  产品图片路径（本地或URL）[必需]

Options:
  -d, --description TEXT      产品补充描述
  -g, --gem-id TEXT           指定Gem模板ID
  -p, --provider TEXT         指定视频提供商
  -r, --resolution TEXT       输出分辨率 [默认: 1080p]
  -t, --duration INTEGER      视频时长(秒) [默认: 5]
  --no-audio                  不生成音频
  -o, --output PATH           输出目录 [默认: ./output]
  --json                      JSON格式输出
```

**输出格式（默认）**:
```
🎬 单产品视频生成
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📷 图片: product.jpg
🤖 分析: 美妆护肤 > 面膜 > 中高端 > 年轻女性
🎯 匹配: 高端护肤种草 (gem_002, 匹配度 0.91)
✍️  提示词: A young woman gently applying...
🎥 视频: Seedance 2.0 Pro
💰 花费: $0.023
⏱️  耗时: 67.3秒
✅ 完成: ./output/video_product_abc123.mp4
```

---

#### CMD02: skill batch — 批量视频生成

```bash
# 基本用法
skill batch IMAGE_DIR [OPTIONS]

# 示例
skill batch ./products/ --name "双十一美妆批次" --concurrency 5
skill batch ./images/ --provider kling --resolution 720p --continue-on-error

# 参数定义
Arguments:
  IMAGE_DIR                   产品图片目录 [必需]

Options:
  -n, --name TEXT             批次名称 [默认: 未命名批次]
  -d, --description TEXT      通用产品描述
  -c, --concurrency INTEGER   最大并发数 [默认: 3]
  -p, --provider TEXT         指定视频提供商
  -r, --resolution TEXT       输出分辨率 [默认: 1080p]
  -t, --duration INTEGER      视频时长(秒) [默认: 5]
  --no-audio                  不生成音频
  --no-drive-upload           不上传到Google Drive
  --continue-on-error         单个失败继续处理 [默认: true]
  -o, --output PATH           输出目录 [默认: ./output]
```

**输出格式（默认）**:
```
📦 批量视频生成
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
批次ID:   batch_x7k9m2p
名称:     双十一美妆批次
任务数:   156
并发数:   3
提供商:   Seedance 2.0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
启动处理...

[████████████████████░░░░░░░░░░░░░░░░░░░░] 52.6% (82/156)
✅ 已完成: 78   ❌ 失败: 4   ⏳ 剩余: 74
💰 花费: $1.79  ⏱️  ETA: 4分32秒  📊 吞吐: 0.34/s
```

---

#### CMD03: skill status — 查询任务状态

```bash
# 基本用法
skill status BATCH_ID [OPTIONS]

# 示例
skill status batch_x7k9m2p
skill status batch_x7k9m2p --watch          # 实时刷新
skill status batch_x7k9m2p --json           # JSON输出

# 参数定义
Arguments:
  BATCH_ID                    批次ID [必需]

Options:
  -w, --watch                 实时监控模式（每秒刷新）
  --interval FLOAT            刷新间隔(秒) [默认: 5.0]
  --json                      JSON格式输出
```

---

#### CMD04: skill analyze — 图片分析

```bash
# 基本用法
skill analyze IMAGE_PATH [OPTIONS]

# 示例
skill analyze product.jpg --dimensions product,audience
skill analyze product.jpg --json | jq '.features.category'

# 参数定义
Arguments:
  IMAGE_PATH                  图片路径 [必需]

Options:
  --dimensions TEXT           分析维度 [默认: product,audience,scene,style]
  --platform TEXT             平台偏好
  --json                      JSON格式输出
```

---

#### CMD05: skill match — 智能路由匹配

```bash
# 基本用法
skill match IMAGE_PATH [OPTIONS]
# 或直接使用特征
skill match --features '{"category":"makeup",...}' [OPTIONS]

# 示例
skill match product.jpg --top-n 5

# 参数定义
Options:
  IMAGE_PATH                  图片路径（可选，与--features二选一）
  --features TEXT             JSON格式的特征字典
  -n, --top-n INTEGER         Top N匹配 [默认: 3]
  --min-score FLOAT           最低匹配分 [默认: 0.5]
  --platform TEXT             平台偏好
  --json                      JSON格式输出
```

---

#### CMD06: skill prompt — 提示词生成

```bash
# 基本用法
skill prompt IMAGE_PATH GEM_ID [OPTIONS]

# 示例
skill prompt product.jpg gem_002 --description "高端抗衰老精华"

# 参数定义
Arguments:
  IMAGE_PATH                  图片路径 [必需]
  GEM_ID                      Gem模板ID [必需]

Options:
  -d, --description TEXT      产品描述
  --json                      JSON格式输出
```

---

#### CMD07: skill video — 原始视频生成

```bash
# 基本用法
skill video PROMPT [OPTIONS]

# 示例
skill video "A cat playing piano" --image cat.jpg --provider kling

# 参数定义
Arguments:
  PROMPT                      视频提示词 [必需]

Options:
  -i, --image PATH            参考图片
  -p, --provider TEXT         提供商 [默认: seedance]
  -r, --resolution TEXT       分辨率 [默认: 1080p]
  -t, --duration INTEGER      时长(秒) [默认: 5]
  --no-audio                  不生成音频
  -o, --output PATH           输出路径
  --json                      JSON格式输出
```

---

#### CMD08: skill gems — Gem模板管理

```bash
# 基本用法
skill gems [COMMAND] [OPTIONS]

# 子命令
skill gems list                          # 列出所有Gem
skill gems list --category 促销类         # 按分类筛选
skill gems list --refresh                # 强制刷新
skill gems show GEM_ID                   # 显示Gem详情
skill gems sync                          # 同步Sheets数据

# 参数定义
Commands:
  list                      列出Gem模板
  show                      显示单个Gem详情
  sync                      同步Sheets数据

Options (list):
  --category TEXT           按分类筛选
  --active-only             仅显示启用的 [默认: true]
  --refresh                 强制刷新缓存
  --json                    JSON格式输出
```

---

#### CMD09: skill health — 健康检查

```bash
# 基本用法
skill health [OPTIONS]

# 示例
skill health
skill health --json | jq '.checks.gemini_service.status'

Options:
  --json                    JSON格式输出
```

---

#### CMD10: skill config — 配置管理

```bash
# 基本用法
skill config [COMMAND] [OPTIONS]

# 子命令
skill config show                        # 显示当前配置
skill config show --section video        # 显示视频配置
skill config reload                      # 热重载配置
skill config validate                    # 验证配置

Options:
  --section TEXT            配置分组
  --json                    JSON格式输出
```

---

#### CMD11: skill retry — 重试失败任务

```bash
# 基本用法
skill retry BATCH_ID [OPTIONS]

# 示例
skill retry batch_x7k9m2p

Arguments:
  BATCH_ID                    批次ID [必需]

Options:
  --task-id TEXT              仅重试指定任务
  --json                      JSON格式输出
```

---

### 4.3 CLI 输出格式规范

```python
# CLI 输出接口（内部使用）
class OutputFormatter:
    """CLI 输出格式化器 — 支持人类可读和JSON两种格式"""
    
    def print_success(self, message: str, details: dict | None = None) -> None:
        """打印成功消息"""
        
    def print_error(self, error: ErrorResponse) -> None:
        """打印错误消息"""
        
    def print_table(self, headers: list[str], rows: list[list[str]]) -> None:
        """打印表格"""
        
    def print_progress(self, snapshot: ProgressSnapshot) -> None:
        """打印进度条"""
        
    def print_json(self, data: dict) -> None:
        """打印JSON格式输出"""
        
    def print_health(self, result: HealthCheckResult) -> None:
        """打印健康检查结果"""
```

---

## 5. Python API 接口

### 5.1 公共 API 总览

```python
# 核心公共类 — 从 skill 包导入
from skill import (
    SkillClient,              # 主客户端（门面模式）
    TaskController,           # 任务控制器
    ImageAnalyzer,            # 图片分析器
    SmartRouter,              # 智能路由器
    PromptBuilder,            # 提示词生成器
    VideoGenerator,           # 视频生成器
    ConfigManager,            # 配置管理器
    EventBus,                 # 事件总线
)

# 数据模型
from skill.models import (
    ImageFeatures,            # 图片特征
    MatchResult,              # 匹配结果
    PromptResult,             # 提示词结果
    VideoGenerationResult,    # 视频生成结果
    TaskRecord,               # 任务记录
    BatchTaskSummary,         # 批量任务汇总
    BatchTaskConfig,          # 批量任务配置
    HealthStatus,             # 健康状态
    SkillConfig,              # Skill配置
)

# 枚举
from skill.enums import (
    TaskStatus,               # 任务状态
    VideoProvider,            # 视频提供商
    VideoResolution,          # 视频分辨率
    ConfidenceLevel,          # 置信度等级
    ErrorCategory,            # 错误分类
    EventType,                # 事件类型
)

# 异常
from skill.exceptions import (
    SkillException,           # 基类异常
    ValidationError,          # 验证错误
    AuthError,                # 认证错误
    TaskFailedError,          # 任务失败
    AllProvidersFailedError,  # 全部提供商失败
)
```

### 5.2 SkillClient — 主客户端

```python
class SkillClient:
    """
    Skill 主客户端 — 统一入口门面
    
    封装所有业务模块，提供简化的一体式API。
    所有方法均为异步，支持上下文管理器。
    
    Usage:
        async with SkillClient() as client:
            result = await client.generate_single(image_path="product.jpg")
            progress = await client.get_progress(batch_id="batch_xxx")
    """

    # ═══════════════════════════════════════════
    # 生命周期
    # ═══════════════════════════════════════════

    def __init__(
        self,
        config: SkillConfig | str | None = None,
        event_bus: EventBus | None = None,
    ) -> None:
        """
        初始化 SkillClient
        
        Args:
            config: 配置对象、配置文件路径或None（自动加载）
            event_bus: 事件总线实例（可选，用于事件订阅）
        """

    async def initialize(self) -> None:
        """异步初始化所有依赖模块"""

    async def close(self) -> None:
        """优雅关闭，释放所有资源"""

    async def __aenter__(self) -> "SkillClient": ...
    async def __aexit__(self, exc_type, exc, tb) -> None: ...

    # ═══════════════════════════════════════════
    # 单产品处理
    # ═══════════════════════════════════════════

    async def generate_single(
        self,
        image_path: str,
        user_description: str = "",
        preferred_gem_id: str | None = None,
        preferred_provider: VideoProvider | None = None,
        output_resolution: VideoResolution = VideoResolution.P1080,
        output_duration: int = 5,
        generate_audio: bool = True,
    ) -> TaskRecord:
        """
        为单个产品生成视频（完整流程）
        
        流程: 图片分析 → 智能路由 → 提示词生成 → 视频生成 → 下载
        
        Args:
            image_path: 产品图片路径或URL
            user_description: 产品补充描述
            preferred_gem_id: 指定Gem模板（跳过路由）
            preferred_provider: 指定视频提供商
            output_resolution: 输出分辨率
            output_duration: 视频时长（秒）
            generate_audio: 是否生成音频
            
        Returns:
            TaskRecord: 完整的任务记录
            
        Raises:
            ValidationError: 参数验证失败
            AuthError: 认证失败
            TaskFailedError: 任务执行失败
            AllProvidersFailedError: 所有提供商不可用
        """

    # ═══════════════════════════════════════════
    # 批量处理
    # ═══════════════════════════════════════════

    async def generate_batch(
        self,
        image_dir: str,
        batch_name: str = "未命名批次",
        user_description: str = "",
        config: BatchTaskConfig | None = None,
    ) -> BatchTaskSummary:
        """
        批量生成视频
        
        Args:
            image_dir: 产品图片目录
            batch_name: 批次名称
            user_description: 通用产品描述
            config: 批量任务配置（可选）
            
        Returns:
            BatchTaskSummary: 批次任务汇总
        """

    async def get_progress(self, batch_id: str) -> ProgressSnapshot:
        """
        获取批量任务进度
        
        Args:
            batch_id: 批次ID
            
        Returns:
            ProgressSnapshot: 进度快照
            
        Raises:
            ResourceNotFoundError: 批次不存在
        """

    async def retry_failed(self, batch_id: str) -> int:
        """
        重试批次中的失败任务
        
        Args:
            batch_id: 批次ID
            
        Returns:
            int: 重试的任务数量
        """

    # ═══════════════════════════════════════════
    # 原子操作
    # ═══════════════════════════════════════════

    async def analyze_image(
        self,
        image_path: str,
        dimensions: list[str] | None = None,
        platform_hint: str | None = None,
    ) -> ImageFeatures:
        """
        分析产品图片
        
        Args:
            image_path: 图片路径或URL
            dimensions: 分析维度列表 [product, audience, scene, style]
            platform_hint: 平台偏好
            
        Returns:
            ImageFeatures: 结构化特征向量
        """

    async def match_gem(
        self,
        features: ImageFeatures,
        top_n: int = 3,
        min_score: float = 0.5,
    ) -> MatchResult:
        """
        智能匹配Gem模板
        
        Args:
            features: 图片特征
            top_n: 返回Top N
            min_score: 最低匹配分
            
        Returns:
            MatchResult: 匹配结果
        """

    async def generate_prompt(
        self,
        image_path: str,
        gem_id: str,
        user_description: str = "",
    ) -> PromptResult:
        """
        生成视频提示词
        
        Args:
            image_path: 产品图片
            gem_id: Gem模板ID
            user_description: 产品描述
            
        Returns:
            PromptResult: 提示词结果
        """

    async def generate_video_raw(
        self,
        prompt: str,
        reference_image: str | None = None,
        provider: VideoProvider = VideoProvider.SEEDANCE,
        resolution: VideoResolution = VideoResolution.P1080,
        duration: int = 5,
        generate_audio: bool = True,
    ) -> VideoGenerationResult:
        """
        直接生成视频（原始API）
        
        Args:
            prompt: 视频提示词
            reference_image: 参考图片路径
            provider: 视频提供商
            resolution: 分辨率
            duration: 时长（秒）
            generate_audio: 是否生成音频
            
        Returns:
            VideoGenerationResult: 视频生成结果
        """

    # ═══════════════════════════════════════════
    # 管理操作
    # ═══════════════════════════════════════════

    async def list_gems(
        self,
        category: str | None = None,
        active_only: bool = True,
    ) -> list[GemInfo]:
        """列出Gem模板"""

    async def health_check(self) -> HealthStatus:
        """系统健康检查"""

    async def reload_config(self, section: str | None = None) -> None:
        """热重载配置"""
```

### 5.3 核心数据模型（Pydantic）

```python
class ImageFeatures(BaseModel):
    """图片特征向量 — 4维度分析结果"""
    
    # 产品维度
    category: str = Field(default="", description="产品品类")
    product_type: str = Field(default="", description="细分产品类型")
    price_tier: str = Field(default="", description="价格带")
    brand_tier: str = Field(default="", description="品牌调性")
    selling_points: list[str] = Field(default_factory=list, description="卖点特征")
    
    # 人群维度
    target_gender: str = Field(default="", description="目标性别")
    target_age: str = Field(default="", description="目标年龄段")
    spending_power: str = Field(default="", description="消费能力")
    personas: list[str] = Field(default_factory=list, description="人群画像标签")
    
    # 场景维度
    marketing_scene: str = Field(default="", description="营销场景")
    usage_scenes: list[str] = Field(default_factory=list, description="使用场景")
    target_platform: str = Field(default="", description="目标平台")
    
    # 风格维度
    video_style: str = Field(default="", description="视频风格")
    visual_tone: str = Field(default="", description="画面调性")
    dominant_colors: list[str] = Field(default_factory=list, description="主色调")


class MatchResult(BaseModel):
    """智能路由匹配结果"""
    
    matches: list[MatchItem] = Field(description="匹配列表")
    confidence_level: ConfidenceLevel = Field(description="置信度等级")
    final_score: float = Field(description="最高匹配分")
    top_candidate_id: str | None = Field(default=None, description="Top1 Gem ID")
    top_candidate_name: str | None = Field(default=None, description="Top1 Gem名称")
    reasoning: str = Field(default="", description="推理说明")
    elapsed_ms: int = Field(default=0, description="匹配耗时")


class MatchItem(BaseModel):
    """单个匹配项"""
    
    gem_id: str = Field(description="Gem ID")
    gem_name: str = Field(description="Gem名称")
    score: float = Field(description="匹配分数（0-1）")
    breakdown: dict[str, float] = Field(default_factory=dict, description="维度得分明细")


class PromptResult(BaseModel):
    """提示词生成结果"""
    
    prompt: str = Field(description="生成的视频提示词")
    raw_text: str = Field(default="", description="原始返回文本")
    gem_used: str = Field(default="", description="使用的Gem ID")
    gem_name: str = Field(default="", description="使用的Gem名称")
    model_used: str = Field(default="", description="使用的模型")
    generation_time_ms: int = Field(default=0, description="生成耗时")
    is_valid: bool = Field(default=False, description="是否通过校验")
    validation: PromptValidation = Field(default_factory=PromptValidation, description="校验详情")


class VideoGenerationResult(BaseModel):
    """视频生成结果"""
    
    task_id: str = Field(description="任务ID")
    provider: VideoProvider = Field(description="视频提供商")
    model_version: str = Field(default="", description="模型版本")
    status: str = Field(description="状态: completed/failed")
    video_url: str | None = Field(default=None, description="视频URL")
    video_local_path: str | None = Field(default=None, description="本地路径")
    cost_usd: float = Field(default=0.0, description="花费（美元）")
    generation_time_sec: float = Field(default=0.0, description="生成耗时")
    error_message: str | None = Field(default=None, description="错误信息")


class TaskRecord(BaseModel):
    """完整任务记录"""
    
    task_id: str = Field(description="任务ID")
    batch_id: str = Field(default="", description="批次ID")
    status: TaskStatus = Field(description="任务状态")
    
    # 输入
    image_path: str = Field(default="", description="图片路径")
    user_description: str = Field(default="", description="用户描述")
    
    # 分析结果
    product_category: str | None = Field(default=None, description="产品品类")
    
    # 路由结果
    matched_gem_id: str | None = Field(default=None, description="匹配Gem ID")
    matched_gem_name: str | None = Field(default=None, description="匹配Gem名称")
    match_score: float | None = Field(default=None, description="匹配分数")
    
    # 提示词
    generated_prompt: str | None = Field(default=None, description="生成的提示词")
    
    # 视频
    video_provider: str | None = Field(default=None, description="视频提供商")
    video_url: str | None = Field(default=None, description="视频URL")
    video_local_path: str | None = Field(default=None, description="本地路径")
    cost_usd: float = Field(default=0.0, description="花费")
    generation_time_sec: float = Field(default=0.0, description="生成耗时")
    
    # 错误
    error_message: str | None = Field(default=None, description="错误信息")
    error_code: str | None = Field(default=None, description="错误码")
    
    # 时间
    created_at: datetime = Field(default_factory=datetime.utcnow)
    started_at: datetime | None = Field(default=None)
    completed_at: datetime | None = Field(default=None)


class BatchTaskConfig(BaseModel):
    """批量任务配置"""
    
    max_concurrency: int = Field(default=3, ge=1, le=10, description="最大并发数")
    continue_on_error: bool = Field(default=True, description="错误继续")
    output_resolution: VideoResolution = Field(default=VideoResolution.P1080)
    output_duration: int = Field(default=5, ge=4, le=16)
    generate_audio: bool = Field(default=True)
    preferred_provider: VideoProvider | None = Field(default=None)
    upload_to_drive: bool = Field(default=True)
    max_retries: int = Field(default=3, ge=0, le=10)
    global_timeout_sec: int = Field(default=3600, ge=60)


class BatchTaskSummary(BaseModel):
    """批量任务汇总"""
    
    batch_id: str = Field(description="批次ID")
    batch_name: str = Field(default="", description="批次名称")
    total_tasks: int = Field(description="总任务数")
    completed_count: int = Field(default=0, description="完成数")
    failed_count: int = Field(default=0, description="失败数")
    progress_percent: float = Field(default=0.0, description="进度百分比")
    total_cost_usd: float = Field(default=0.0, description="总花费")
    status: str = Field(default="running", description="批次状态")


class ProgressSnapshot(BaseModel):
    """进度快照"""
    
    batch_id: str = Field(description="批次ID")
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    total: int = Field(description="总数")
    by_status: dict[str, int] = Field(default_factory=dict, description="各状态计数")
    completed: int = Field(default=0)
    failed: int = Field(default=0)
    in_progress: int = Field(default=0)
    percent: float = Field(default=0.0)
    eta_seconds: float | None = Field(default=None, description="预计剩余时间")
    throughput: float | None = Field(default=None, description="吞吐量")
    is_complete: bool = Field(default=False)


class HealthStatus(BaseModel):
    """系统健康状态"""
    
    status: str = Field(description="整体状态: healthy/degraded/unhealthy")
    version: str = Field(default="", description="版本号")
    checks: dict[str, HealthCheck] = Field(default_factory=dict, description="组件检查")
    latency_ms: int = Field(default=0, description="检查耗时")


class HealthCheck(BaseModel):
    """单个组件健康检查"""
    
    status: str = Field(description="状态: healthy/degraded/unhealthy")
    latency_ms: int = Field(default=0)
    message: str = Field(default="")


class GemInfo(BaseModel):
    """Gem模板信息"""
    
    gem_id: str = Field(description="唯一标识")
    name: str = Field(description="显示名称")
    category: str = Field(default="", description="分类")
    description: str = Field(default="", description="描述")
    tags: list[str] = Field(default_factory=list, description="标签")
    is_active: bool = Field(default=True, description="是否启用")
    usage_count: int = Field(default=0, description="使用次数")
    success_rate: float = Field(default=1.0, description="成功率")
    created_at: datetime | None = Field(default=None)


class SkillConfig(BaseModel):
    """Skill 总配置"""
    
    # Gemini 配置
    gemini_1psid: str = Field(default="", description="__Secure-1PSID Cookie")
    gemini_1psidts: str = Field(default="", description="__Secure-1PSIDTS Cookie")
    gemini_proxy: str | None = Field(default=None)
    gemini_timeout: int = Field(default=120)
    
    # Workspace 配置
    workspace_sa_file: str = Field(default="", description="Service Account JSON路径")
    workspace_spreadsheet_id: str = Field(default="", description="Sheets表格ID")
    
    # 视频提供商配置
    video_providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    
    # 路由配置
    router_match_weights: dict[str, float] = Field(default_factory=dict)
    router_min_confidence: float = Field(default=0.5)
    
    # 任务配置
    task_max_concurrency: int = Field(default=3)
    task_timeout_sec: int = Field(default=600)
    task_max_retries: int = Field(default=3)
    
    # 监控配置
    metrics_enabled: bool = Field(default=True)
    log_level: str = Field(default="INFO")
```

---

## 6. 配置接口

### 6.1 配置分层架构

```
┌─────────────────────────────────────────────────────────────┐
│                      配置来源优先级                           │
│                      （高 → 低）                              │
├─────────────────────────────────────────────────────────────┤
│ 1. 环境变量       SKILL_VIDEO_MAX_CONCURRENCY=5              │
│ 2. .env 文件      项目根目录 .env                            │
│ 3. config.yaml    项目根目录 config.yaml                     │
│ 4. 默认值         Pydantic Field(default=...)                │
└─────────────────────────────────────────────────────────────┘
规则: 高优先级覆盖低优先级，合并而非替换
```

### 6.2 配置文件格式（YAML）

```yaml
# config.yaml — 带货视频批量生产 Skill 配置文件

# ═══════════════════════════════════════════
# Gemini 配置
# ═════════════════════════════════════════==
gemini:
  # Cookie 认证（优先从环境变量读取）
  secure_1psid: ""           # 优先: GEMINI_1PSID 环境变量
  secure_1psidts: ""         # 优先: GEMINI_1PSIDTS 环境变量
  proxy: null                # 代理地址，如 http://127.0.0.1:7890
  timeout: 120               # 请求超时（秒）
  max_retries: 3             # 最大重试次数
  
  # Sheets 同步配置
  sheets:
    spreadsheet_id: ""       # Gem配置表ID，优先: GEMINI_SHEETS_ID
    credentials_path: ""     # SA JSON路径，优先: GEMINI_SHEETS_CREDENTIALS
    sync_interval_sec: 300   # 同步间隔（秒）
    enabled: true            # 是否启用Sheets同步

# ═══════════════════════════════════════════
# Workspace 配置
# ═══════════════════════════════════════════
workspace:
  auth_method: service_account    # 认证方式
  credentials_file: ""            # Service Account JSON路径
  project_id: ""                  # GCP项目ID
  gws_path: "gws"                 # gws CLI路径
  gws_timeout: 300                # gws命令超时（秒）
  
  # 速率限制
  rate_limit:
    sheets_rpm: 300               # Sheets API 每分钟请求数
    drive_rps: 10                 # Drive API 每秒请求数
    gmail_rps: 25                 # Gmail API 每秒配额

# ═══════════════════════════════════════════
# 视频提供商配置
# ═══════════════════════════════════════════
video:
  # 各提供商API Key（优先从环境变量读取）
  providers:
    seedance:
      api_keys: []                # 优先: SEEDANCE_API_KEYS 环境变量（逗号分隔）
      base_url: "https://api.ark.qq.com"
      default_model: "seedance_2_0_standard"
      
    kling:
      api_keys: []                # 优先: KLING_API_KEYS 环境变量
      base_url: "https://api.klingai.com"
      default_model: "kling_3_0"
      
    vidu:
      api_keys: []                # 优先: VIDU_API_KEYS 环境变量
      base_url: "https://api.vidu.com"
      
    veo3:
      api_keys: []                # 优先: VEO3_API_KEYS 环境变量
      base_url: "https://us-central1-aiplatform.googleapis.com"
  
  # 默认视频参数
  defaults:
    resolution: "1080p"           # 默认分辨率
    duration: 5                   # 默认时长（秒）
    generate_audio: true          # 默认生成音频

# ═══════════════════════════════════════════
# 智能路由配置
# ═══════════════════════════════════════════
router:
  # 匹配权重（总和应为1.0）
  match_weights:
    category: 0.30                # 产品品类权重
    marketing_scene: 0.20         # 营销场景权重
    target_audience: 0.15         # 目标人群权重
    price_tier: 0.10              # 价格带权重
    video_style: 0.10             # 视频风格权重
    historical_effect: 0.10       # 历史效果权重
    other: 0.05                   # 其他权重
  
  # 置信度阈值
  min_confidence_high: 0.85       # HIGH置信度阈值
  min_confidence_medium: 0.70     # MEDIUM置信度阈值
  
  # 匹配策略
  default_strategy: "standard"    # 默认策略: standard/fast/semantic/ensemble

# ═══════════════════════════════════════════
# 任务调度配置
# ═══════════════════════════════════════════
task:
  max_concurrency: 3              # 全局最大并发数
  timeout_sec: 600                # 单个任务超时（秒）
  max_retries: 3                  # 最大重试次数
  retry_base_delay: 2.0           # 重试基础延迟（秒）
  continue_on_error: true         # 批量任务错误继续
  
  # 各阶段超时
  timeouts:
    analyzing: 120                # 图片分析超时
    routing: 60                   # 智能路由超时
    prompt_generating: 180        # 提示词生成超时
    video_generating: 600         # 视频生成超时

# ═══════════════════════════════════════════
# 监控与日志配置
# ═══════════════════════════════════════════
monitoring:
  log_level: "INFO"               # 日志级别: DEBUG/INFO/WARNING/ERROR/CRITICAL
  log_format: "json"              # 日志格式: json/text
  metrics_enabled: true           # 是否启用Prometheus指标
  metrics_port: 9090              # 指标暴露端口
  trace_enabled: true             # 是否启用全链路追踪
  
  # 通知配置
  notification:
    enabled: true
    email: ""                     # 通知邮箱
    on_batch_complete: true       # 批次完成通知
    on_error: true                # 错误通知
```

### 6.3 环境变量映射表

| 环境变量 | 对应配置路径 | 类型 | 默认值 | 说明 |
|----------|-------------|------|--------|------|
| `GEMINI_1PSID` | `gemini.secure_1psid` | `str` | `""` | 主Cookie |
| `GEMINI_1PSIDTS` | `gemini.secure_1psidts` | `str` | `""` | 辅助Cookie |
| `GEMINI_PROXY` | `gemini.proxy` | `str` | `null` | 代理地址 |
| `GEMINI_SHEETS_ID` | `gemini.sheets.spreadsheet_id` | `str` | `""` | Gem配置表ID |
| `GEMINI_SHEETS_CREDENTIALS` | `gemini.sheets.credentials_path` | `str` | `""` | SA凭证路径 |
| `WORKSPACE_SA_FILE` | `workspace.credentials_file` | `str` | `""` | Workspace SA文件 |
| `WORKSPACE_PROJECT_ID` | `workspace.project_id` | `str` | `""` | GCP项目ID |
| `SEEDANCE_API_KEYS` | `video.providers.seedance.api_keys` | `list` | `[]` | 即梦API Keys（逗号分隔） |
| `KLING_API_KEYS` | `video.providers.kling.api_keys` | `list` | `[]` | 可灵API Keys |
| `VIDU_API_KEYS` | `video.providers.vidu.api_keys` | `list` | `[]` | Vidu API Keys |
| `VEO3_API_KEYS` | `video.providers.veo3.api_keys` | `list` | `[]` | Veo3 API Keys |
| `SKILL_VIDEO_CONCURRENCY` | `task.max_concurrency` | `int` | `3` | 最大并发数 |
| `SKILL_VIDEO_TIMEOUT` | `task.timeout_sec` | `int` | `600` | 任务超时 |
| `SKILL_LOG_LEVEL` | `monitoring.log_level` | `str` | `"INFO"` | 日志级别 |
| `SKILL_METRICS_PORT` | `monitoring.metrics_port` | `int` | `9090` | 指标端口 |
| `SKILL_CONFIG_PATH` | — | `str` | `"./config.yaml"` | 配置文件路径 |
| `SKILL_ENV_FILE` | — | `str` | `"./.env"` | .env文件路径 |

### 6.4 配置热更新接口

```python
class ConfigManager:
    """
    配置管理器 — 支持热更新和变更监听
    
    配置来源优先级: 环境变量 > .env > config.yaml > 默认值
    """

    def __init__(
        self,
        config_path: str | None = None,
        env_file: str | None = None,
    ) -> None:
        """
        初始化配置管理器
        
        Args:
            config_path: YAML配置文件路径（默认从 SKILL_CONFIG_PATH 或 ./config.yaml）
            env_file: .env文件路径（默认从 SKILL_ENV_FILE 或 ./.env）
        """

    # ── 读取接口 ──

    def get(self, key: str, default: Any = None) -> Any:
        """
        获取配置项（支持点号路径）
        
        Args:
            key: 配置键，如 "video.providers.seedance.base_url"
            default: 默认值
            
        Returns:
            配置值
            
        Example:
            base_url = config.get("video.providers.seedance.base_url")
        """

    def get_section(self, section: str) -> dict[str, Any]:
        """
        获取配置分组
        
        Args:
            section: 分组名，如 "gemini", "video", "task"
            
        Returns:
            该分组下所有配置项
        """

    def get_typed(self, key: str, type_: type[T], default: T | None = None) -> T:
        """
        获取指定类型的配置值
        
        Args:
            key: 配置键
            type_: 期望类型
            default: 默认值
        """

    # ── 热更新接口 ──

    async def reload(self, section: str | None = None) -> ReloadResult:
        """
        热重载配置
        
        重新读取环境变量和配置文件，合并后更新内存配置。
        触发配置变更事件。
        
        Args:
            section: 指定重载的分组（None则重载全部）
            
        Returns:
            ReloadResult: 重载结果（变更项列表）
            
        Example:
            result = await config.reload("video")
            # ReloadResult(changed=["video.providers.seedance.timeout"], errors=[])
        """

    def watch(self, key: str, callback: ConfigChangeCallback) -> WatchHandle:
        """
        监听配置变更
        
        当指定配置项变更时触发回调。
        
        Args:
            key: 配置键（支持通配符，如 "video.*.api_keys"）
            callback: 回调函数 (old_value, new_value) -> None
            
        Returns:
            WatchHandle: 监听句柄（用于取消监听）
            
        Example:
            handle = config.watch("task.max_concurrency", on_concurrency_change)
            handle.cancel()  # 取消监听
        """

    def watch_section(
        self,
        section: str,
        callback: ConfigChangeCallback,
    ) -> WatchHandle:
        """监听整个配置分组变更"""

    async def start_auto_reload(self, interval_sec: float = 60.0) -> None:
        """
        启动自动重载循环（后台任务）
        
        定期检查配置文件修改时间，变更时自动重载。
        
        Args:
            interval_sec: 检查间隔（秒）
        """

    async def stop_auto_reload(self) -> None:
        """停止自动重载循环"""

    # ── 验证接口 ──

    def validate(self) -> ValidationResult:
        """
        验证配置完整性
        
        检查所有必需配置项是否已设置，格式是否正确。
        
        Returns:
            ValidationResult: 验证结果（错误列表）
        """

    def dump(self, mask_secrets: bool = True) -> dict[str, Any]:
        """
        导出当前配置
        
        Args:
            mask_secrets: 是否脱敏敏感值
            
        Returns:
            配置字典
        """


# 配置变更回调类型
ConfigChangeCallback = Callable[[Any, Any], Awaitable[None] | None]
# old_value, new_value -> None


class ReloadResult(BaseModel):
    """重载结果"""
    changed: list[str] = Field(default_factory=list, description="变更的配置键列表")
    errors: list[str] = Field(default_factory=list, description="错误信息列表")
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class ValidationResult(BaseModel):
    """验证结果"""
    is_valid: bool = Field(description="是否有效")
    errors: list[ValidationError] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class WatchHandle:
    """配置监听句柄"""
    
    def cancel(self) -> None:
        """取消监听"""
```

---

## 7. 事件系统

### 7.1 事件系统架构

```
┌─────────────────────────────────────────────────────────────┐
│                       事件总线 (EventBus)                      │
│                                                               │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐  │
│  │   Publisher   │───▶│   EventBus   │───▶│  Subscriber  │  │
│  │              │    │              │    │              │  │
│  │ TaskController│   │  asyncio.Queue│   │ ProgressTracker│  │
│  │ BatchProcessor│   │  + dict[EventType, list[Handler]]  │  │
│  │ VideoGenerator│   │              │    │ MetricsCollector│  │
│  │ ConfigManager │   │              │    │ Logger        │  │
│  └──────────────┘    └──────────────┘    │ WorkspaceClient│  │
│                                           └──────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

### 7.2 事件类型枚举

```python
class EventType(str, Enum):
    """
    事件类型枚举 — 全系统事件定义
    
    命名规范: domain_action_detail
    示例: task_status_changed, video_generation_completed
    """
    
    # ═══════════════════════════════════════════
    # 任务生命周期事件
    # ═══════════════════════════════════════════
    
    # 单任务事件
    TASK_CREATED = "task_created"                          # 任务创建
    TASK_STARTED = "task_started"                          # 任务开始
    TASK_STATUS_CHANGED = "task_status_changed"            # 状态变更
    TASK_COMPLETED = "task_completed"                      # 任务完成
    TASK_FAILED = "task_failed"                            # 任务失败
    TASK_RETRYING = "task_retrying"                        # 任务重试
    TASK_CANCELLED = "task_cancelled"                      # 任务取消
    
    # 批次事件
    BATCH_CREATED = "batch_created"                        # 批次创建
    BATCH_STARTED = "batch_started"                        # 批次开始
    BATCH_PAUSED = "batch_paused"                          # 批次暂停
    BATCH_RESUMED = "batch_resumed"                        # 批次恢复
    BATCH_PROGRESS = "batch_progress"                      # 进度更新
    BATCH_COMPLETED = "batch_completed"                    # 批次完成
    BATCH_FAILED = "batch_failed"                          # 批次失败
    
    # ═══════════════════════════════════════════
    # 处理阶段事件
    # ═══════════════════════════════════════════
    
    # 图片分析阶段
    ANALYZING_STARTED = "analyzing_started"                # 分析开始
    ANALYZING_COMPLETED = "analyzing_completed"            # 分析完成
    ANALYZING_CACHE_HIT = "analyzing_cache_hit"            # 缓存命中
    ANALYZING_FAILED = "analyzing_failed"                  # 分析失败
    
    # 智能路由阶段
    ROUTING_STARTED = "routing_started"                    # 路由开始
    ROUTING_COMPLETED = "routing_completed"                # 路由完成
    ROUTING_LOW_CONFIDENCE = "routing_low_confidence"      # 低置信度匹配
    
    # 提示词生成阶段
    PROMPT_GENERATION_STARTED = "prompt_generation_started"
    PROMPT_GENERATION_COMPLETED = "prompt_generation_completed"
    PROMPT_GENERATION_FAILED = "prompt_generation_failed"
    
    # 视频生成阶段
    VIDEO_SUBMITTED = "video_submitted"                    # 视频已提交
    VIDEO_STATUS_CHANGED = "video_status_changed"          # 视频状态变更
    VIDEO_COMPLETED = "video_completed"                    # 视频完成
    VIDEO_DOWNLOADED = "video_downloaded"                  # 视频已下载
    VIDEO_FAILED = "video_failed"                          # 视频失败
    VIDEO_PROVIDER_FALLBACK = "video_provider_fallback"    # 提供商降级
    
    # ═══════════════════════════════════════════
    # 系统事件
    # ═══════════════════════════════════════════
    
    # 错误与降级
    ERROR_OCCURRED = "error_occurred"                      # 错误发生
    PROVIDER_FAILED = "provider_failed"                    # 提供商失败
    FAILOVER_TRIGGERED = "failover_triggered"              # 降级触发
    CIRCUIT_BREAKER_OPENED = "circuit_breaker_opened"      # 熔断器打开
    CIRCUIT_BREAKER_CLOSED = "circuit_breaker_closed"      # 熔断器闭合
    
    # 配置事件
    CONFIG_RELOADED = "config_reloaded"                    # 配置重载
    CONFIG_CHANGED = "config_changed"                      # 配置变更
    
    # 健康事件
    HEALTH_CHECK = "health_check"                          # 健康检查
    PROVIDER_HEALTH_CHANGED = "provider_health_changed"    # 提供商健康变更
    
    # Workspace事件
    SHEETS_SYNCED = "sheets_synced"                        # Sheets同步
    DRIVE_UPLOADED = "drive_uploaded"                      # Drive上传
    NOTIFICATION_SENT = "notification_sent"                # 通知发送
```

### 7.3 事件数据结构

```python
class SkillEvent(BaseModel):
    """
    标准化事件 — 所有事件的统一格式
    
    所有事件必须携带 trace_id 实现全链路追踪。
    事件数据放在 payload 中，根据事件类型有不同的结构。
    """
    
    event_type: EventType = Field(description="事件类型")
    trace_id: str = Field(description="追踪ID（与关联任务相同）")
    timestamp: datetime = Field(default_factory=datetime.utcnow, description="事件发生时间")
    source: str = Field(description="事件来源模块")
    
    # 业务数据（根据事件类型变化）
    payload: dict[str, Any] = Field(default_factory=dict, description="事件数据")
    
    # 上下文
    task_id: str | None = Field(default=None, description="关联任务ID")
    batch_id: str | None = Field(default=None, description="关联批次ID")
    provider: str | None = Field(default=None, description="关联提供商")


# ═══════════════════════════════════════════
# 各事件类型的 Payload 定义
# ═══════════════════════════════════════════

class TaskStatusChangedPayload(BaseModel):
    """TASK_STATUS_CHANGED 事件数据"""
    task_id: str
    old_status: TaskStatus
    new_status: TaskStatus
    reason: str = ""


class BatchProgressPayload(BaseModel):
    """BATCH_PROGRESS 事件数据"""
    batch_id: str
    total: int
    completed: int
    failed: int
    progress_percent: float
    eta_seconds: float | None
    throughput: float | None


class VideoCompletedPayload(BaseModel):
    """VIDEO_COMPLETED 事件数据"""
    task_id: str
    video_url: str
    video_local_path: str
    provider: str
    cost_usd: float
    generation_time_sec: float


class ErrorPayload(BaseModel):
    """ERROR_OCCURRED 事件数据"""
    error_code: str
    message: str
    retryable: bool
    retry_after_sec: int = 0


class ConfigChangedPayload(BaseModel):
    """CONFIG_CHANGED 事件数据"""
    changed_keys: list[str]
    old_values: dict[str, Any]
    new_values: dict[str, Any]
```

### 7.4 事件处理器注册

```python
class EventBus:
    """
    事件总线 — 发布-订阅模式
    
    支持同步和异步处理器，支持一次性监听和条件过滤。
    """

    def __init__(self, max_queue_size: int = 10000) -> None:
        """
        初始化事件总线
        
        Args:
            max_queue_size: 事件队列最大长度
        """

    # ── 发布接口 ──

    async def publish(self, event: SkillEvent) -> None:
        """
        发布事件
        
        Args:
            event: 事件对象
        """

    async def publish_typed(
        self,
        event_type: EventType,
        payload: dict[str, Any],
        trace_id: str,
        task_id: str | None = None,
        batch_id: str | None = None,
        source: str = "",
    ) -> None:
        """
        便捷发布方法
        
        自动构建 SkillEvent 并发布。
        """

    # ── 订阅接口 ──

    def subscribe(
        self,
        event_type: EventType,
        handler: EventHandler,
        filter_fn: EventFilter | None = None,
    ) -> Subscription:
        """
        订阅事件
        
        Args:
            event_type: 事件类型
            handler: 处理器函数 (event: SkillEvent) -> None
            filter_fn: 过滤函数（可选）
            
        Returns:
            Subscription: 订阅对象（用于取消订阅）
            
        Example:
            sub = event_bus.subscribe(
                EventType.BATCH_PROGRESS,
                on_progress,
                filter_fn=lambda e: e.payload.get("batch_id") == "my_batch"
            )
            sub.cancel()  # 取消订阅
        """

    def subscribe_many(
        self,
        event_types: list[EventType],
        handler: EventHandler,
    ) -> list[Subscription]:
        """批量订阅多个事件类型"""

    def once(
        self,
        event_type: EventType,
        handler: EventHandler,
        timeout: float | None = None,
    ) -> asyncio.Future:
        """
        一次性监听（只处理一次后自动取消）
        
        Args:
            event_type: 事件类型
            handler: 处理器
            timeout: 超时（秒），None表示不限
            
        Returns:
            Future: 可等待的Future对象
        """

    def wait_for(
        self,
        event_type: EventType,
        condition: Callable[[SkillEvent], bool] | None = None,
        timeout: float | None = None,
    ) -> asyncio.Future[SkillEvent]:
        """
        等待特定事件
        
        Args:
            event_type: 等待的事件类型
            condition: 条件函数
            timeout: 超时（秒）
            
        Returns:
            Future[SkillEvent]: 事件Future
            
        Example:
            event = await event_bus.wait_for(
                EventType.TASK_COMPLETED,
                condition=lambda e: e.task_id == "task_001",
                timeout=120.0
            )
        """

    # ── 管理接口 ──

    def get_subscriber_count(self, event_type: EventType) -> int:
        """获取某事件的订阅者数量"""

    def get_queue_size(self) -> int:
        """获取事件队列当前大小"""

    async def shutdown(self, drain: bool = True, timeout: float = 30.0) -> None:
        """
        优雅关闭
        
        Args:
            drain: 是否排空队列中已有事件
            timeout: 排空超时（秒）
        """


# 类型别名
EventHandler = Callable[[SkillEvent], Awaitable[None] | None]
EventFilter = Callable[[SkillEvent], bool]


class Subscription:
    """订阅对象 — 用于管理订阅生命周期"""
    
    event_type: EventType
    handler_id: str
    
    def cancel(self) -> None:
        """取消订阅"""
```

### 7.5 内置事件处理器

```python
# 系统内置的标准事件处理器

class ProgressTrackerHandler:
    """进度追踪处理器 — 更新内存进度状态"""
    
    async def __call__(self, event: SkillEvent) -> None:
        if event.event_type == EventType.TASK_STATUS_CHANGED:
            # 更新状态计数器
            ...
        elif event.event_type == EventType.BATCH_PROGRESS:
            # 更新进度百分比
            ...

class SheetsSyncHandler:
    """Sheets同步处理器 — 同步任务状态到Google Sheets"""
    
    async def __call__(self, event: SkillEvent) -> None:
        if event.event_type in (EventType.TASK_STATUS_CHANGED, EventType.TASK_COMPLETED):
            # 写入task-queue表
            ...
        elif event.event_type == EventType.BATCH_PROGRESS:
            # 更新batch-summary表
            ...

class MetricsHandler:
    """指标收集处理器 — 更新Prometheus指标"""
    
    async def __call__(self, event: SkillEvent) -> None:
        # 更新计数器/仪表盘
        ...

class LoggingHandler:
    """日志处理器 — 记录结构化日志"""
    
    async def __call__(self, event: SkillEvent) -> None:
        # 根据事件级别记录日志
        ...

class NotificationHandler:
    """通知处理器 — 发送邮件通知"""
    
    async def __call__(self, event: SkillEvent) -> None:
        if event.event_type == EventType.BATCH_COMPLETED:
            # 发送批次完成通知
            ...
        elif event.event_type == EventType.BATCH_FAILED:
            # 发送错误通知
            ...
```

---

## 8. 完整代码示例

### 8.1 MCP Server 实现示例

```python
"""
server.py — MCP Server 实现
使用 FastMCP 框架暴露 10 个工具
"""

from fastmcp import FastMCP
from skill import SkillClient, EventType

# 创建 MCP Server
mcp = FastMCP("带货视频批量生产 Skill")

# 全局 SkillClient 实例
_client: SkillClient | None = None


async def get_client() -> SkillClient:
    """获取或初始化 SkillClient"""
    global _client
    if _client is None:
        _client = SkillClient()
        await _client.initialize()
    return _client


# ═══════════════════════════════════════════
# T01: 单产品视频生成
# ═══════════════════════════════════════════

@mcp.tool()
async def generate_video_single(
    image_path: str,
    user_description: str = "",
    preferred_gem_id: str | None = None,
    preferred_provider: str | None = None,
    output_resolution: str = "1080p",
    output_duration: int = 5,
    generate_audio: bool = True,
) -> dict:
    """
    为单个产品图片生成带货视频
    
    完整流程：图片分析 → 智能路由 → 提示词生成 → 视频生成
    
    Args:
        image_path: 产品图片路径（本地路径或URL）
        user_description: 产品补充描述
        preferred_gem_id: 指定Gem模板ID（可选）
        preferred_provider: 指定视频提供商（可选，如 seedance/kling/vidu）
        output_resolution: 输出分辨率（540p/720p/1080p/4k）
        output_duration: 视频时长（秒），默认5秒
        generate_audio: 是否生成音频，默认True
    
    Returns:
        包含任务记录的字典
    """
    client = await get_client()
    result = await client.generate_single(
        image_path=image_path,
        user_description=user_description,
        preferred_gem_id=preferred_gem_id,
        preferred_provider=preferred_provider,
        output_resolution=output_resolution,
        output_duration=output_duration,
        generate_audio=generate_audio,
    )
    return result.model_dump()


# ═══════════════════════════════════════════
# T02: 批量视频生成
# ═══════════════════════════════════════════

@mcp.tool()
async def generate_video_batch(
    image_dir: str,
    batch_name: str = "未命名批次",
    user_description: str = "",
    max_concurrency: int = 3,
    preferred_provider: str | None = None,
    output_resolution: str = "1080p",
    output_duration: int = 5,
    continue_on_error: bool = True,
    upload_to_drive: bool = True,
) -> dict:
    """
    为多个产品图片批量生成带货视频
    
    Args:
        image_dir: 产品图片目录路径
        batch_name: 批次名称
        user_description: 通用产品描述
        max_concurrency: 最大并发数（1-10）
        preferred_provider: 指定视频提供商
        output_resolution: 输出分辨率
        output_duration: 视频时长（秒）
        continue_on_error: 单个失败是否继续
        upload_to_drive: 完成后上传Google Drive
    
    Returns:
        批次信息字典（batch_id, total_tasks, status）
    """
    from skill.models import BatchTaskConfig
    
    client = await get_client()
    config = BatchTaskConfig(
        max_concurrency=max_concurrency,
        continue_on_error=continue_on_error,
        output_resolution=output_resolution,
        output_duration=output_duration,
        preferred_provider=preferred_provider,
        upload_to_drive=upload_to_drive,
    )
    result = await client.generate_batch(
        image_dir=image_dir,
        batch_name=batch_name,
        user_description=user_description,
        config=config,
    )
    return {
        "batch_id": result.batch_id,
        "total_tasks": result.total_tasks,
        "status": result.status,
        "message": f"批次已创建，共 {result.total_tasks} 个任务",
    }


# ═══════════════════════════════════════════
# T03: 查询批量任务进度
# ═══════════════════════════════════════════

@mcp.tool()
async def get_batch_progress(batch_id: str) -> dict:
    """
    查询指定批次的处理进度
    
    Args:
        batch_id: 批次ID
    
    Returns:
        进度快照字典
    """
    client = await get_client()
    progress = await client.get_progress(batch_id)
    return progress.model_dump()


# ═══════════════════════════════════════════
# T04: 图片深度分析
# ═══════════════════════════════════════════

@mcp.tool()
async def analyze_image(
    image_path: str,
    dimensions: list[str] | None = None,
    platform_hint: str | None = None,
) -> dict:
    """
    对产品图片进行4维度深度分析
    
    Args:
        image_path: 图片路径或URL
        dimensions: 分析维度列表（默认: product, audience, scene, style）
        platform_hint: 平台偏好（如 douyin, xiaohongshu）
    
    Returns:
        分析结果字典（features, confidence_score, reliability）
    """
    client = await get_client()
    features = await client.analyze_image(
        image_path=image_path,
        dimensions=dimensions or ["product", "audience", "scene", "style"],
        platform_hint=platform_hint,
    )
    return {
        "features": features.model_dump(),
        "confidence_score": 0.0,  # 由分析器填充
        "reliability": "moderate",
    }


# ═══════════════════════════════════════════
# T05: 智能路由匹配
# ═══════════════════════════════════════════

@mcp.tool()
async def match_gem_template(
    features: dict,
    top_n: int = 3,
    min_score: float = 0.5,
    platform_hint: str | None = None,
) -> dict:
    """
    根据图片分析结果匹配最佳Gem模板
    
    Args:
        features: 图片特征字典（analyze_image的返回值）
        top_n: 返回Top N匹配，默认3
        min_score: 最低匹配分（0-1），默认0.5
        platform_hint: 平台偏好
    
    Returns:
        匹配结果字典（matches, confidence_level, reasoning）
    """
    from skill.models import ImageFeatures
    
    client = await get_client()
    image_features = ImageFeatures(**features)
    result = await client.match_gem(
        features=image_features,
        top_n=top_n,
        min_score=min_score,
    )
    return result.model_dump()


# ═══════════════════════════════════════════
# T06: 提示词生成
# ═══════════════════════════════════════════

@mcp.tool()
async def generate_prompt(
    image_path: str,
    gem_id: str,
    user_description: str = "",
) -> dict:
    """
    调用Gem生成视频提示词
    
    Args:
        image_path: 产品图片路径
        gem_id: Gem模板ID
        user_description: 产品补充描述
    
    Returns:
        提示词结果字典
    """
    client = await get_client()
    result = await client.generate_prompt(
        image_path=image_path,
        gem_id=gem_id,
        user_description=user_description,
    )
    return result.model_dump()


# ═══════════════════════════════════════════
# T07: 原始视频生成
# ═══════════════════════════════════════════

@mcp.tool()
async def generate_video_raw(
    prompt: str,
    reference_image: str | None = None,
    provider: str = "seedance",
    resolution: str = "1080p",
    duration: int = 5,
    generate_audio: bool = True,
) -> dict:
    """
    直接调用视频生成API（不包含分析和提示词生成）
    
    Args:
        prompt: 视频生成提示词
        reference_image: 参考图片路径（可选）
        provider: 视频提供商（默认 seedance）
        resolution: 分辨率（默认 1080p）
        duration: 时长（秒，默认5）
        generate_audio: 是否生成音频
    
    Returns:
        视频生成结果字典
    """
    from skill.enums import VideoProvider, VideoResolution
    
    client = await get_client()
    result = await client.generate_video_raw(
        prompt=prompt,
        reference_image=reference_image,
        provider=VideoProvider(provider),
        resolution=VideoResolution(resolution),
        duration=duration,
        generate_audio=generate_audio,
    )
    return result.model_dump()


# ═══════════════════════════════════════════
# T08: 列出Gem模板
# ═══════════════════════════════════════════

@mcp.tool()
async def list_gems(
    category: str | None = None,
    active_only: bool = True,
) -> dict:
    """
    列出所有可用的Gem模板
    
    Args:
        category: 按分类筛选（可选）
        active_only: 仅返回启用的Gem（默认True）
    
    Returns:
        Gem列表字典（gems, total）
    """
    client = await get_client()
    gems = await client.list_gems(
        category=category,
        active_only=active_only,
    )
    return {
        "gems": [g.model_dump() for g in gems],
        "total": len(gems),
    }


# ═══════════════════════════════════════════
# T09: 系统健康检查
# ═══════════════════════════════════════════

@mcp.tool()
async def health_check() -> dict:
    """
    检查系统各组件健康状态
    
    Returns:
        健康状态字典（status, checks, latency_ms）
    """
    client = await get_client()
    result = await client.health_check()
    return result.model_dump()


# ═══════════════════════════════════════════
# T10: 获取配置
# ═══════════════════════════════════════════

@mcp.tool()
async def get_config(section: str | None = None) -> dict:
    """
    获取当前系统配置（脱敏）
    
    Args:
        section: 配置分组（如 gemini/video/router/task）
    
    Returns:
        配置字典
    """
    from skill.config import ConfigManager
    
    config = ConfigManager()
    if section:
        return config.get_section(section)
    return config.dump(mask_secrets=True)


# ═══════════════════════════════════════════
# 启动入口
# ═══════════════════════════════════════════

if __name__ == "__main__":
    mcp.run(transport="stdio")
```

### 8.2 CLI 实现示例

```python
"""
cli.py — CLI 实现
使用 Click 框架
"""

import click
import asyncio
from pathlib import Path

from skill import SkillClient
from skill.models import BatchTaskConfig
from skill.enums import VideoProvider, VideoResolution


# ═══════════════════════════════════════════
# 上下文管理
# ═══════════════════════════════════════════

class CliContext:
    """CLI 上下文"""
    
    def __init__(self):
        self.verbose = 0
        self.json_output = False
        self.client: SkillClient | None = None
    
    async def get_client(self) -> SkillClient:
        if self.client is None:
            self.client = SkillClient()
            await self.client.initialize()
        return self.client


pass_context = click.make_pass_decorator(CliContext, ensure=True)


# ═══════════════════════════════════════════
# 主命令
# ═══════════════════════════════════════════

@click.group()
@click.option("-v", "--verbose", count=True, help="详细输出（可叠加）")
@click.option("-q", "--quiet", is_flag=True, help="静默模式")
@click.option("--json", "json_output", is_flag=True, help="JSON格式输出")
@click.option("--config", type=click.Path(), help="配置文件路径")
@click.option("--env", type=click.Path(), help=".env文件路径")
@click.version_option(version="1.0.0")
@click.pass_context
def cli(ctx, verbose, quiet, json_output, config, env):
    """带货视频批量生产 Skill — 命令行工具"""
    ctx.obj = CliContext()
    ctx.obj.verbose = verbose
    ctx.obj.json_output = json_output


# ═══════════════════════════════════════════
# CMD01: 单产品视频生成
# ═══════════════════════════════════════════

@cli.command()
@click.argument("image_path")
@click.option("-d", "--description", default="", help="产品补充描述")
@click.option("-g", "--gem-id", help="指定Gem模板ID")
@click.option("-p", "--provider", help="指定视频提供商")
@click.option("-r", "--resolution", default="1080p", help="输出分辨率")
@click.option("-t", "--duration", default=5, help="视频时长(秒)")
@click.option("--no-audio", is_flag=True, help="不生成音频")
@click.option("-o", "--output", type=click.Path(), help="输出目录")
@pass_context
def single(ctx, image_path, description, gem_id, provider, resolution, duration, no_audio, output):
    """为单个产品生成视频"""
    
    async def run():
        client = await ctx.get_client()
        result = await client.generate_single(
            image_path=image_path,
            user_description=description,
            preferred_gem_id=gem_id,
            preferred_provider=VideoProvider(provider) if provider else None,
            output_resolution=VideoResolution(resolution),
            output_duration=duration,
            generate_audio=not no_audio,
        )
        
        if ctx.json_output:
            click.echo(result.model_dump_json(indent=2))
        else:
            click.echo(f"✅ 视频生成完成: {result.video_local_path}")
            click.echo(f"💰 花费: ${result.cost_usd:.4f}")
    
    asyncio.run(run())


# ═══════════════════════════════════════════
# CMD02: 批量视频生成
# ═══════════════════════════════════════════

@cli.command()
@click.argument("image_dir", type=click.Path(exists=True))
@click.option("-n", "--name", default="未命名批次", help="批次名称")
@click.option("-d", "--description", default="", help="通用产品描述")
@click.option("-c", "--concurrency", default=3, help="最大并发数")
@click.option("-p", "--provider", help="指定视频提供商")
@click.option("-r", "--resolution", default="1080p", help="输出分辨率")
@click.option("-t", "--duration", default=5, help="视频时长(秒)")
@click.option("--no-audio", is_flag=True, help="不生成音频")
@click.option("--no-drive-upload", is_flag=True, help="不上传到Drive")
@click.option("--continue-on-error/--no-continue-on-error", default=True)
@pass_context
def batch(ctx, image_dir, name, description, concurrency, provider, resolution,
          duration, no_audio, no_drive_upload, continue_on_error):
    """批量生成视频"""
    
    async def run():
        client = await ctx.get_client()
        config = BatchTaskConfig(
            max_concurrency=concurrency,
            continue_on_error=continue_on_error,
            output_resolution=VideoResolution(resolution),
            output_duration=duration,
            preferred_provider=VideoProvider(provider) if provider else None,
            generate_audio=not no_audio,
            upload_to_drive=not no_drive_upload,
        )
        result = await client.generate_batch(
            image_dir=image_dir,
            batch_name=name,
            user_description=description,
            config=config,
        )
        
        if ctx.json_output:
            click.echo(result.model_dump_json(indent=2))
        else:
            click.echo(f"📦 批次创建: {result.batch_id}")
            click.echo(f"📊 总任务数: {result.total_tasks}")
            click.echo(f"⏳ 状态: {result.status}")
    
    asyncio.run(run())


# ═══════════════════════════════════════════
# CMD03: 查询进度
# ═══════════════════════════════════════════

@cli.command()
@click.argument("batch_id")
@click.option("-w", "--watch", is_flag=True, help="实时监控模式")
@click.option("--interval", default=5.0, help="刷新间隔(秒)")
@pass_context
def status(ctx, batch_id, watch, interval):
    """查询任务状态"""
    
    async def run():
        client = await ctx.get_client()
        
        while True:
            progress = await client.get_progress(batch_id)
            
            if ctx.json_output:
                click.echo(progress.model_dump_json(indent=2))
            else:
                bar_width = 40
                filled = int(bar_width * progress.percent / 100)
                bar = "█" * filled + "░" * (bar_width - filled)
                click.echo(f"\r[{bar}] {progress.percent:.1f}% "
                           f"({progress.completed}/{progress.total}) "
                           f"ETA: {progress.eta_seconds}s", nl=False)
            
            if not watch or progress.is_complete:
                click.echo()  # 换行
                break
            
            await asyncio.sleep(interval)
    
    asyncio.run(run())


# ═══════════════════════════════════════════
# CMD04-11: 其他命令
# ═══════════════════════════════════════════

@cli.command()
@click.argument("image_path")
@click.option("--dimensions", help="分析维度")
@click.option("--platform", help="平台偏好")
@pass_context
def analyze(ctx, image_path, dimensions, platform):
    """分析产品图片"""
    ...


@cli.command()
@click.argument("image_path")
@click.option("-n", "--top-n", default=3, help="Top N匹配")
@pass_context
def match(ctx, image_path, top_n):
    """智能路由匹配Gem模板"""
    ...


@cli.command()
@click.argument("image_path")
@click.argument("gem_id")
@click.option("-d", "--description", default="", help="产品描述")
@pass_context
def prompt(ctx, image_path, gem_id, description):
    """生成视频提示词"""
    ...


@cli.command()
@click.argument("prompt_text")
@click.option("-i", "--image", help="参考图片")
@click.option("-p", "--provider", default="seedance", help="视频提供商")
@pass_context
def video(ctx, prompt_text, image, provider):
    """直接生成视频"""
    ...


@cli.command()
@click.option("--category", help="按分类筛选")
@click.option("--refresh", is_flag=True, help="强制刷新")
@pass_context
def gems(ctx, category, refresh):
    """管理Gem模板"""
    ...


@cli.command()
@pass_context
def health(ctx):
    """系统健康检查"""
    
    async def run():
        client = await ctx.get_client()
        result = await client.health_check()
        
        if ctx.json_output:
            click.echo(result.model_dump_json(indent=2))
        else:
            status_emoji = {"healthy": "✅", "degraded": "⚠️", "unhealthy": "❌"}
            click.echo(f"{status_emoji.get(result.status, '❓')} 整体状态: {result.status}")
            for name, check in result.checks.items():
                emoji = status_emoji.get(check.status, "❓")
                click.echo(f"  {emoji} {name}: {check.message} ({check.latency_ms}ms)")
    
    asyncio.run(run())


@cli.command()
@click.option("--section", help="配置分组")
@pass_context
def config(ctx, section):
    """配置管理"""
    ...


@cli.command()
@click.argument("batch_id")
@pass_context
def retry(ctx, batch_id):
    """重试失败任务"""
    ...


if __name__ == "__main__":
    cli()
```

### 8.3 Python SDK 使用示例

```python
"""
examples/usage.py — Python SDK 使用示例
"""

import asyncio
from skill import SkillClient, EventType
from skill.models import BatchTaskConfig
from skill.enums import VideoProvider, VideoResolution


# ═══════════════════════════════════════════
# 示例1: 单产品视频生成
# ═══════════════════════════════════════════

async def example_single():
    """单产品视频生成示例"""
    
    async with SkillClient() as client:
        result = await client.generate_single(
            image_path="./product.jpg",
            user_description="高端抗衰老精华液",
            output_resolution=VideoResolution.P1080,
            output_duration=8,
        )
        
        print(f"任务状态: {result.status}")
        print(f"匹配Gem: {result.matched_gem_name} (得分: {result.match_score})")
        print(f"视频路径: {result.video_local_path}")
        print(f"花费: ${result.cost_usd:.4f}")


# ═══════════════════════════════════════════
# 示例2: 批量视频生成
# ═══════════════════════════════════════════

async def example_batch():
    """批量视频生成示例"""
    
    async with SkillClient() as client:
        # 提交批次
        summary = await client.generate_batch(
            image_dir="./products/",
            batch_name="双十一美妆批次",
            config=BatchTaskConfig(
                max_concurrency=5,
                preferred_provider=VideoProvider.SEEDANCE,
                output_resolution=VideoResolution.P720,
            ),
        )
        
        batch_id = summary.batch_id
        print(f"批次ID: {batch_id}, 总任务: {summary.total_tasks}")
        
        # 轮询进度
        while True:
            progress = await client.get_progress(batch_id)
            print(f"进度: {progress.percent:.1f}% ({progress.completed}/{progress.total})")
            
            if progress.is_complete:
                print("批次完成!")
                break
            
            await asyncio.sleep(10)


# ═══════════════════════════════════════════
# 示例3: 事件订阅
# ═══════════════════════════════════════════

async def example_events():
    """事件订阅示例"""
    
    client = SkillClient()
    await client.initialize()
    
    # 订阅进度事件
    def on_progress(event):
        payload = event.payload
        print(f"进度更新: {payload['progress_percent']:.1f}%")
    
    client.event_bus.subscribe(EventType.BATCH_PROGRESS, on_progress)
    
    # 订阅完成事件
    def on_complete(event):
        print(f"任务完成: {event.task_id}")
    
    client.event_bus.subscribe(EventType.TASK_COMPLETED, on_complete)
    
    # 订阅错误事件
    def on_error(event):
        print(f"错误: {event.payload['message']}")
    
    client.event_bus.subscribe(EventType.ERROR_OCCURRED, on_error)
    
    # 执行批量任务
    await client.generate_batch(image_dir="./products/")


# ═══════════════════════════════════════════
# 示例4: 原子操作（图片分析 + 路由 + 提示词）
# ═══════════════════════════════════════════

async def example_atomic():
    """原子操作示例 — 手动编排流程"""
    
    async with SkillClient() as client:
        # 步骤1: 分析图片
        features = await client.analyze_image(
            image_path="./product.jpg",
            platform_hint="douyin",
        )
        print(f"产品品类: {features.category}")
        print(f"目标人群: {features.target_gender}, {features.target_age}")
        
        # 步骤2: 智能路由
        match = await client.match_gem(features, top_n=3)
        print(f"\nTop 3 匹配:")
        for item in match.matches:
            print(f"  - {item.gem_name}: {item.score:.2f}")
        
        # 步骤3: 生成提示词
        top_gem = match.matches[0]
        prompt_result = await client.generate_prompt(
            image_path="./product.jpg",
            gem_id=top_gem.gem_id,
            user_description="高端护肤品",
        )
        print(f"\n生成提示词:\n{prompt_result.prompt}")
        
        # 步骤4: 生成视频
        video_result = await client.generate_video_raw(
            prompt=prompt_result.prompt,
            reference_image="./product.jpg",
            provider=VideoProvider.SEEDANCE,
            duration=8,
        )
        print(f"\n视频路径: {video_result.video_local_path}")


# ═══════════════════════════════════════════
# 示例5: 配置热更新
# ═══════════════════════════════════════════

async def example_config():
    """配置热更新示例"""
    
    from skill.config import ConfigManager
    
    config = ConfigManager(config_path="./config.yaml")
    
    # 读取配置
    concurrency = config.get("task.max_concurrency", default=3)
    print(f"当前并发数: {concurrency}")
    
    # 监听配置变更
    def on_concurrency_change(old, new):
        print(f"并发数变更: {old} -> {new}")
    
    handle = config.watch("task.max_concurrency", on_concurrency_change)
    
    # 热重载
    result = await config.reload("task")
    print(f"重载变更: {result.changed}")
    
    # 启动自动重载
    await config.start_auto_reload(interval_sec=60)
    
    # ... 运行一段时间后 ...
    
    await config.stop_auto_reload()
    handle.cancel()


# ═══════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════

if __name__ == "__main__":
    # asyncio.run(example_single())
    # asyncio.run(example_batch())
    asyncio.run(example_atomic())
```

### 8.4 配置热更新示例

```python
"""
examples/config_reload.py — 配置热更新使用示例
"""

import asyncio
from skill.config import ConfigManager


async def main():
    # 初始化配置管理器
    config = ConfigManager(
        config_path="./config.yaml",
        env_file="./.env",
    )
    
    # 读取当前配置
    print("=== 当前配置 ===")
    print(f"并发数: {config.get('task.max_concurrency')}")
    print(f"超时: {config.get('task.timeout_sec')}")
    print(f"日志级别: {config.get('monitoring.log_level')}")
    
    # 监听特定配置项变更
    async def on_video_provider_change(old, new):
        print(f"[监听器] 视频提供商默认配置变更: {old} -> {new}")
    
    handle = config.watch("video.defaults.provider", on_video_provider_change)
    
    # 监听整个分组变更
    async def on_task_config_change(old, new):
        print(f"[监听器] 任务配置变更: {old} -> {new}")
    
    handle2 = config.watch_section("task", on_task_config_change)
    
    # 热重载配置（模拟外部修改了 config.yaml）
    print("\n=== 热重载配置 ===")
    result = await config.reload()
    print(f"变更项: {result.changed}")
    print(f"错误: {result.errors}")
    
    # 启动自动重载（每30秒检查一次文件修改）
    print("\n=== 启动自动重载 ===")
    await config.start_auto_reload(interval_sec=30)
    
    # 运行60秒后停止
    await asyncio.sleep(60)
    
    await config.stop_auto_reload()
    handle.cancel()
    handle2.cancel()


if __name__ == "__main__":
    asyncio.run(main())
```

---

## 附录A: 接口版本历史

| 版本 | 日期 | 变更说明 |
|------|------|----------|
| v1.0 | 2026-07 | 初始版本，包含10个MCP工具、11个CLI命令、完整Python API |

## 附录B: 术语表

| 术语 | 说明 |
|------|------|
| Gem | Google Gemini 的自定义模板，用于特定场景的提示词生成 |
| VLM | Visual Language Model，视觉语言模型 |
| Provider | 视频生成服务提供商（如Seedance、Kling） |
| Skill | 本系统的整体名称，一个可复用的AI能力模块 |
| MCP | Model Context Protocol，模型上下文协议 |
| GWS | Google Workspace，谷歌办公套件 |

## 附录C: 文件清单

| 文件路径 | 说明 |
|----------|------|
| `/mnt/agents/output/stage-3/interface-specification.md` | 本文件 — 接口规范总设计 |
| `/mnt/agents/output/stage-3/architecture-system.md` | 总体系统架构设计 |
| `/mnt/agents/output/stage-3/module-gemini.md` | Gemini对接模块设计 |
| `/mnt/agents/output/stage-3/module-image-analysis.md` | 图片分析模块设计 |
| `/mnt/agents/output/stage-3/module-smart-router.md` | 智能路由模块设计 |
| `/mnt/agents/output/stage-3/module-video-generation.md` | 视频生成模块设计 |
| `/mnt/agents/output/stage-3/module-workspace.md` | Workspace集成模块设计 |
| `/mnt/agents/output/stage-3/module-task-scheduler.md` | 任务调度模块设计 |
| `/mnt/agents/output/stage-3/module-error-monitoring.md` | 错误处理与监控模块设计 |
