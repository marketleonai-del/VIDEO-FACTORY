# 带货视频批量生产 Skill — 总体系统架构设计

> **文档版本**: v1.0 (Stage-3 核心产出)
> **生成日期**: 2026-07
> **设计输入**: Stage-2 融合分析结果（45个功能点，22个P0）
> **设计范围**: 全系统分层架构、模块划分、数据流、技术选型

---

## 目录

1. [架构概述与设计原则](#1-架构概述与设计原则)
2. [系统分层架构图](#2-系统分层架构图)
3. [核心模块划分与职责定义](#3-核心模块划分与职责定义)
4. [模块依赖关系图](#4-模块依赖关系图)
5. [数据流设计](#5-数据流设计)
6. [状态机设计](#6-状态机设计)
7. [技术栈总览](#7-技术栈总览)
8. [关键设计决策（ADR）](#8-关键设计决策adr)
9. [部署架构](#9-部署架构)
10. [接口规范摘要](#10-接口规范摘要)

---

## 1. 架构概述与设计原则

### 1.1 系统定位

本系统是一个**带货视频批量生产 Skill**，核心能力是将产品图片通过 AI 智能分析、Gem 模板匹配、提示词生成、视频生成，最终输出带货视频。系统支持单产品处理和批量并发处理两种模式，集成 Google Workspace 实现配置管理和结果交付。

### 1.2 设计原则

| 原则 | 说明 | 在本系统中的体现 |
|------|------|----------------|
| **模块化** | 高内聚低耦合，每个模块职责单一 | 8大核心模块，模块间仅通过接口通信 |
| **可扩展性** | 新增视频模型/Gem模板零改动 | 适配器模式：新增模型只需实现适配器，新增Gem只需配置Sheets |
| **可测试性** | 每个模块可独立测试 | 抽象接口 + Mock实现，支持单元/集成/端到端测试 |
| **可观测性** | 日志、监控、追踪全覆盖 | 结构化日志 + Prometheus指标 + 分布式追踪 |
| **容错性** | 错误隔离、自动恢复、优雅降级 | 三级降级链 + 熔断保护 + 自动重试 |
| **配置驱动** | 行为通过配置改变，不修改代码 | Gems配置/模型配置/路由规则全部Sheets化 |

### 1.3 架构风格

采用**分层架构 + 管道过滤器 + 策略模式**的混合风格：

- **分层架构**: 表现层 → 业务层 → 服务层 → 基础设施层
- **管道过滤器**: 单产品流程的7个阶段形成处理管道
- **策略模式**: 视频模型适配器、Gem选择策略可插拔替换

### 1.4 关键架构指标

| 指标 | 目标值 | 说明 |
|------|--------|------|
| 模块间耦合度 | < 3个依赖/模块 | 每个模块最多依赖3个其他模块 |
| 新增模型成本 | 1人天 | 实现适配器即可接入 |
| 新增Gem成本 | 0代码 | 在Sheets中添加配置行 |
| 单产品端到端延迟 | < 180秒 | 分析(5s) + 路由(1s) + 提示词(10s) + 视频(120s) |
| 批量并发度 | 3-5个并行 | 可配置的信号量并发控制 |
| 系统可用性 | > 99% | 三级降级链保障 |

---

## 2. 系统分层架构图

### 2.1 总体分层架构（ASCII）

```
+==========================================================================+
|                         带货视频批量生产 Skill                              |
|                         总体分层架构图 (v1.0)                              |
+==========================================================================+
|                                                                          |
|  LAYER 4: 表现层 (Presentation Layer)                                    |
|  +-------------------+  +-------------------+  +---------------------+  |
|  |  MCP Server       |  |  CLI Interface    |  |  HTTP API (Future)  |  |
|  |  (P1: F032+F026)  |  |  (P0: Direct)     |  |  (P3: Reserved)     |  |
|  |  10 Tools暴露      |  |  命令行交互        |  |  RESTful接口预留     |  |
|  +--------+----------+  +--------+----------+  +----------+----------+  |
|           |                      |                        |              |
+-----------|----------------------|------------------------|--------------+
|           |                      |                        |               |
|  LAYER 3: 业务层 (Business Layer)                                          |
|           |                      |                        |               |
|  +--------v----------+  +--------v----------+  +---------v---------+    |
|  |  Task Controller  |  |  Smart Router     |  |  Prompt Builder   |    |
|  |  任务控制器        |  |  智能路由引擎      |  |  提示词生成器      |    |
|  |  (F028)           |  |  (F021+F022)      |  |  (Gem调用)         |    |
|  |  - 单/批量调度     |  |  - 4维度匹配       |  |  - 提示词组装      |    |
|  |  - 状态机驱动      |  |  - TopN推荐        |  |  - 多Gem并行       |    |
|  |  - 并发控制        |  |  - 置信度判断      |  |  - 结果解析        |    |
|  +--------+----------+  +--------+----------+  +---------+---------+    |
|           |                      |                        |               |
|           |  +-------------------v--------------------+   |               |
|           |  |  Image Analyzer                         |   |               |
|           |  |  图片分析模块 (F022)                     |   |               |
|           |  |  - VLM视觉分析 / 4维度提取               |   |               |
|           |  |  - 缓存 / 置信度评估                     |   |               |
|           |  +-------------------+--------------------+   |               |
|           |                      |                        |               |
+-----------|----------------------|------------------------|---------------+
|           |                      |                        |               |
|  LAYER 2: 服务层 (Service Layer)                                           |
|           |                      |                        |               |
|  +--------v----------+  +--------v----------+  +---------v---------+    |
|  |  Gemini Service   |  |  Video Generation |  |  Workspace        |    |
|  |  Gemini服务        |  |  视频生成服务      |  |  集成服务          |    |
|  |  (F012-F014)      |  |  (F017-F020)      |  |  (F015-F016)      |    |
|  |                   |  |                   |  |                   |    |
|  |  +-------------+  |  |  +-------------+  |  |  +-------------+  |    |
|  |  |WebapiAdapter|  |  |  |UnifiedVideo |  |  |  |GWSClient    |  |    |
|  |  |  (F014)     |  |  |  |Generator    |  |  |  |  (F015)     |  |    |
|  |  +------+------+  |  |  +------+------+  |  |  +------+------+  |    |
|  |         |         |  |         |         |  |         |         |    |
|  |  +------v------+  |  |  +------v------+  |  |  +------v------+  |    |
|  |  |Cookie Health|  |  |  |Model Adapter|  |  |  |Sheets IO    |  |    |
|  |  |Checker(F027)|  |  |  |Registry     |  |  |  |  (F007)     |  |    |
|  |  +-------------+  |  |  +------+------+  |  |  +-------------+  |    |
|  |                   |  |         |         |  |                   |    |
|  +-------------------+  |  +------v------+  |  +-------------------+    |
|                         |  |Kling|Veo|Seed|  |                           |
|                         |  |Vidu |Run|Pika|  |                           |
|                         |  +-------------+  |                           |
|                         +-------------------+                           |
|                                                                          |
+--------------------------------------------------------------------------+
|                                                                          |
|  LAYER 1: 基础设施层 (Infrastructure Layer)                               |
|  +-------------------+  +-------------------+  +---------------------+  |
|  |  Config Manager   |  |  Error Handler    |  |  Metrics & Monitor  |  |
|  |  配置管理 (F008)   |  |  错误处理 (F003-5) |  |  监控指标 (F030-31)  |  |
|  +-------------------+  +-------------------+  +---------------------+  |
|  +-------------------+  +-------------------+  +---------------------+  |
|  |  Auth Manager     |  |  Rate Limiter     |  |  Task Poller        |  |
|  |  认证管理 (F001-2) |  |  限流器 (F010)    |  |  轮询框架 (F011)    |  |
|  +-------------------+  +-------------------+  +---------------------+  |
|  +-------------------+  +-------------------+  +---------------------+  |
|  |  BaseClient       |  |  Retry Engine     |  |  Notifier           |  |
|  |  统一客户端 (F009) |  |  重试引擎 (F004)  |  |  通知器 (F029)      |  |
|  +-------------------+  +-------------------+  +---------------------+  |
|                                                                          |
+==========================================================================+

外部依赖 (External Dependencies):
+---------------+  +---------------+  +---------------+  +---------------+
| gemini-webapi |  | Kling/Veo/    |  | Google APIs   |  | Browser       |
| (AGPL-3.0)    |  | Seedance/Vidu |  | (Sheets/Drive |  | Cookie3       |
| 独立进程隔离   |  | /Runway/Pika  |  | /Gmail)       |  | (Cookie刷新)  |
+---------------+  +---------------+  +---------------+  +---------------+
```

### 2.2 分层职责定义

| 层级 | 职责 | 包含模块 | 依赖方向 |
|------|------|----------|----------|
| **表现层** | 接收用户输入，返回格式化结果 | MCP Server, CLI | 仅依赖业务层 |
| **业务层** | 核心业务逻辑编排 | TaskController, SmartRouter, ImageAnalyzer, PromptBuilder | 依赖服务层和基础设施层 |
| **服务层** | 外部服务封装和适配 | GeminiService, VideoGenerationService, WorkspaceService | 依赖基础设施层 |
| **基础设施层** | 横切关注点 | Config, Auth, ErrorHandler, RateLimiter, Poller, Metrics | 不依赖上层 |

---

## 3. 核心模块划分与职责定义

### 3.1 模块总览（8大核心模块）

```
+========================================================================+
|                        核心模块划分全景图                               |
+========================================================================+
|                                                                         |
|  +----------------+  +----------------+  +----------------+            |
|  |  配置管理模块   |  |  图片分析模块   |  |  智能路由模块   |            |
|  |  ConfigManager |  | ImageAnalyzer  |  |  SmartRouter   |            |
|  |  (F008)        |  |  (F022)        |  |  (F021)        |            |
|  +----------------+  +----------------+  +----------------+            |
|                                                                         |
|  +----------------+  +----------------+  +----------------+            |
|  |  提示词生成模块 |  |  视频生成模块   |  |  任务管理模块   |            |
|  | PromptBuilder  |  | VideoGenerator |  | TaskController |            |
|  |  (Gem调用)      |  |  (F017-F020)   |  |  (F028)        |            |
|  +----------------+  +----------------+  +----------------+            |
|                                                                         |
|  +----------------+  +----------------+                               |
|  |  Workspace集成  |  |  错误处理与监控 |                               |
|  | WorkspaceClient|  | Error/Monitor  |                               |
|  |  (F015-F016)   |  |  (F003-F005)   |                               |
|  +----------------+  +----------------+                               |
|                                                                         |
+========================================================================+
```

### 3.2 模块详细定义

#### M1: 配置管理模块 (ConfigManager) — F008

```python
"""
配置管理模块 — 统一管理所有配置项
- 配置来源优先级: 环境变量 > .env文件 > config.yaml > 默认值
- 支持运行时热更新（Sheets配置变更自动感知）
- 提供类型安全的配置访问接口
"""

class ConfigManager:
    """配置管理器"""
    
    # 配置分组
    gemini: GeminiConfig          # Cookie路径、MCP端点
    workspace: WorkspaceConfig    # Service Account、Sheets ID
    video: VideoProvidersConfig   # 各模型API Key、限流参数
    router: RouterConfig          # 匹配权重、置信度阈值
    task: TaskConfig              # 并发数、超时、重试
    
    async def load(self, source: ConfigSource = ConfigSource.AUTO) -> None:
        """从所有来源加载配置"""
        
    async def reload(self, section: Optional[str] = None) -> None:
        """热重载指定配置分组"""
        
    def get(self, key: str, default: Any = None) -> Any:
        """获取配置项，支持点号路径 (如 "video.kling.api_key")"""
        
    def watch(self, key: str, callback: Callable) -> None:
        """监听配置变更"""
```

**关键配置项**:

| 配置分组 | 关键项 | 来源 | 说明 |
|----------|--------|------|------|
| gemini | `secure_1psid` | 环境变量 | 主Cookie |
| gemini | `secure_1psidts` | 环境变量 | 辅助Cookie |
| workspace | `service_account_file` | 环境变量 | SA JSON路径 |
| workspace | `gem_metadata_sheet_id` | config.yaml | Gems配置表ID |
| video.kling | `api_key` | 环境变量 | 可灵API Key |
| video.veo | `api_key` | 环境变量 | Veo API Key |
| router | `match_weights` | config.yaml | 匹配权重配置 |
| task | `max_concurrency` | config.yaml | 最大并发数 |

---

#### M2: 图片分析模块 (ImageAnalyzer) — F022

```python
"""
图片分析模块 — 使用VLM对商品图片进行4维度深度分析
- 产品维度: 品类、价格带、卖点、品牌定位
- 人群维度: 性别、年龄、画像、购买力
- 场景维度: 营销场景、使用场景、平台适配
- 风格维度: 视频风格、画面调性、剪辑节奏

支持结果缓存（图片哈希 → 分析结果）
"""

class ImageAnalyzer:
    """图片分析器"""
    
    def __init__(
        self,
        gemini_provider: BaseGeminiProvider,  # 依赖Gemini服务
        cache: Optional[AnalysisCache] = None,  # 可选缓存
    ):
        ...
    
    async def analyze(
        self,
        image: ReferenceMedia,           # 输入图片
        user_description: Optional[str] = None,  # 用户补充描述
    ) -> ImageAnalysisResult:           # 4维度分析结果
        """
        执行图片深度分析
        
        流程:
        1. 计算图片哈希，检查缓存
        2. 构建VLM分析Prompt（4维度引导）
        3. 调用Gemini进行视觉分析
        4. 解析并验证4维度结果
        5. 计算整体置信度
        6. 写入缓存
        """
        
    async def analyze_batch(
        self,
        images: List[ReferenceMedia],
    ) -> List[ImageAnalysisResult]:
        """批量分析（并发执行）"""
```

**分析Prompt设计**:

```
你是一位电商视觉分析专家。请对这张商品图片进行深度分析，输出JSON格式：

1. 产品维度: 一级品类、二级品类、产品特征(功能型/颜值型/性价比型/高端型)、价格带、卖点、品牌定位
2. 人群维度: 目标性别、主要年龄段、人群画像、购买力
3. 场景维度: 适合营销场景、使用场景、内容平台、带货节奏
4. 风格维度: 视频风格、画面调性、剪辑节奏、推荐情绪

要求: 每项给出置信度(0-1)，整体JSON必须可解析。
```

---

#### M3: 智能路由模块 (SmartRouter) — F021

```python
"""
智能路由模块 — 根据图片分析结果匹配最优Gem模板
- 输入: ImageAnalysisResult（4维度标签）
- 输出: RouteMatchResult（Top N Gem推荐 + 置信度 + 建议）
- 算法: 多维度加权匹配 + 历史效果排序

权重配置（可配置化）:
  产品品类 30% | 营销场景 20% | 目标人群 15%
  价格带 10%  | 视频风格 10% | 历史效果 10% | 其他 5%
"""

class SmartRouter:
    """智能路由引擎"""
    
    def __init__(
        self,
        gem_store: GemMetadataStore,      # Gem元数据存储（Sheets）
        config: RouterConfig,              # 路由配置（权重/阈值）
    ):
        ...
    
    async def route(
        self,
        analysis: ImageAnalysisResult,     # 图片分析结果
        top_n: int = 3,                    # 返回Top N
        min_score: float = 0.5,            # 最低匹配分
    ) -> RouteMatchResult:                 # 匹配结果
        """
        执行智能路由匹配
        
        流程:
        1. 从Sheets加载活跃Gem列表
        2. 提取分析结果的标签集合
        3. 对每个Gem计算多维度匹配分数
        4. 加权汇总排序
        5. 判断置信度等级
        6. 生成推荐理由和建议
        """
        
    def _compute_match_score(
        self,
        analysis_tags: set[str],
        gem: GemMetadata,
    ) -> MatchScoreBreakdown:
        """计算单个Gem的多维度匹配分数"""
        
    async def record_feedback(
        self,
        gem_id: str,
        success: bool,
        match_score: float,
    ) -> None:
        """记录使用效果反馈（用于自学习）"""
```

**置信度判断规则**:

| Top1分数 | 置信度等级 | 建议操作 | 生成视频数 |
|----------|-----------|----------|-----------|
| > 0.85 | HIGH | 直接使用Top1 | 1个 |
| 0.70-0.85 | MEDIUM | 从Top3中选择 | 最多3个 |
| < 0.70 | LOW | 人工确认或补充信息 | 最多3个 |

---

#### M4: 提示词生成模块 (PromptBuilder)

```python
"""
提示词生成模块 — 调用Gem生成视频生成提示词
- 输入: 选中Gem + 图片分析结果 + 原始图片
- 输出: 结构化视频提示词
- 特性: 支持多Gem并行生成，取最优结果
"""

class PromptBuilder:
    """提示词生成器"""
    
    def __init__(
        self,
        gemini_provider: BaseGeminiProvider,
    ):
        ...
    
    async def build_prompt(
        self,
        gem: GemMetadata,                  # 选中的Gem
        analysis: ImageAnalysisResult,     # 图片分析结果
        image: ReferenceMedia,             # 原始产品图
        options: Optional[PromptOptions] = None,  # 生成选项
    ) -> PromptResult:                     # 提示词结果
        """
        调用Gem生成视频提示词
        
        流程:
        1. 根据Gem模板构建System Prompt
        2. 组装用户输入（图片 + 分析结果）
        3. 调用Gemini生成提示词
        4. 解析和验证输出格式
        5. 返回结构化提示词
        """
        
    async def build_prompt_multi_gem(
        self,
        matches: List[RouteMatchItem],     # Top N匹配结果
        analysis: ImageAnalysisResult,
        image: ReferenceMedia,
    ) -> List[PromptResult]:
        """多Gem并行生成提示词"""
```

---

#### M5: 视频生成模块 (VideoGenerator) — F017-F020

```python
"""
视频生成模块 — 多模型适配的统一视频生成接口
- 核心设计: 适配器模式 + 工厂模式
- 支持模型: Kling 3.0 / Veo 3.1 / Seedance 2.0 / Vidu Q3 / Runway Gen-4 / Pika 2.2
- 特性: 智能选型、自动降级、异步轮询、成本追踪
"""

# ============ 统一接口 ============

class UnifiedVideoGenerator:
    """统一视频生成器 — 所有视频生成的唯一入口"""
    
    def __init__(
        self,
        adapter_registry: AdapterRegistry,    # 适配器注册表
        model_selector: Optional[ModelSelector] = None,  # 智能选型器(P1)
        failover_manager: FailoverManager,      # 降级管理器
    ):
        ...
    
    async def generate(
        self,
        request: VideoGenerationRequest,    # 统一请求格式
    ) -> VideoGenerationResult:            # 统一结果格式
        """
        统一视频生成入口
        
        流程:
        1. 智能选型（如未指定提供商）
        2. 检查提供商能力兼容性
        3. 提交生成任务
        4. 异步轮询直到完成
        5. 下载视频到本地
        6. 返回统一结果
        7. 失败时自动降级重试
        """
        
    async def generate_batch(
        self,
        requests: List[VideoGenerationRequest],
        config: BatchTaskConfig,
    ) -> List[VideoGenerationResult]:
        """批量生成（信号量并发控制）"""


# ============ 适配器接口 ============

class BaseVideoAdapter(ABC):
    """视频适配器抽象基类 — 新增模型只需实现此类"""
    
    @property
    @abstractmethod
    def provider(self) -> VideoProvider: ...       # 提供商标识
    
    @property
    @abstractmethod
    def capability(self) -> ProviderCapability: ... # 能力声明
    
    @abstractmethod
    async def submit(self, request: VideoGenerationRequest) -> str: ...
        """提交任务，返回task_id"""
        
    @abstractmethod
    async def query(self, task_id: str) -> VideoGenerationResult: ...
        """查询任务状态"""
        
    @abstractmethod
    async def download(self, video_url: str, save_path: str) -> str: ...
        """下载视频到本地"""


# ============ 具体适配器示例 ============

class KlingAdapter(BaseVideoAdapter):
    """可灵 Kling 3.0 适配器"""
    provider = VideoProvider.KLING_3_0
    capability = ProviderCapability(
        supports_4k=True,
        supports_audio_generation=False,
        max_duration=10,
        ...
    )

class VeoAdapter(BaseVideoAdapter):
    """Google Veo 3.1 适配器"""
    provider = VideoProvider.VEO_3_1_STANDARD
    capability = ProviderCapability(
        supports_4k=True,
        supports_audio_generation=True,
        max_duration=8,
        ...
    )

class SeedanceAdapter(BaseVideoAdapter):
    """即梦 Seedance 2.0 适配器"""
    provider = VideoProvider.SEEDANCE_2_0
    capability = ProviderCapability(
        supports_4k=True,
        supports_reference_images=True,
        supports_reference_audios=True,
        max_duration=12,
        ...
    )
```

**适配器注册表（自动发现）**:

```python
class AdapterRegistry:
    """适配器注册表 — 自动发现和注册所有适配器"""
    
    def __init__(self):
        self._adapters: Dict[VideoProvider, BaseVideoAdapter] = {}
        self._discover_and_register()  # 自动发现
    
    def _discover_and_register(self) -> None:
        """自动发现所有BaseVideoAdapter子类并注册"""
        for cls in BaseVideoAdapter.__subclasses__():
            adapter = cls()
            self._adapters[adapter.provider] = adapter
    
    def get(self, provider: VideoProvider) -> BaseVideoAdapter:
        return self._adapters[provider]
    
    def list_available(self, request: VideoGenerationRequest) -> List[VideoProvider]:
        """列出支持给定请求的所有提供商"""
        available = []
        for provider, adapter in self._adapters.items():
            supports, _ = adapter.capability.supports_request(request)
            if supports:
                available.append(provider)
        return available
```

---

#### M6: 任务管理模块 (TaskController) — F028

```python
"""
任务管理模块 — 批量任务调度与状态机管理
- 状态机: 7状态完整生命周期
- 调度: 信号量并发控制
- 存储: Sheets持久化 + 内存缓存
"""

class TaskController:
    """任务控制器"""
    
    def __init__(
        self,
        sheets_client: GWSClient,          # Sheets存储
        image_analyzer: ImageAnalyzer,      # 图片分析
        smart_router: SmartRouter,          # 智能路由
        prompt_builder: PromptBuilder,      # 提示词生成
        video_generator: UnifiedVideoGenerator,  # 视频生成
        config: TaskConfig,                  # 任务配置
    ):
        ...
    
    # ========== 单产品流程 ==========
    
    async def process_single(
        self,
        image: ReferenceMedia,
        user_description: Optional[str] = None,
    ) -> TaskRecord:
        """
        单产品完整处理流程
        
        状态流转:
        PENDING → ANALYZING → ROUTING → PROMPTING → GENERATING → DOWNLOADING → COMPLETED
                                              ↓
                                           FAILED (可重试)
        """
        
    # ========== 批量流程 ==========
    
    async def process_batch(
        self,
        images: List[ReferenceMedia],
        config: BatchTaskConfig,
    ) -> BatchTaskSummary:
        """
        批量处理流程
        
        调度策略:
        - 信号量并发控制（默认3个并行）
        - 错误继续模式（单个失败不影响整体）
        - 全局超时保护
        - 实时进度更新到Sheets
        """
        
    async def get_progress(self, batch_id: str) -> Dict[str, Any]:
        """获取批量任务进度"""
        
    async def retry_failed(self, batch_id: str) -> BatchTaskSummary:
        """重试批次中的失败任务"""
```

---

#### M7: Workspace集成模块 (WorkspaceClient) — F015-F016

```python
"""
Workspace集成模块 — Google Workspace 读写操作
- Sheets: gem-metadata表读写、task-queue表读写
- Drive: 视频文件上传、文件夹管理
- Gmail: 批量完成通知邮件
"""

class WorkspaceClient:
    """Workspace客户端"""
    
    def __init__(
        self,
        auth_manager: AuthManager,           # 认证管理
        config: WorkspaceConfig,             # Workspace配置
    ):
        ...
    
    # ========== Sheets操作 ==========
    
    async def read_gem_metadata(self) -> List[GemMetadata]:
        """从gem-metadata表读取所有Gem配置"""
        
    async def write_task_record(self, record: TaskRecord) -> None:
        """写入task-queue表"""
        
    async def update_task_status(
        self,
        task_id: str,
        status: TaskStatus,
        updates: Dict[str, Any],
    ) -> None:
        """更新任务状态"""
        
    async def append_batch_summary(self, summary: BatchTaskSummary) -> None:
        """追加批量汇总到Sheets"""
    
    # ========== Drive操作 ==========
    
    async def upload_video(
        self,
        local_path: str,
        folder_id: Optional[str] = None,
    ) -> str:  # 返回Drive file_id
        """上传视频到Google Drive"""
        
    async def create_folder(self, name: str, parent_id: Optional[str] = None) -> str:
        """创建Drive文件夹"""
    
    # ========== Gmail操作 ==========
    
    async def send_notification(
        self,
        to: str,
        subject: str,
        body: str,
    ) -> None:
        """发送通知邮件"""
```

---

#### M8: 错误处理与监控模块 (ErrorHandler + Metrics) — F003-F005, F029-F031

```python
"""
错误处理与监控模块 — 全系统的横切关注点
- 统一异常体系: 7大类40+错误码
- 三级降级链: 模型级 → Provider级 → 服务级
- 监控指标: Prometheus格式，覆盖全链路
"""

# ========== 统一异常体系 ==========

class UnifiedAPIError(Exception):
    """统一异常基类"""
    error_code: str
    retryable: bool
    api_type: Optional[ApiType]

class AuthenticationError(UnifiedAPIError):      # 401/403 不重试
class RateLimitError(UnifiedAPIError):           # 429 等待后重试
class QuotaExceededError(UnifiedAPIError):       # 配额超限 不重试
class TaskTimeoutError(UnifiedAPIError):          # 超时 可重试
class TaskFailedError(UnifiedAPIError):           # 任务失败 不重试
class ValidationError(UnifiedAPIError):           # 参数错误 不重试
class ServerError(UnifiedAPIError):               # 5xx 自动重试

# ========== 三级降级管理器 ==========

class FailoverManager:
    """三级降级管理器"""
    
    async def execute_with_failover(
        self,
        operation: Callable[[], T],
        fallback_chain: List[Callable[[], T]],
    ) -> T:
        """
        执行带降级的操作
        
        降级链:
        Tier 1: 模型级降级（同Provider的不同模型，如Pro→Flash）
        Tier 2: Provider级降级（切换到其他Provider）
        Tier 3: 服务级降级（返回缓存/通用模板）
        """
        
    def _circuit_breaker_check(self, provider: VideoProvider) -> bool:
        """熔断器检查 — 连续5次失败熔断60秒"""

# ========== 监控指标收集器 ==========

class MetricsCollector:
    """监控指标收集器 — Prometheus格式"""
    
    # 计数器
    tasks_total: Counter      # 任务总数（按状态分类）
    errors_total: Counter     # 错误总数（按错误码分类）
    failover_total: Counter   # 降级触发次数（按Tier分类）
    
    # 仪表盘
    active_tasks: Gauge       # 当前活跃任务数
    circuit_breaker_state: Gauge  # 熔断器状态
    cookie_health: Gauge      # Cookie健康度
    
    # 直方图
    task_duration: Histogram  # 任务耗时分布
    api_latency: Histogram    # API延迟分布
    cost_usd: Gauge           # 累计成本（美元）
```

---

### 3.3 模块接口矩阵

| 模块 | 输入接口 | 输出接口 | 依赖模块 | 被依赖模块 |
|------|----------|----------|----------|-----------|
| ConfigManager | 环境变量/.env/yaml | 类型安全配置 | 无 | 全部 |
| ImageAnalyzer | ReferenceMedia | ImageAnalysisResult | ConfigManager, GeminiService | SmartRouter, TaskController |
| SmartRouter | ImageAnalysisResult | RouteMatchResult | ConfigManager, WorkspaceClient | PromptBuilder, TaskController |
| PromptBuilder | GemMetadata, ImageAnalysisResult | PromptResult | GeminiService | TaskController |
| VideoGenerator | VideoGenerationRequest | VideoGenerationResult | ConfigManager, ErrorHandler | TaskController |
| TaskController | ReferenceMedia列表 | TaskRecord/BatchSummary | 全部业务模块 | Presentation Layer |
| WorkspaceClient | 各类数据模型 | Sheets/Drive/Gmail操作结果 | ConfigManager, AuthManager | SmartRouter, TaskController |
| ErrorHandler | 异常事件 | 降级决策/告警通知 | ConfigManager | 全部 |

---

## 4. 模块依赖关系图

### 4.1 完整依赖关系图（ASCII）

```
+====================================================================+
|                      模块依赖关系图 (完整版)                         |
+====================================================================+
|                                                                     |
|  基础设施层（底层，被所有上层依赖）                                    |
|  +-------------+  +-------------+  +-------------+                 |
|  | ConfigManager|  |AuthManager  |  |ErrorHandler |                 |
|  |  (F008)     |  | (F001/F002) |  | (F003-F005) |                 |
|  +------+------+  +------+------+  +------+------+                 |
|         |                |                |                         |
|         v                v                v                         |
|  +------+------+  +------+------+  +------+------+                 |
|  |BaseClient   |  |RateLimiter  |  |RetryEngine  |                 |
|  |  (F009)     |  |  (F010)     |  |  (F004)     |                 |
|  +------+------+  +------+------+  +------+------+                 |
|         |                |                |                         |
|         +----------------+----------------+                         |
|                          |                                          |
|                          v                                          |
|  +-----------------------+-----------------------+                 |
|  |           AsyncTaskPoller (F011)             |                 |
|  +-----------------------+-----------------------+                 |
|                          |                                          |
+--------------------------|------------------------------------------+
                           |
+--------------------------|------------------------------------------+
|                          v                                          |
|  服务层                                                              |
|  +-----------------------+-----------------------+                 |
|  |                                                 |                 |
|  |  +----------------+      +----------------+   |                 |
|  |  | GeminiService  |      | VideoGenerator |   |                 |
|  |  | (F012-F014)    |      | (F017-F020)    |   |                 |
|  |  |                |      |                |   |                 |
|  |  | +------------+ |      | +------------+ |   |                 |
|  |  | |WebapiAdapter| |      | | KlingAdapter | |   |                 |
|  |  | +------------+ |      | +------------+ |   |                 |
|  |  | +------------+ |      | | VeoAdapter   | |   |                 |
|  |  | |CookieHealth| |      | +------------+ |   |                 |
|  |  | |Checker     | |      | |SeedanceAdapter|   |                 |
|  |  | +------------+ |      | +------------+ |   |                 |
|  |  +-------+--------+      | | ViduAdapter  | |   |                 |
|  |          |               | +------------+ |   |                 |
|  |          |               +--------+-------+   |                 |
|  |          |                        |            |                 |
|  |  +-------v------------------------v-------+   |                 |
|  |  |      WorkspaceClient (F015-F016)       |   |                 |
|  |  |  - Sheets IO (F007)                    |   |                 |
|  |  |  - Drive Upload                        |   |                 |
|  |  |  - Gmail Notify                        |   |                 |
|  |  +----------------------------------------+   |                 |
|  |                                                 |                 |
|  +-----------------------+-------------------------+                 |
|                          |                                          |
+--------------------------|------------------------------------------+
                           |
+--------------------------|------------------------------------------+
|                          v                                          |
|  业务层                                                              |
|  +-----------------------+-----------------------+                 |
|  |                                                 |                 |
|  |  +----------------+      +----------------+   |                 |
|  |  | ImageAnalyzer  +----->+ SmartRouter    |   |                 |
|  |  |   (F022)       |      |   (F021)       |   |                 |
|  |  +----------------+      +--------+-------+   |                 |
|  |                                   |            |                 |
|  |                          +--------v-------+   |                 |
|  |                          | PromptBuilder  |   |                 |
|  |                          +--------+-------+   |                 |
|  |                                   |            |                 |
|  |  +--------------------------------v--------+   |                 |
|  |  |      TaskController (F028)               |   |                 |
|  |  |  - 状态机驱动                             |   |                 |
|  |  |  - 单/批量调度                            |   |                 |
|  |  |  - 并发控制                               |   |                 |
|  |  +----------------------------------------+   |                 |
|  |                                                 |                 |
|  +-----------------------+-------------------------+                 |
|                          |                                          |
+--------------------------|------------------------------------------+
                           |
+--------------------------|------------------------------------------+
|                          v                                          |
|  表现层                                                              |
|  +-----------------------+-----------------------+                 |
|  |                                                 |                 |
|  |  +----------------+      +----------------+   |                 |
|  |  |   MCP Server   |      |  CLI Interface |   |                 |
|  |  |   (P1/F026)    |      |  (P0/Direct)   |   |                 |
|  |  +----------------+      +----------------+   |                 |
|  |                                                 |                 |
|  +-------------------------------------------------+                 |
|                                                                     |
+====================================================================+

图例: -----> 依赖方向（箭头指向被依赖方）
      上层模块依赖下层模块，同层模块间尽量无依赖
```

### 4.2 简化依赖矩阵

| 依赖方 \ 被依赖方 | ConfigManager | AuthManager | ErrorHandler | BaseClient | GeminiService | VideoGenerator | WorkspaceClient | ImageAnalyzer | SmartRouter | PromptBuilder |
|:----------------:|:-------------:|:-----------:|:------------:|:----------:|:-------------:|:--------------:|:---------------:|:-------------:|:-----------:|:-------------:|
| **ConfigManager** | — | — | — | — | — | — | — | — | — | — |
| **AuthManager** | ✓ | — | — | — | — | — | — | — | — | — |
| **ErrorHandler** | ✓ | — | — | — | — | — | — | — | — | — |
| **BaseClient** | ✓ | ✓ | ✓ | — | — | — | — | — | — | — |
| **GeminiService** | ✓ | ✓ | ✓ | ✓ | — | — | — | — | — | — |
| **VideoGenerator** | ✓ | ✓ | ✓ | ✓ | — | — | — | — | — | — |
| **WorkspaceClient** | ✓ | ✓ | ✓ | ✓ | — | — | — | — | — | — |
| **ImageAnalyzer** | ✓ | — | ✓ | — | ✓ | — | — | — | — | — |
| **SmartRouter** | ✓ | — | ✓ | — | — | — | ✓ | — | — | — |
| **PromptBuilder** | — | — | ✓ | — | ✓ | — | — | — | — | — |
| **TaskController** | ✓ | — | ✓ | — | — | ✓ | ✓ | ✓ | ✓ | ✓ |
| **MCP/CLI** | ✓ | — | — | — | — | — | — | — | — | — | — |

> **设计原则**: 业务层模块只依赖所需的最小服务集合，禁止循环依赖。

---

## 5. 数据流设计

### 5.1 单产品处理流程

```
+====================================================================+
|                    单产品处理数据流图                                 |
+====================================================================+
|                                                                     |
|  [输入] 产品图片 + 用户描述                                          |
|     |                                                               |
|     v                                                               |
|  +--+-----------+  ImageAnalysisResult   +--------------------+   |
|  | 图片分析      |----------------------->|                    |   |
|  | ImageAnalyzer|  (4维度标签集合)        |   智能路由          |   |
|  |  ~5s         |                         |   SmartRouter      |   |
|  +--+-----------+                         |   ~1s              |   |
|     |                                     +--+-------------+---+   |
|     |                                        |             |        |
|     |                RouteMatchResult        |             |        |
|     |                (Top N推荐)             v             v        |
|     |                                     +--+----+   +---+----+   |
|     |                                     |Gem #1 |   |Gem #2 |   |
|     |                                     |(Top1) |   |(Top2) |   |
|     |                                     +---+---+   +---+----+   |
|     |                                         |           |         |
|     |         PromptResult                    v           v         |
|     +<----------------------------------+--+--+-----------+--+      |
|     |      (视频提示词)                  |  多Gem并行提示词生成  |      |
|     |                                    |  PromptBuilder       |      |
|     |                                    |  ~10s                |      |
|     |                                    +--------------------+      |
|     v                                                               |
|  +--+-----------+  VideoGenerationRequest  +--------------------+   |
|  | 视频生成      |----------------------->|                    |   |
|  | VideoGenerator|                        |   统一视频生成      |   |
|  |  ~120s       |                         |   UnifiedVideoGen  |   |
|  +--+-----------+  VideoGenerationResult  |   (含降级重试)      |   |
|     |<----------------------------------- |                    |   |
|     |         (视频URL/状态/成本)         +--------------------+   |
|     v                                                               |
|  +--+-----------+                                                   |
|  | 视频下载      |                                                   |
|  | Download      |  保存到本地 + 上传Drive                            |
|  |  ~10s         |                                                   |
|  +--+-----------+                                                   |
|     |                                                               |
|     v                                                               |
|  +--+-----------+                                                   |
|  | 状态更新      |  更新task-queue表 + 发送通知邮件                      |
|  | Workspace IO  |                                                   |
|  |  ~2s          |                                                   |
|  +--+-----------+                                                   |
|     |                                                               |
|     v                                                               |
|  [输出] TaskRecord (完整任务记录)                                     |
|                                                                     |
|  总耗时: ~150-180秒 (主要耗时在视频生成: 120秒)                        |
+====================================================================+
```

### 5.2 批量处理流程

```
+====================================================================+
|                    批量处理数据流图                                   |
+====================================================================+
|                                                                     |
|  [输入] 产品图片列表 [img1, img2, img3, img4, img5, ...]             |
|     |                                                               |
|     v                                                               |
|  +--+-----------+                                                   |
|  | 批量初始化    |  创建BatchTaskSummary、初始化Sheets行                |
|  |  ~1s          |                                                   |
|  +--+-----------+                                                   |
|     |                                                               |
|     v                                                               |
|  +================================================================+ |
|  | 并发处理池 (信号量控制，默认max_concurrency=3)                    | |
|  |                                                                 | |
|  |  +-------------+  +-------------+  +-------------+             | |
|  |  |  Slot #1    |  |  Slot #2    |  |  Slot #3    |             | |
|  |  |             |  |             |  |             |             | |
|  |  |  img1       |  |  img2       |  |  img3       |  <- 第一批  | |
|  |  |  ├→分析     |  |  ├→分析     |  |  ├→分析     |             | |
|  |  |  ├→路由     |  |  ├→路由     |  |  ├→路由     |             | |
|  |  |  ├→提示词   |  |  ├→提示词   |  |  ├→提示词   |             | |
|  |  |  └→视频生成 |  |  └→视频生成 |  |  └→视频生成 |  ~120s      | |
|  |  |             |  |             |  |             |             | |
|  |  +-------------+  +-------------+  +-------------+             | |
|  |         |                |                |                     | |
|  |         v                v                v                     | |
|  |  +-------------+  +-------------+  +-------------+             | |
|  |  |  Slot #1    |  |  Slot #2    |  |  Slot #3    |             | |
|  |  |  img4       |  |  img5       |  |  img6       |  <- 第二批  | |
|  |  |  (同上流程)  |  |  (同上流程)  |  |  (同上流程)  |             | |
|  |  +-------------+  +-------------+  +-------------+             | |
|  |                                                                 | |
|  |  ... 持续直到所有图片处理完成 ...                                 | |
|  |                                                                 | |
|  +================================================================+ |
|     |                                                               |
|     |  每个任务完成时更新TaskRecord到Sheets                           |
|     v                                                               |
|  +--+-----------+                                                   |
|  | 批量汇总      |  统计: 成功数/失败数/总成本/平均耗时/Gem分布/模型分布  |
|  |  ~2s          |                                                   |
|  +--+-----------+                                                   |
|     |                                                               |
|     v                                                               |
|  +--+-----------+                                                   |
|  | 交付          |  上传汇总到Sheets + 发送Gmail通知                   |
|  |  ~5s          |                                                   |
|  +--+-----------+                                                   |
|     |                                                               |
|     v                                                               |
|  [输出] BatchTaskSummary + 所有TaskRecord列表                        |
|                                                                     |
|  10张图片总耗时估算: ~150 + (10/3 * 120) ≈ 550秒 ≈ 9分钟             |
|  (第一批等待最长任务 + 后续批次流水线执行)                              |
+====================================================================+
```

### 5.3 批量处理流水线时序图

```
+====================================================================+
|              批量处理时序图 (5张图片, 并发度=3)                       |
+====================================================================+
|                                                                     |
|  Time →  0s    5s    6s    16s   136s  137s  141s  261s  262s     |
|   │                                                                 |
|   │  img1 [分析][路由][提示词][====视频生成====][下载][上传]         |
|   │        |<- 5s ->|<-1s->|<-10s->|<--- 120s --->|<-2s->|         |
|   │        └────────────── 第一批 Slot #1 ─────────────────┘         |
|   │                                                                 |
|   │  img2 [分析][路由][提示词][====视频生成====][下载][上传]         |
|   │        |<- 5s ->|<-1s->|<-10s->|<--- 120s --->|<-2s->|         |
|   │        └────────────── 第一批 Slot #2 ─────────────────┘         |
|   │                                                                 |
|   │  img3 [分析][路由][提示词][====视频生成====][下载][上传]         |
|   │        |<- 5s ->|<-1s->|<-10s->|<--- 120s --->|<-2s->|         |
|   │        └────────────── 第一批 Slot #3 ─────────────────┘         |
|   │                                                                 |
|   │  img4                          [分析][路由][提示词][====视频====]|
|   │                                |<- 5s ->|<-1s->|<-10s->|< 120s >|
|   │                                └────────── 第二批 Slot #1 ─────┘|
|   │                                                                 |
|   │  img5                          [分析][路由][提示词][====视频====]|
|   │                                |<- 5s ->|<-1s->|<-10s->|< 120s >|
|   │                                └────────── 第二批 Slot #2 ─────┘|
|   │                                                                 |
|   │  [汇总]                                              [汇总写入]   |
|   │                                                      [邮件通知]   |
|   │                                                                 |
|   │  关键路径: img1分析(5s) + img1视频(120s) + img4视频(120s)        |
|   │            = 245秒 ≈ 4分5秒 (5张图片)                            |
|   │                                                                 |
|   │  优化点: 分析阶段可以流水线化（视频生成时并行分析下批图片）          |
|   │          优化后: 120s(首视频) + max(16s, 120s) + 2s ≈ 138s       |
|   │          即约 2分18秒                                             |
|   │                                                                 |
+====================================================================+
```

---

## 6. 状态机设计

### 6.1 任务状态机（7状态）

```
+====================================================================+
|                      任务状态机 (7状态)                              |
+====================================================================+
|                                                                     |
|                         +-----------+                               |
|                         |  PENDING  |                               |
|                         |  (待处理)  |                               |
|                         +----+------+                               |
|                              |                                      |
|                              | start()                              |
|                              v                                      |
|  +-----------+        +------+------+                               |
|  |  CANCELLED|<-------|  ANALYZING  |                               |
|  |  (已取消)  | cancel |  (分析中)   |                               |
|  +-----------+        +------+------+                               |
|                              |                                      |
|                              | analyze_complete()                   |
|                              v                                      |
|                       +------+------+                               |
|                       |   ROUTING   |                               |
|                       |  (路由匹配)  |                               |
|                       +------+------+                               |
|                              |                                      |
|                              | route_complete()                     |
|                              v                                      |
|                       +------+------+                               |
|                       |  PROMPTING  |                               |
|                       | (提示词生成) |                               |
|                       +------+------+                               |
|                              |                                      |
|                              | prompt_complete()                    |
|                              v                                      |
|                       +------+------+      +-----------+           |
|                       |  GENERATING |----->|   FAILED  |           |
|                       |  (视频生成)  | fail |  (失败)   |           |
|                       +------+------+      +-----+-----+           |
|                              |                    |                 |
|                              | generate_complete()| retry()         |
|                              v                    |                 |
|                       +------+------+             |                 |
|                       |  DOWNLOADING|<------------+                 |
|                       | (下载上传)   |                               |
|                       +------+------+                               |
|                              |                                      |
|                              | download_complete()                   |
|                              v                                      |
|                       +------+------+      +-----------+           |
|                       |  COMPLETED  |----->|  NOTIFYING|           |
|                       |  (已完成)   |      | (通知中)   |           |
|                       +-----------+      +-----------+           |
|                                                                     |
|  状态转移矩阵:                                                      |
|  +--------------+---+---+---+---+---+---+---+                     |
|  | 当前 \ 下一  |PEN|ANA|ROU|PRO|GEN|DOW|COM|FAI|CAN|             |
|  +--------------+---+---+---+---+---+---+---+---+                 |
|  | PENDING      |   | ✓ |   |   |   |   |   |   | ✓ |             |
|  | ANALYZING    |   |   | ✓ |   |   |   |   | ✓ | ✓ |             |
|  | ROUTING      |   |   |   | ✓ |   |   |   | ✓ | ✓ |             |
|  | PROMPTING    |   |   |   |   | ✓ |   |   | ✓ | ✓ |             |
|  | GENERATING   |   |   |   |   |   | ✓ |   | ✓ | ✓ |             |
|  | DOWNLOADING  |   |   |   |   |   |   | ✓ |   |   |             |
|  | COMPLETED    |   |   |   |   |   |   |   |   |   |             |
|  | FAILED       |   | ✓ |   |   |   |   |   |   |   |             |
|  | CANCELLED    |   |   |   |   |   |   |   |   |   |             |
|  +--------------+---+---+---+---+---+---+---+---+                 |
|                                                                     |
|  说明: FAILED状态可通过retry()回到ANALYZING（重新执行全流程）         |
|        或从GENERATING重试（仅重新生成视频）                          |
|        CANCELLED为终态，不可恢复                                     |
+====================================================================+
```

### 6.2 状态流转触发条件

| 状态转移 | 触发条件 | 执行动作 | Sheets更新 |
|----------|----------|----------|------------|
| PENDING → ANALYZING | 任务开始处理 | 创建TaskRecord，写入Sheets | status=analyzing |
| ANALYZING → ROUTING | VLM分析完成 | 解析4维度结果 | analysis_json=结果 |
| ROUTING → PROMPTING | 路由匹配完成 | 记录选中Gem | selected_gem_id=xxx |
| PROMPTING → GENERATING | 提示词生成完成 | 记录提示词 | generated_prompt=xxx |
| GENERATING → DOWNLOADING | 视频生成完成 | 获取video_url | task_api_id=xxx |
| GENERATING → FAILED | 视频生成失败 | 记录错误信息 | error_code/message=xxx |
| DOWNLOADING → COMPLETED | 上传Drive完成 | 更新所有URL | drive_file_id=xxx |
| FAILED → ANALYZING | 用户触发重试 | 增加retry_count | retry_count+=1 |
| 任何 → CANCELLED | 用户取消 | 清理资源 | status=cancelled |

---

## 7. 技术栈总览

### 7.1 核心依赖

| 类别 | 技术/库 | 版本 | 用途 |
|------|---------|------|------|
| **语言** | Python | 3.11+ | 主开发语言 |
| **异步框架** | asyncio / aiohttp | 内置 / 3.9+ | 异步I/O和HTTP客户端 |
| **数据验证** | Pydantic | 2.0+ | 数据模型和配置验证 |
| **认证** | google-auth | 2.0+ | Service Account认证 |
| **Google API** | google-api-python-client | 2.0+ | Sheets/Drive/Gmail API |
| **重试** | tenacity | 8.0+ | 重试策略和指数退避 |
| **日志** | loguru | 0.7+ | 结构化日志 |
| **环境配置** | python-dotenv | 1.0+ | .env文件加载 |
| **Gemini** | gemini-webapi | latest | Gemini API调用（独立进程） |

### 7.2 开发与运维工具

| 类别 | 技术/工具 | 用途 |
|------|-----------|------|
| **测试** | pytest / pytest-asyncio / pytest-cov | 单元/集成测试 |
| **代码质量** | ruff / mypy | 代码格式和类型检查 |
| **容器化** | Docker / Docker Compose | 应用容器化部署 |
| **监控** | Prometheus / Grafana | 指标收集和可视化 |
| **CI/CD** | GitHub Actions | 自动化构建和测试 |

### 7.3 外部服务

| 服务 | 用途 | 认证方式 | 调用频率 |
|------|------|----------|----------|
| Gemini Web API | 图片分析、提示词生成 | Cookie | ~10次/产品 |
| 可灵 Kling 3.0 | 视频生成 | API Key | 1次/产品 |
| Google Veo 3.1 | 视频生成 | API Key / Vertex AI | 1次/产品 |
| 即梦 Seedance 2.0 | 视频生成 | API Key | 1次/产品 |
| Vidu Q3 | 视频生成（P1） | API Key | 1次/产品 |
| Runway Gen-4 | 视频生成（P1） | API Key | 1次/产品 |
| Google Sheets | 配置存储、任务追踪 | Service Account | 多次/产品 |
| Google Drive | 视频文件存储 | Service Account | 1次/产品 |
| Gmail | 通知邮件 | Service Account | 1次/批次 |

### 7.4 技术栈选型理由

```
+====================================================================+
|                    技术选型决策树                                    |
+====================================================================+
|                                                                     |
|  为什么选择 Python 3.11+ ?                                          |
|  ├── 所有Agent项目均为Python生态                                     |
|  ├── asyncio + aiohttp 成熟稳定                                      |
|  ├── Pydantic 2.0 性能优秀                                           |
|  └── gemini-webapi 为Python库                                        |
|                                                                     |
|  为什么选择 Pydantic 而非 dataclass ?                                |
|  ├── 需要JSON序列化/反序列化（Sheets交互）                            |
|  ├── 需要字段验证和类型检查                                           |
|  ├── 需要配置管理（Pydantic Settings）                                |
|  └── 生态成熟，与FastAPI等框架兼容                                    |
|                                                                     |
|  为什么选择适配器模式而非SDK集成 ?                                    |
|  ├── 6家提供商无统一Python SDK                                       |
|  ├── REST API为最小公倍数，最稳定                                     |
|  ├── 隔离各提供商接口变动的影响                                       |
|  └── 新增模型只需新增适配器文件，零改动现有代码                         |
|                                                                     |
|  为什么选择 Google Sheets 作为配置存储 ?                               |
|  ├── 用户已在使用Sheets管理Gems                                      |
|  ├── 非技术人员可以直接编辑配置                                        |
|  ├── 天然支持版本历史（Sheets自带）                                    |
|  └── 与Workspace生态无缝集成                                         |
|                                                                     |
|  为什么 gemini-webapi 需要独立进程隔离 ?                               |
|  ├── AGPL-3.0许可证与商业闭源不兼容                                   |
|  ├── 独立进程通过HTTP通信，避免license污染                             |
|  └── 可独立升级和替换                                                |
|                                                                     |
+====================================================================+
```

---

## 8. 关键设计决策（ADR）

### ADR-001: 采用适配器模式统一6家视频模型

| 属性 | 内容 |
|------|------|
| **状态** | 已接受 |
| **背景** | 6家视频模型（Kling/Veo/Seedance/Vidu/Runway/Pika）接口各异，无统一SDK |
| **决策** | 定义BaseVideoAdapter抽象基类，每家模型实现适配器，通过AdapterRegistry自动发现和注册 |
| **替代方案** | 直接调用各API / 使用第三方聚合SDK（无成熟方案） |
| **影响** | 正面: 新增模型1人天接入，接口变动只改适配器。负面: 需要维护适配器代码 |
| **风险** | API变动导致适配器失效 →  mitigate: 每日健康检查 + 版本锁定 |

### ADR-002: Google Sheets 作为配置和任务存储

| 属性 | 内容 |
|------|------|
| **状态** | 已接受 |
| **背景** | Gems配置需要非技术人员可编辑，任务状态需要持久化和可视化 |
| **决策** | gem-metadata表存储Gem配置，task-queue表存储任务记录 |
| **替代方案** | PostgreSQL + Admin UI / Firebase / Airtable |
| **影响** | 正面: 用户零学习成本，天然版本历史。负面: 并发写入有限制，无事务支持 |
| **风险** | Sheets API限流 → mitigate: 批量操作(batchUpdate) + 内存缓存 + 指数退避 |

### ADR-003: gemini-webapi AGPL许可证隔离

| 属性 | 内容 |
|------|------|
| **状态** | 已接受 |
| **背景** | gemini-webapi使用AGPL-3.0许可证，直接引入会导致衍生作品也必须AGPL |
| **决策** | 将gemini-webapi部署为独立服务（AGPL容器），主项目通过HTTP/gRPC调用 |
| **替代方案** | 不使用gemini-webapi / 使用Gemini官方API（有成本） |
| **影响** | 正面: 商业闭源不受影响。负面: 多一个服务需要部署和维护 |
| **风险** | 服务间通信延迟 → mitigate: 同机部署 + Unix Socket连接 |

### ADR-004: 三级降级链保障可用性

| 属性 | 内容 |
|------|------|
| **状态** | 已接受 |
| **背景** | 视频生成依赖外部API，任何API故障都会导致系统不可用 |
| **决策** | 实现三级降级：模型级(同Provider不同模型) → Provider级(切换Provider) → 服务级(返回缓存) |
| **替代方案** | 单级重试 / 完全放弃 |
| **影响** | 正面: 可用性>99%。负面: 降级可能降低质量，需要监控 |
| **风险** | 多Provider同时故障 → mitigate: 服务级兜底 + 告警通知 |

### ADR-005: Cookie认证三层恢复机制

| 属性 | 内容 |
|------|------|
| **状态** | 已接受 |
| **背景** | Gemini使用Cookie认证，Cookie可能过期或失效 |
| **决策** | 三层恢复：自动刷新(600s间隔) → browser-cookie3自动加载 → 多层Cookie备选 |
| **替代方案** | 仅使用手动更新 / 使用Gemini官方API（有成本且限制更多） |
| **影响** | 正面: 自动恢复，减少人工干预。负面: 依赖本地浏览器Cookie |
| **风险** | 所有Cookie同时失效 → mitigate: 健康检查 + 告警通知管理员 |

### ADR-006: 信号量并发控制而非队列

| 属性 | 内容 |
|------|------|
| **状态** | 已接受 |
| **背景** | 批量处理需要控制并发，避免触发API限流 |
| **决策** | 使用asyncio.Semaphore实现内存级并发控制，而非Redis/RabbitMQ队列 |
| **替代方案** | Celery + Redis / RabbitMQ / AWS SQS |
| **影响** | 正面: 零额外依赖，实现简单。负面: 进程重启丢失排队任务，无分布式能力 |
| **风险** | 单机并发上限 → mitigate: 当前需求规模小（3-5并发足够），未来可迁移到Celery |

### ADR-007: 智能路由权重配置化

| 属性 | 内容 |
|------|------|
| **状态** | 已接受 |
| **背景** | 需要平衡匹配准确度与灵活性 |
| **决策** | 匹配权重通过config.yaml配置（默认: 品类30%/场景20%/人群15%/价格10%/风格10%/历史10%/其他5%），支持运行时调整 |
| **替代方案** | 硬编码权重 / 机器学习动态权重 |
| **影响** | 正面: 灵活调整，无需代码变更。负面: 需要人工调优 |
| **风险** | 权重设置不合理 → mitigate: 自学习机制(P1)根据效果反馈自动调整 |

---

## 9. 部署架构

### 9.1 Docker Compose 部署（推荐）

```yaml
# docker-compose.yml
version: '3.8'

services:
  # 主应用服务 (商业闭源)
  video-skill:
    build: .
    container_name: video-batch-skill
    restart: unless-stopped
    environment:
      - GEMINI_SECURE_1PSID=${GEMINI_SECURE_1PSID}
      - GEMINI_SECURE_1PSIDTS=${GEMINI_SECURE_1PSIDTS}
      - GWS_SERVICE_ACCOUNT_FILE=/secrets/service-account.json
      - KLING_API_KEY=${KLING_API_KEY}
      - VEO_API_KEY=${VEO_API_KEY}
      - SEEDANCE_API_KEY=${SEEDANCE_API_KEY}
      - LOG_LEVEL=INFO
      - MAX_CONCURRENCY=3
    volumes:
      - ./secrets:/secrets:ro
      - ./output:/app/output
      - ./config:/app/config
    ports:
      - "8000:8000"  # MCP Server / HTTP API
    depends_on:
      - gemini-service
    networks:
      - skill-network

  # Gemini服务 (AGPL-3.0隔离)
  gemini-service:
    build: ./gemini-service
    container_name: gemini-agpl-service
    restart: unless-stopped
    environment:
      - GEMINI_SECURE_1PSID=${GEMINI_SECURE_1PSID}
      - GEMINI_SECURE_1PSIDTS=${GEMINI_SECURE_1PSIDTS}
    volumes:
      - gemini-cookies:/root/.local/share  # Cookie持久化
    expose:
      - "8000"  # 仅内网暴露
    networks:
      - skill-network

  # Prometheus 监控 (可选)
  prometheus:
    image: prom/prometheus:latest
    container_name: skill-prometheus
    volumes:
      - ./monitoring/prometheus.yml:/etc/prometheus/prometheus.yml:ro
      - prometheus-data:/prometheus
    ports:
      - "9090:9090"
    profiles: ["monitoring"]

  # Grafana 可视化 (可选)
  grafana:
    image: grafana/grafana:latest
    container_name: skill-grafana
    volumes:
      - grafana-data:/var/lib/grafana
      - ./monitoring/dashboards:/etc/grafana/provisioning/dashboards:ro
    ports:
      - "3000:3000"
    profiles: ["monitoring"]

networks:
  skill-network:
    driver: bridge

volumes:
  gemini-cookies:
  prometheus-data:
  grafana-data:
```

### 9.2 目录结构

```
video-batch-skill/
├── README.md
├── docker-compose.yml
├── Dockerfile
├── pyproject.toml
├── .env.example
├── config/
│   └── config.yaml                 # 主配置文件
├── src/
│   ├── __init__.py
│   ├── main.py                     # 入口文件（CLI模式）
│   ├── mcp_server.py               # MCP Server入口
│   ├── core/                       # 业务层
│   │   ├── __init__.py
│   │   ├── task_controller.py      # M6: 任务控制器 (F028)
│   │   ├── smart_router.py         # M3: 智能路由 (F021)
│   │   ├── image_analyzer.py       # M2: 图片分析 (F022)
│   │   └── prompt_builder.py       # M4: 提示词生成
│   ├── services/                   # 服务层
│   │   ├── __init__.py
│   │   ├── gemini/                 # Gemini服务
│   │   │   ├── __init__.py
│   │   │   ├── base_provider.py    # F013: BaseGeminiProvider
│   │   │   ├── webapi_adapter.py   # F014: WebapiAdapter
│   │   │   └── health_checker.py   # F027: Cookie健康检查
│   │   ├── video/                  # 视频生成服务
│   │   │   ├── __init__.py
│   │   │   ├── unified_generator.py # F020: UnifiedVideoGenerator
│   │   │   ├── adapter_registry.py  # 适配器注册表
│   │   │   ├── base_adapter.py      # 适配器基类
│   │   │   ├── kling_adapter.py     # F017: Kling适配器
│   │   │   ├── veo_adapter.py       # F018: Veo适配器
│   │   │   ├── seedance_adapter.py  # F019: Seedance适配器
│   │   │   ├── vidu_adapter.py      # F023: Vidu适配器 (P1)
│   │   │   └── runway_adapter.py    # F024: Runway适配器 (P1)
│   │   └── workspace/              # Workspace服务
│   │       ├── __init__.py
│   │       ├── client.py            # F015: GWSClient
│   │       ├── sheets_io.py         # F007: Sheets序列化
│   │       └── drive_uploader.py    # Drive上传
│   ├── infrastructure/             # 基础设施层
│   │   ├── __init__.py
│   │   ├── config.py               # F008: ConfigManager
│   │   ├── auth.py                 # F001: AuthManager
│   │   ├── exceptions.py           # F003: 异常体系
│   │   ├── retry.py                # F004: RetryEngine
│   │   ├── failover.py             # F005: FailoverManager
│   │   ├── rate_limiter.py         # F010: RateLimiter
│   │   ├── base_client.py          # F009: BaseUnifiedClient
│   │   ├── poller.py               # F011: AsyncTaskPoller
│   │   ├── metrics.py              # F030: MetricsCollector
│   │   └── notifier.py             # F029: UserNotifier
│   └── models/                     # 数据模型层 (F006)
│       ├── __init__.py
│       ├── enums.py                # 所有枚举类型
│       ├── base.py                 # 基础混入类
│       ├── media.py                # ReferenceMedia
│       ├── gem_metadata.py         # GemMetadata + MatchDimensions
│       ├── image_analysis.py       # ImageAnalysisResult + 4维度
│       ├── route_match.py          # RouteMatchResult
│       ├── video_generation.py     # VideoGenerationRequest/Result
│       └── task_record.py          # TaskRecord + BatchTaskSummary
├── gemini-service/                 # AGPL隔离服务
│   ├── Dockerfile
│   ├── main.py
│   └── requirements.txt
├── tests/
│   ├── unit/                       # 单元测试
│   ├── integration/                # 集成测试
│   └── fixtures/                   # 测试数据
├── monitoring/
│   ├── prometheus.yml
│   └── dashboards/
└── secrets/                        # 凭证文件（不提交git）
    └── .gitkeep
```

### 9.3 环境变量清单

| 变量名 | 必填 | 说明 | 示例 |
|--------|------|------|------|
| `GEMINI_SECURE_1PSID` | ✅ | Gemini主Cookie | `g.a000...` |
| `GEMINI_SECURE_1PSIDTS` | ✅ | Gemini辅助Cookie | `bdch...` |
| `GWS_SERVICE_ACCOUNT_FILE` | ✅ | Service Account JSON路径 | `/secrets/sa.json` |
| `GWS_ADMIN_EMAIL` | ❌ | 域授权管理员邮箱 | `admin@example.com` |
| `KLING_API_KEY` | ✅ | 可灵API Key | `sk_...` |
| `VEO_API_KEY` | ✅ | Veo API Key | `AIza...` |
| `SEEDANCE_API_KEY` | ✅ | 即梦API Key | `sk-...` |
| `VIDU_API_KEY` | ❌ | Vidu API Key (P1) | `sk_...` |
| `RUNWAY_API_KEY` | ❌ | Runway API Key (P1) | `key_...` |
| `LOG_LEVEL` | ❌ | 日志级别 | `INFO` |
| `MAX_CONCURRENCY` | ❌ | 最大并发数 | `3` |
| `PROMETHEUS_PORT` | ❌ | 监控端口 | `9090` |

---

## 10. 接口规范摘要

### 10.1 模块间核心接口

```python
"""
模块间核心接口定义 — 所有模块通过以下接口交互
"""

from abc import ABC, abstractmethod
from typing import List, Optional
from models import *

# ========== M2: 图片分析模块接口 ==========

class IImageAnalyzer(ABC):
    @abstractmethod
    async def analyze(self, image: ReferenceMedia, 
                      user_description: Optional[str] = None) -> ImageAnalysisResult: ...
    
    @abstractmethod
    async def analyze_batch(self, images: List[ReferenceMedia]) -> List[ImageAnalysisResult]: ...

# ========== M3: 智能路由模块接口 ==========

class ISmartRouter(ABC):
    @abstractmethod
    async def route(self, analysis: ImageAnalysisResult, 
                    top_n: int = 3) -> RouteMatchResult: ...
    
    @abstractmethod
    async def record_feedback(self, gem_id: str, success: bool, 
                              match_score: float) -> None: ...

# ========== M4: 提示词生成模块接口 ==========

class IPromptBuilder(ABC):
    @abstractmethod
    async def build_prompt(self, gem: GemMetadata, 
                           analysis: ImageAnalysisResult,
                           image: ReferenceMedia) -> PromptResult: ...

# ========== M5: 视频生成模块接口 ==========

class IVideoGenerator(ABC):
    @abstractmethod
    async def generate(self, request: VideoGenerationRequest) -> VideoGenerationResult: ...
    
    @abstractmethod
    async def generate_batch(self, requests: List[VideoGenerationRequest],
                             config: BatchTaskConfig) -> List[VideoGenerationResult]: ...

class IVideoAdapter(ABC):
    @property
    @abstractmethod
    def provider(self) -> VideoProvider: ...
    
    @property
    @abstractmethod
    def capability(self) -> ProviderCapability: ...
    
    @abstractmethod
    async def submit(self, request: VideoGenerationRequest) -> str: ...
    
    @abstractmethod
    async def query(self, task_id: str) -> VideoGenerationResult: ...

# ========== M6: 任务管理模块接口 ==========

class ITaskController(ABC):
    @abstractmethod
    async def process_single(self, image: ReferenceMedia,
                             user_description: Optional[str] = None) -> TaskRecord: ...
    
    @abstractmethod
    async def process_batch(self, images: List[ReferenceMedia],
                            config: BatchTaskConfig) -> BatchTaskSummary: ...
    
    @abstractmethod
    async def get_progress(self, batch_id: str) -> dict: ...

# ========== M7: Workspace模块接口 ==========

class IWorkspaceClient(ABC):
    @abstractmethod
    async def read_gem_metadata(self) -> List[GemMetadata]: ...
    
    @abstractmethod
    async def write_task_record(self, record: TaskRecord) -> None: ...
    
    @abstractmethod
    async def upload_video(self, local_path: str) -> str: ...
    
    @abstractmethod
    async def send_notification(self, to: str, subject: str, body: str) -> None: ...
```

### 10.2 数据流转总结

| 流转阶段 | 输入数据 | 处理模块 | 输出数据 | 耗时估算 |
|----------|----------|----------|----------|----------|
| 图片分析 | ReferenceMedia | ImageAnalyzer | ImageAnalysisResult | ~5s |
| 智能路由 | ImageAnalysisResult | SmartRouter | RouteMatchResult | ~1s |
| 提示词生成 | GemMetadata + ImageAnalysisResult | PromptBuilder | PromptResult | ~10s |
| 视频生成 | VideoGenerationRequest | VideoGenerator | VideoGenerationResult | ~120s |
| 视频下载 | video_url | WorkspaceClient | local_path + drive_file_id | ~10s |
| 状态更新 | TaskRecord | WorkspaceClient | Sheets更新 | ~2s |

---

## 附录 A: 与Stage-2的映射关系

| Stage-2功能ID | Stage-2功能点 | 本架构对应模块 | 对应章节 |
|:-------------:|--------------|---------------|----------|
| F001 | 统一认证管理器 | infrastructure/auth.py | 3.2 M8 |
| F002 | 安全凭证存储 | infrastructure/auth.py | 3.2 M8 |
| F003 | 统一异常体系 | infrastructure/exceptions.py | 3.2 M8 |
| F004 | 智能重试引擎 | infrastructure/retry.py | 3.2 M8 |
| F005 | 统一降级管理器 | infrastructure/failover.py | 3.2 M8 |
| F006 | Pydantic核心数据模型 | models/ | 3.2 全部 |
| F007 | Sheets序列化层 | services/workspace/sheets_io.py | 3.2 M7 |
| F008 | 统一配置管理 | infrastructure/config.py | 3.2 M1 |
| F009 | BaseUnifiedClient | infrastructure/base_client.py | 3.2 M8 |
| F010 | 限流控制器 | infrastructure/rate_limiter.py | 3.2 M8 |
| F011 | 异步轮询框架 | infrastructure/poller.py | 3.2 M8 |
| F012 | gemini-webapi集成 | services/gemini/ | 3.2 M5-Gemini |
| F013 | BaseGeminiProvider | services/gemini/base_provider.py | 3.2 M5-Gemini |
| F014 | WebapiAdapter | services/gemini/webapi_adapter.py | 3.2 M5-Gemini |
| F015 | gws CLI适配 | services/workspace/client.py | 3.2 M7 |
| F016 | Service Account | infrastructure/auth.py | 3.2 M8 |
| F017 | Kling适配器 | services/video/kling_adapter.py | 3.2 M5 |
| F018 | Veo适配器 | services/video/veo_adapter.py | 3.2 M5 |
| F019 | Seedance适配器 | services/video/seedance_adapter.py | 3.2 M5 |
| F020 | UnifiedVideoGenerator | services/video/unified_generator.py | 3.2 M5 |
| F021 | SmartRouter | core/smart_router.py | 3.2 M3 |
| F022 | 图片分析维度 | core/image_analyzer.py + models/image_analysis.py | 3.2 M2 |
| F028 | 批量任务管理 | core/task_controller.py | 3.2 M6 |
| F029 | 用户通知 | infrastructure/notifier.py | 3.2 M8 |
| F030 | 监控指标 | infrastructure/metrics.py | 3.2 M8 |
| F031 | 成本监控 | infrastructure/metrics.py | 3.2 M8 |

---

## 附录 B: 术语表

| 术语 | 说明 |
|------|------|
| **Gem** | Gemini的自定义模板/技能，用于生成特定风格的提示词 |
| **VLM** | Vision Language Model，视觉语言模型，用于理解图片内容 |
| **适配器(Adapter)** | 将不同视频模型的异构接口统一为相同接口的设计模式 |
| **降级(Failover)** | 当主要服务不可用时，自动切换到备用服务的机制 |
| **熔断(Circuit Breaker)** | 连续失败后暂停调用的保护机制 |
| **信号量(Semaphore)** | 控制并发数量的同步原语 |
| **AGPL** | Affero General Public License，一种强copyleft开源许可证 |
| **Service Account** | Google Cloud的服务账号，用于服务器到服务器的API认证 |
| **P0/P1/P2/P3** | 优先级分级：P0必须实现，P1推荐实现，P2可选，P3长期观察 |

---

> **文档结束**
>
> 本文档为 300 Agent 融合项目 Stage-3 的核心产出，定义了带货视频批量生产Skill的完整系统架构。
> 下一步: 进入 Stage-4 详细设计阶段，基于本架构进行各模块的详细设计和代码实现。
>
> 生成时间: 2026-07
> 架构版本: v1.0
> 覆盖模块: 8大核心模块
> 设计决策: 7个ADR
> 总依赖项: 22个P0功能点全部覆盖
