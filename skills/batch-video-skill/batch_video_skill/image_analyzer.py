"""
image_analyzer.py — ImageAnalyzer 图片分析器

300 Agent 融合项目的图片深度分析模块，负责将产品图片转化为
结构化特征向量 (ImageFeatures)，供下游 SmartRouter 匹配引擎使用。

核心特性:
- 4 维度独立并行分析 (产品/人群/场景/风格)
- Gemini Vision API 调用封装
- LRU 缓存 + 全量结果缓存
- 降级与错误处理

用法:
    analyzer = ImageAnalyzer(api_key="your-key")
    response = analyzer.analyze("https://example.com/product.jpg")
    features = response.features  # 可直接传入 SmartRouter
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import time
from abc import ABC, abstractmethod
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from enum import Enum
from io import BytesIO
from pathlib import Path
from typing import (
    Any, Dict, List, Optional, Type, Union,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# 枚举定义
# ═══════════════════════════════════════════════════════════

class ProductCategory(str, Enum):
    """产品品类 — 一级类目"""
    SKINCARE = "skincare"
    MAKEUP = "makeup"
    FRAGRANCE = "fragrance"
    PERSONAL_CARE = "personal_care"
    WOMENS_CLOTHING = "womens_clothing"
    MENS_CLOTHING = "mens_clothing"
    SHOES = "shoes"
    BAGS = "bags"
    ACCESSORIES = "accessories"
    PHONE = "phone"
    COMPUTER = "computer"
    DIGITAL_ACCESSORY = "digital_accessory"
    HOME_APPLIANCE = "home_appliance"
    SNACK = "snack"
    BEVERAGE = "beverage"
    FRESH_FOOD = "fresh_food"
    HEALTH_FOOD = "health_food"
    FURNITURE = "furniture"
    HOME_DECOR = "home_decor"
    DAILY_NECESSITIES = "daily_necessities"
    MATERNITY_BABY = "maternity_baby"
    PET = "pet"
    SPORTS = "sports"
    OUTDOOR = "outdoor"
    JEWELRY = "jewelry"
    WATCH = "watch"


class PriceTier(str, Enum):
    """价格带 — 5档分级"""
    BUDGET = "budget"
    AFFORDABLE = "affordable"
    MID_RANGE = "mid_range"
    PREMIUM = "premium"
    LUXURY = "luxury"


class BrandTier(str, Enum):
    """品牌调性 — 5档分级"""
    MASS = "mass"
    MID_MARKET = "mid_market"
    PREMIUM = "premium"
    LUXURY = "luxury"
    D2C = "d2c"


class SellingPoint(str, Enum):
    """产品卖点特征 — 13种标签"""
    NATURAL_INGREDIENTS = "natural_ingredients"
    HIGH_TECH = "high_tech"
    DURABLE = "durable"
    LIGHTWEIGHT = "lightweight"
    COST_EFFECTIVE = "cost_effective"
    FLASH_SALE = "flash_sale"
    BUNDLE_DEAL = "bundle_deal"
    PREMIUM_PACKAGING = "premium_packaging"
    LIMITED_EDITION = "limited_edition"
    GIFT_READY = "gift_ready"
    TRENDING = "trending"
    CELEBRITY_ENDORSED = "celebrity_endorsed"
    SUSTAINABLE = "sustainable"


class TargetGender(str, Enum):
    """目标性别 — 4种"""
    MALE = "male"
    FEMALE = "female"
    UNISEX = "unisex"
    NOT_SPECIFIED = "not_specified"


class TargetAgeGroup(str, Enum):
    """目标年龄层 — 6档"""
    GEN_Z = "gen_z"
    GEN_Y = "gen_y"
    GEN_X = "gen_x"
    SILVER = "silver"
    SENIOR = "senior"
    ALL_AGES = "all_ages"


class SpendingPower(str, Enum):
    """消费能力 — 4档"""
    ECONOMY = "economy"
    MIDDLE = "middle"
    AFFLUENT = "affluent"
    ULTRA_HIGH = "ultra_high"


class PersonaTag(str, Enum):
    """人群画像标签 — 12种"""
    MINIMALIST = "minimalist"
    FASHION_FORWARD = "fashion_forward"
    TECH_ENTHUSIAST = "tech_enthusiast"
    HEALTH_CONSCIOUS = "health_conscious"
    BARGAIN_HUNTER = "bargain_hunter"
    QUALITY_FIRST = "quality_first"
    BRAND_LOYAL = "brand_loyal"
    EARLY_ADOPTER = "early_adopter"
    OFFICE_WORKER = "office_worker"
    STUDENT = "student"
    PARENT = "parent"
    SENIOR_BUYER = "senior_buyer"


class MarketingScene(str, Enum):
    """营销场景 — 8种"""
    NEW_LAUNCH = "new_launch"
    FLASH_SALE = "flash_sale"
    SEASONAL_PROMO = "seasonal_promo"
    BRAND_BUILDING = "brand_building"
    SOCIAL_PROOF = "social_proof"
    HOLIDAY_GIFT = "holiday_gift"
    CLEARANCE = "clearance"
    MEMBERSHIP = "membership"


class UsageScene(str, Enum):
    """使用场景 — 9种"""
    DAILY = "daily"
    WORK = "work"
    OUTDOOR = "outdoor"
    TRAVEL = "travel"
    SPORTS = "sports"
    PARTY = "party"
    HOME = "home"
    GIFT = "gift"
    SPECIAL_OCCASION = "special_occasion"


class TargetPlatform(str, Enum):
    """目标平台 — 8种"""
    DOUYIN = "douyin"
    XIAOHONGSHU = "xiaohongshu"
    TAOBAO = "taobao"
    KUAISHOU = "kuaishou"
    WECHAT = "wechat"
    TIKTOK = "tiktok"
    INSTAGRAM = "instagram"
    UNIVERSAL = "universal"


class VideoStyle(str, Enum):
    """视频风格 — 10种"""
    FAST_PACED = "fast_paced"
    SLOW_LIVING = "slow_living"
    STORY_DRIVEN = "story_driven"
    PRODUCT_SHOWCASE = "product_showcase"
    UNBOXING = "unboxing"
    TUTORIAL = "tutorial"
    LIFESTYLE = "lifestyle"
    DRAMATIC = "dramatic"
    MINIMAL = "minimal"
    UGC_STYLE = "ugc_style"


class VisualTone(str, Enum):
    """画面调性 — 10种"""
    BRIGHT = "bright"
    DARK_MOODY = "dark_moody"
    WARM = "warm"
    COOL = "cool"
    PASTEL = "pastel"
    MONOCHROME = "monochrome"
    VIBRANT = "vibrant"
    PREMIUM = "premium"
    NATURAL = "natural"
    FUTURISTIC = "futuristic"


class ImageSourceType(str, Enum):
    """图片来源类型"""
    URL = "url"
    LOCAL_PATH = "local_path"
    BASE64 = "base64"
    BYTES = "bytes"
    UPLOADED_FILE = "uploaded_file"


# ═══════════════════════════════════════════════════════════
# Pydantic 模型
# ═══════════════════════════════════════════════════════════

from pydantic import BaseModel, Field  # noqa: E402


class ProductAnalysisResult(BaseModel):
    """产品维度分析结果"""
    category: Optional[ProductCategory] = Field(default=None)
    product_type: Optional[str] = Field(default=None)
    price_tier: Optional[PriceTier] = Field(default=None)
    brand_tier: Optional[BrandTier] = Field(default=None)
    selling_points: List[SellingPoint] = Field(default_factory=list)
    confidence: Dict[str, float] = Field(default_factory=dict)
    raw_description: str = Field(default="")


class AudienceAnalysisResult(BaseModel):
    """人群维度分析结果"""
    target_gender: Optional[TargetGender] = Field(default=None)
    target_age: Optional[TargetAgeGroup] = Field(default=None)
    spending_power: Optional[SpendingPower] = Field(default=None)
    persona_tags: List[PersonaTag] = Field(default_factory=list)
    confidence: Dict[str, float] = Field(default_factory=dict)
    reasoning: str = Field(default="")


class SceneAnalysisResult(BaseModel):
    """场景维度分析结果"""
    marketing_scene: Optional[MarketingScene] = Field(default=None)
    usage_scenes: List[UsageScene] = Field(default_factory=list)
    target_platform: Optional[TargetPlatform] = Field(default=None)
    confidence: Dict[str, float] = Field(default_factory=dict)
    scene_description: str = Field(default="")


class StyleAnalysisResult(BaseModel):
    """风格维度分析结果"""
    video_style: Optional[VideoStyle] = Field(default=None)
    visual_tone: Optional[VisualTone] = Field(default=None)
    confidence: Dict[str, float] = Field(default_factory=dict)
    visual_description: str = Field(default="")
    dominant_colors: List[str] = Field(default_factory=list)
    composition_features: List[str] = Field(default_factory=list)


class DimensionResults(BaseModel):
    """四维度分析结果聚合"""
    product: Optional[ProductAnalysisResult] = None
    audience: Optional[AudienceAnalysisResult] = None
    scene: Optional[SceneAnalysisResult] = None
    style: Optional[StyleAnalysisResult] = None
    analyzed_dimensions: List[str] = Field(default_factory=list)
    failed_dimensions: List[str] = Field(default_factory=list)
    total_confidence: float = Field(default=0.0)


class ImageFeatures(BaseModel):
    """
    标准化图片特征向量
    此模型是 ImageAnalyzer 的核心输出，字段命名和类型
    严格兼容 SmartRouter 的输入要求。
    """
    category: Optional[ProductCategory] = Field(default=None)
    product_type: Optional[str] = Field(default=None)
    price_tier: Optional[PriceTier] = Field(default=None)
    brand_tier: Optional[BrandTier] = Field(default=None)
    detected_selling_points: List[SellingPoint] = Field(default_factory=list)
    target_gender: Optional[TargetGender] = Field(default=None)
    target_age: Optional[TargetAgeGroup] = Field(default=None)
    estimated_spending_power: Optional[SpendingPower] = Field(default=None)
    inferred_personas: List[PersonaTag] = Field(default_factory=list)
    inferred_marketing_scene: Optional[MarketingScene] = Field(default=None)
    inferred_usage_scenes: List[UsageScene] = Field(default_factory=list)
    target_platform: Optional[TargetPlatform] = Field(default=None)
    inferred_video_style: Optional[VideoStyle] = Field(default=None)
    inferred_visual_tone: Optional[VisualTone] = Field(default=None)
    feature_confidence: Dict[str, float] = Field(default_factory=dict)
    raw_analysis: Dict[str, Any] = Field(default_factory=dict)

    def get_completeness_score(self: "ImageFeatures") -> float:
        """计算特征完整度分数 (0.0 - 1.0)"""
        required_fields = [
            "category", "product_type", "price_tier", "brand_tier",
            "target_gender", "target_age",
            "target_platform",
            "inferred_video_style", "inferred_visual_tone",
        ]
        filled = sum(1 for f in required_fields if getattr(self, f) is not None)
        return filled / len(required_fields)


class AnalysisOptions(BaseModel):
    """分析选项配置"""
    model_name: str = Field(default="gemini-2.0-flash-exp")
    temperature: float = Field(default=0.2, ge=0.0, le=1.0)
    max_output_tokens: int = Field(default=2048, ge=100, le=8192)
    timeout_seconds: int = Field(default=30, ge=5, le=120)
    max_retries: int = Field(default=3, ge=0, le=5)
    retry_backoff_base: float = Field(default=2.0)
    use_cache: bool = Field(default=True)
    cache_ttl_seconds: int = Field(default=86400)
    fallback_on_error: bool = Field(default=True)
    partial_results_on_timeout: bool = Field(default=True)


class AnalysisMetadata(BaseModel):
    """分析过程元数据"""
    image_hash: str = Field(default="")
    image_size_bytes: int = Field(default=0)
    image_dimensions: tuple = Field(default=(0, 0))
    preprocess_time_ms: int = 0
    cache_lookup_time_ms: int = 0
    api_call_time_ms: int = 0
    parse_time_ms: int = 0
    total_time_ms: int = 0
    model_name: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    api_calls_count: int = 0
    cache_hit: bool = False
    cache_source: Optional[str] = None
    created_at: str = Field(default_factory=lambda: str(time.time()))
    errors: List[Dict[str, Any]] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class AnalysisResponse(BaseModel):
    """图片分析完整响应"""
    features: ImageFeatures = Field(...)
    dimension_results: DimensionResults = Field(default_factory=DimensionResults)
    metadata: AnalysisMetadata = Field(default_factory=AnalysisMetadata)
    analysis_quality: Dict[str, Any] = Field(
        default_factory=lambda: {
            "completeness_score": 0.0,
            "confidence_score": 0.0,
            "reliability": "unknown",
        }
    )


# ═══════════════════════════════════════════════════════════
# LRU 缓存实现
# ═══════════════════════════════════════════════════════════

class LRUCache:
    """
    基于 OrderedDict 的 LRU 缓存实现

    支持:
    - 固定容量，自动淘汰最久未使用的条目
    - TTL (time-to-live) 自动过期
    - 线程安全（基于 GIL）
    """

    def __init__(self: "LRUCache", capacity: int = 256, default_ttl: int = 86400) -> None:
        """
        初始化 LRU 缓存

        Args:
            capacity: 最大缓存条目数
            default_ttl: 默认过期时间（秒）
        """
        self._capacity: int = capacity
        self._default_ttl: int = default_ttl
        self._cache: OrderedDict[str, Any] = OrderedDict()
        self._expires: Dict[str, float] = {}

    def get(self: "LRUCache", key: str) -> Optional[Any]:
        """
        获取缓存值

        Args:
            key: 缓存键

        Returns:
            缓存值，不存在或已过期返回 None
        """
        now = time.time()
        # 检查过期
        if key in self._expires and now > self._expires[key]:
            self._remove(key)
            return None

        if key in self._cache:
            # 移动到末尾（最近使用）
            self._cache.move_to_end(key)
            return self._cache[key]
        return None

    def set(self: "LRUCache", key: str, value: Any, ttl: Optional[int] = None) -> None:
        """
        设置缓存值

        Args:
            key: 缓存键
            value: 缓存值
            ttl: 过期时间（秒），None 使用默认值
        """
        if key in self._cache:
            self._cache.move_to_end(key)
        self._cache[key] = value
        self._expires[key] = time.time() + (ttl or self._default_ttl)

        # 超出容量，淘汰最久未使用的
        while len(self._cache) > self._capacity:
            oldest_key = next(iter(self._cache))
            self._remove(oldest_key)

    def delete(self: "LRUCache", key: str) -> bool:
        """删除缓存键，存在则返回 True"""
        if key in self._cache:
            self._remove(key)
            return True
        return False

    def clear(self: "LRUCache") -> None:
        """清空缓存"""
        self._cache.clear()
        self._expires.clear()

    def keys(self: "LRUCache") -> List[str]:
        """获取所有有效键"""
        now = time.time()
        valid_keys = []
        expired = []
        for k in list(self._cache.keys()):
            if k in self._expires and now > self._expires[k]:
                expired.append(k)
            else:
                valid_keys.append(k)
        for k in expired:
            self._remove(k)
        return valid_keys

    def _remove(self: "LRUCache", key: str) -> None:
        """内部删除"""
        self._cache.pop(key, None)
        self._expires.pop(key, None)

    @property
    def size(self: "LRUCache") -> int:
        """当前缓存大小"""
        return len(self._cache)

    @property
    def capacity(self: "LRUCache") -> int:
        """缓存容量"""
        return self._capacity


# ═══════════════════════════════════════════════════════════
# Token 使用统计
# ═══════════════════════════════════════════════════════════

@dataclass
class TokenUsage:
    """Token 使用统计"""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class APIResponse:
    """
    API 响应封装

    Attributes:
        raw_text: API 返回的原始文本
        parsed_json: 从文本中解析出的 JSON 对象
        token_usage: Token 使用统计
        latency_ms: API 调用延迟（毫秒）
        model_name: 使用的模型名称
        success: 调用是否成功
        error_message: 错误信息
    """
    raw_text: str
    parsed_json: Optional[Dict[str, Any]]
    token_usage: TokenUsage
    latency_ms: int
    model_name: str
    success: bool
    error_message: Optional[str] = None


# ═══════════════════════════════════════════════════════════
# Gemini Vision 客户端
# ═══════════════════════════════════════════════════════════

class GeminiVisionClient:
    """
    Gemini Vision API 客户端

    特性:
    - 支持同步/异步调用
    - 自动重试 + 指数退避
    - Token 用量追踪
    - 熔断器保护
    - 多模型降级
    """

    DEFAULT_SAFETY_SETTINGS: List[Dict[str, str]] = [
        {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_ONLY_HIGH"},
        {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
    ]

    def __init__(
        self,
        api_key: str,
        model_name: str = "gemini-2.0-flash-exp",
        fallback_model: str = "gemini-1.5-flash",
        timeout_seconds: int = 30,
        max_retries: int = 3,
        retry_backoff_base: float = 2.0,
        failure_threshold: int = 5,
        circuit_timeout: int = 60,
    ) -> None:
        """
        初始化 Gemini Vision 客户端

        Args:
            api_key: Gemini API Key
            model_name: 主模型名称
            fallback_model: 降级模型名称
            timeout_seconds: API 调用超时时间（秒）
            max_retries: 最大重试次数
            retry_backoff_base: 指数退避基数
            failure_threshold: 熔断器连续失败阈值
            circuit_timeout: 熔断器打开后恢复探测时间（秒）
        """
        if not api_key:
            raise ValueError("api_key 不能为空")

        self._api_key = api_key
        self._model_name = model_name
        self._fallback_model = fallback_model
        self._timeout = timeout_seconds
        self._max_retries = max_retries
        self._backoff_base = retry_backoff_base

        # 初始化 Gemini SDK
        try:
            import google.generativeai as genai
            genai.configure(api_key=api_key)
            self._model = genai.GenerativeModel(model_name)
            self._fallback = genai.GenerativeModel(fallback_model)
            self._genai = genai
        except ImportError:
            logger.warning("google.generativeai 未安装，GeminiVisionClient 将以模拟模式运行")
            self._model = None  # type: ignore
            self._fallback = None  # type: ignore
            self._genai = None

        # Token 统计
        self._total_tokens_used = 0
        self._total_api_calls = 0

        # 熔断器状态
        self._circuit_state: str = "closed"
        self._failure_count = 0
        self._failure_threshold = failure_threshold
        self._circuit_timeout = circuit_timeout
        self._last_failure_time: float = 0

    def analyze(
        self,
        image_data: Union[str, bytes, Any],
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.2,
        max_output_tokens: int = 2048,
        **kwargs: Any,
    ) -> APIResponse:
        """
        同步分析单张图片

        Args:
            image_data: 图片数据 (URL/本地路径/PIL Image)
            prompt: 分析提示词
            system_prompt: 系统提示词
            temperature: 生成温度
            max_output_tokens: 最大输出 token 数

        Returns:
            APIResponse: 封装后的响应
        """
        # 检查熔断器
        if self._circuit_state == "open":
            if time.time() - self._last_failure_time > self._circuit_timeout:
                self._circuit_state = "half_open"
            else:
                return APIResponse(
                    raw_text="",
                    parsed_json=None,
                    token_usage=TokenUsage(),
                    latency_ms=0,
                    model_name=self._model_name,
                    success=False,
                    error_message="Circuit breaker is OPEN",
                )

        # 预处理图片
        try:
            pil_image = self._prepare_image(image_data)
        except Exception as e:
            return APIResponse(
                raw_text="",
                parsed_json=None,
                token_usage=TokenUsage(),
                latency_ms=0,
                model_name=self._model_name,
                success=False,
                error_message=f"Image preparation failed: {e}",
            )

        messages = self._build_messages(pil_image, prompt, system_prompt)
        return self._call_with_retry(
            messages,
            use_fallback=False,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            **kwargs,
        )

    async def analyze_async(
        self,
        image_data: Union[str, bytes, Any],
        prompt: str,
        system_prompt: Optional[str] = None,
        **kwargs: Any,
    ) -> APIResponse:
        """异步分析单张图片"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            self.analyze,
            image_data,
            prompt,
            system_prompt,
            **kwargs,
        )

    def _prepare_image(self: "GeminiVisionClient", image_data: Union[str, bytes, Any]) -> Any:
        """将各种格式的图片数据转为 PIL Image"""
        try:
            from PIL import Image
        except ImportError:
            raise ValueError("PIL 未安装，无法处理图片")

        if isinstance(image_data, Image.Image):
            return image_data.convert("RGB")

        if isinstance(image_data, bytes):
            return Image.open(BytesIO(image_data)).convert("RGB")

        if isinstance(image_data, str):
            if image_data.startswith("http://") or image_data.startswith("https://"):
                return self._download_image(image_data)
            elif image_data.startswith("data:image"):
                base64_data = image_data.split(",")[1]
                return Image.open(BytesIO(base64.b64decode(base64_data))).convert("RGB")
            elif Path(image_data).exists():
                return Image.open(image_data).convert("RGB")
            else:
                try:
                    return Image.open(BytesIO(base64.b64decode(image_data))).convert("RGB")
                except Exception:
                    raise ValueError(f"无法识别的图片数据格式: {image_data[:50]}...")

        raise ValueError(f"不支持的图片数据类型: {type(image_data)}")

    def _download_image(self: "GeminiVisionClient", url: str) -> Any:
        """从 URL 下载图片"""
        try:
            import requests
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            from PIL import Image
            return Image.open(BytesIO(response.content)).convert("RGB")
        except Exception as e:
            logger.error(f"图片下载失败: {url}, error={e}")
            raise

    def _build_messages(
        self, image: Any, prompt: str, system_prompt: Optional[str] = None
    ) -> List[Any]:
        """构建 Gemini 消息格式"""
        parts: List[Any] = []
        if system_prompt:
            parts.append(system_prompt)
        parts.append(image)
        parts.append(prompt)
        return parts

    def _call_with_retry(
        self,
        messages: List[Any],
        use_fallback: bool = False,
        **kwargs: Any,
    ) -> APIResponse:
        """
        带重试的 API 调用（核心方法）
        """
        model = self._fallback if use_fallback else self._model
        model_name = self._fallback_model if use_fallback else self._model_name

        # 模拟模式：如果 genai 未安装，返回模拟响应
        if self._genai is None or model is None:
            return self._mock_response(model_name)

        last_error = ""

        for attempt in range(1, self._max_retries + 1):
            start_time = time.time()
            try:
                generation_config = {
                    "temperature": kwargs.get("temperature", 0.2),
                    "max_output_tokens": kwargs.get("max_output_tokens", 2048),
                    "top_p": kwargs.get("top_p", 0.95),
                    "top_k": kwargs.get("top_k", 40),
                    "response_mime_type": "application/json",
                }

                response = model.generate_content(
                    messages,
                    generation_config=generation_config,
                    safety_settings=self.DEFAULT_SAFETY_SETTINGS,
                )

                latency_ms = int((time.time() - start_time) * 1000)
                raw_text = response.text
                parsed_json = self._extract_json(raw_text)

                token_usage = TokenUsage(
                    prompt_tokens=self._estimate_tokens(str(messages)),
                    completion_tokens=self._estimate_tokens(raw_text),
                )
                token_usage.total_tokens = (
                    token_usage.prompt_tokens + token_usage.completion_tokens
                )

                self._total_tokens_used += token_usage.total_tokens
                self._total_api_calls += 1

                # 成功 → 重置熔断器
                if self._circuit_state == "half_open":
                    self._circuit_state = "closed"
                    self._failure_count = 0

                return APIResponse(
                    raw_text=raw_text,
                    parsed_json=parsed_json,
                    token_usage=token_usage,
                    latency_ms=latency_ms,
                    model_name=model_name,
                    success=True,
                )

            except Exception as e:
                latency_ms = int((time.time() - start_time) * 1000)
                last_error = str(e)
                self._failure_count += 1
                if self._failure_count >= self._failure_threshold:
                    self._circuit_state = "open"
                    self._last_failure_time = time.time()

                if attempt == self._max_retries and not use_fallback:
                    return self._call_with_retry(
                        messages, use_fallback=True, **kwargs
                    )

                if attempt < self._max_retries:
                    delay = min(self._backoff_base ** attempt, 60)
                    time.sleep(delay)

        return APIResponse(
            raw_text="",
            parsed_json=None,
            token_usage=TokenUsage(),
            latency_ms=0,
            model_name=model_name,
            success=False,
            error_message=f"All {self._max_retries} retries failed. Last: {last_error}",
        )

    def _mock_response(self: "GeminiVisionClient", model_name: str) -> APIResponse:
        """模拟响应（当 google.generativeai 未安装时使用）"""
        mock_json = {
            "category": "skincare",
            "product_type": "面膜",
            "price_tier": "mid_range",
            "brand_tier": "premium",
            "selling_points": ["natural_ingredients", "premium_packaging"],
            "confidence": {
                "category": 0.9, "product_type": 0.85,
                "price_tier": 0.7, "brand_tier": 0.8,
                "selling_points": 0.6
            },
            "raw_description": "一款高端面膜产品，包装精美",
        }
        return APIResponse(
            raw_text=json.dumps(mock_json),
            parsed_json=mock_json,
            token_usage=TokenUsage(prompt_tokens=100, completion_tokens=50),
            latency_ms=100,
            model_name=model_name,
            success=True,
        )

    def _extract_json(self: "GeminiVisionClient", text: str) -> Optional[Dict[str, Any]]:
        """从文本中提取 JSON 对象"""
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        import re
        json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass

        json_match = re.search(r'\{.*\}', text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group())
            except json.JSONDecodeError:
                pass

        return None

    def _estimate_tokens(self: "GeminiVisionClient", text: str) -> int:
        """估算 Token 数量（粗略：1 token ≈ 4 字符）"""
        return len(text) // 4 + 1

    @property
    def total_tokens_used(self: "GeminiVisionClient") -> int:
        """获取总 Token 使用量"""
        return self._total_tokens_used

    @property
    def total_api_calls(self: "GeminiVisionClient") -> int:
        """获取总 API 调用次数"""
        return self._total_api_calls

    def reset_stats(self: "GeminiVisionClient") -> None:
        """重置统计数据"""
        self._total_tokens_used = 0
        self._total_api_calls = 0


# ═══════════════════════════════════════════════════════════
# 提示词模板
# ═══════════════════════════════════════════════════════════

SYSTEM_PROMPT = """You are an expert e-commerce product image analyst specializing in Chinese consumer markets. Your task is to analyze product images and output structured analysis results.

## Output Rules
1. Output MUST be valid JSON format
2. All enum values MUST be from the provided enum lists
3. Confidence scores MUST be between 0.0 and 1.0
4. Use English enum values, Chinese descriptions
5. If uncertain, use null for single-value fields and empty list [] for multi-value fields
6. Provide detailed reasoning for each judgment

## Analysis Principles
- Focus on VISUAL CUES only: packaging, design, colors, typography, materials
- Consider Chinese e-commerce context (Douyin, Xiaohongshu, Taobao)
- Be conservative: low confidence is better than wrong classification
- Consider cross-dimension consistency
"""

PRODUCT_ANALYSIS_PROMPT = """
Analyze the product in this image and output a JSON object with the following fields:

### 1. category (ProductCategory)
Choose ONE from: skincare, makeup, fragrance, personal_care, womens_clothing, mens_clothing, shoes, bags, accessories, phone, computer, digital_accessory, home_appliance, snack, beverage, fresh_food, health_food, furniture, home_decor, daily_necessities, maternity_baby, pet, sports, outdoor, jewelry, watch

### 2. product_type (string)
Specific product type in Chinese, e.g., "口红", "面膜", "运动鞋"

### 3. price_tier (PriceTier)
Choose ONE: "budget"(0-50元), "affordable"(50-200元), "mid_range"(200-1000元), "premium"(1000-5000元), "luxury"(5000+元)

### 4. brand_tier (BrandTier)
Choose ONE: "mass", "mid_market", "premium", "luxury", "d2c"

### 5. selling_points (List[SellingPoint])
Choose from: "natural_ingredients", "high_tech", "durable", "lightweight", "cost_effective", "flash_sale", "bundle_deal", "premium_packaging", "limited_edition", "gift_ready", "trending", "celebrity_endorsed", "sustainable"

### 6. confidence (object)
Score 0.0-1.0 for each field

### 7. raw_description (string)
Brief Chinese description (max 100 chars)

Output JSON only, no markdown formatting.
"""

AUDIENCE_ANALYSIS_PROMPT = """
Based on the product shown in this image, infer the target audience and output JSON:

### 1. target_gender (TargetGender)
Choose ONE: "male", "female", "unisex", "not_specified"

### 2. target_age (TargetAgeGroup)
Choose ONE: "gen_z"(18-25), "gen_y"(26-35), "gen_x"(36-45), "silver"(46-60), "senior"(60+), "all_ages"

### 3. spending_power (SpendingPower)
Choose ONE: "economy", "middle", "affluent", "ultra_high"

### 4. persona_tags (List[PersonaTag])
Choose from: "minimalist", "fashion_forward", "tech_enthusiast", "health_conscious", "bargain_hunter", "quality_first", "brand_loyal", "early_adopter", "office_worker", "student", "parent", "senior_buyer"

### 5. confidence (object)
Score 0.0-1.0 for each field

### 6. reasoning (string)
Chinese explanation (max 150 chars)

Output JSON only.
"""

SCENE_ANALYSIS_PROMPT = """
Analyze the marketing context of this product image and output JSON:

### 1. marketing_scene (MarketingScene)
Choose ONE: "new_launch", "flash_sale", "seasonal_promo", "brand_building", "social_proof", "holiday_gift", "clearance", "membership"

### 2. usage_scenes (List[UsageScene])
Choose from: "daily", "work", "outdoor", "travel", "sports", "party", "home", "gift", "special_occasion"

### 3. target_platform (TargetPlatform)
Choose ONE: "douyin", "xiaohongshu", "taobao", "kuaishou", "wechat", "tiktok", "instagram", "universal"

### 4. confidence (object)
Score 0.0-1.0 for each field

### 5. scene_description (string)
Chinese description (max 100 chars)

Output JSON only.
"""

STYLE_ANALYSIS_PROMPT = """
Analyze the visual style of this product image and output JSON:

### 1. video_style (VideoStyle)
Choose ONE: "fast_paced", "slow_living", "story_driven", "product_showcase", "unboxing", "tutorial", "lifestyle", "dramatic", "minimal", "ugc_style"

### 2. visual_tone (VisualTone)
Choose ONE: "bright", "dark_moody", "warm", "cool", "pastel", "monochrome", "vibrant", "premium", "natural", "futuristic"

### 3. confidence (object)
Score 0.0-1.0 for each field

### 4. visual_description (string)
Chinese description (max 100 chars)

### 5. dominant_colors (List[string])
Main colors in #RRGGBB format, up to 5

### 6. composition_features (List[string])
Choose from: "close_up", "centered", "rule_of_thirds", "flat_lay", "lifestyle_shot", "white_background", "model_shot"

Output JSON only.
"""


# ═══════════════════════════════════════════════════════════
# 提示词模板管理器
# ═══════════════════════════════════════════════════════════

class PromptTemplateManager:
    """
    提示词模板管理器

    负责管理 4 组提示词模板，支持动态扩展。
    """

    _PROMPT_TEMPLATES: Dict[str, str] = {
        "product": PRODUCT_ANALYSIS_PROMPT,
        "audience": AUDIENCE_ANALYSIS_PROMPT,
        "scene": SCENE_ANALYSIS_PROMPT,
        "style": STYLE_ANALYSIS_PROMPT,
    }

    @classmethod
    def get_prompt(
        cls,
        dimension: str,
        category_hint: Optional[ProductCategory] = None,
        platform_hint: Optional[str] = None,
    ) -> str:
        """
        获取指定维度的提示词模板

        Args:
            dimension: 维度名称 (product/audience/scene/style)
            category_hint: 品类提示 (可选)
            platform_hint: 平台提示 (可选)

        Returns:
            str: 完整的提示词文本

        Raises:
            ValueError: 如果维度名称不合法
        """
        if dimension not in cls._PROMPT_TEMPLATES:
            raise ValueError(
                f"未知的分析维度: {dimension}. "
                f"可选: {list(cls._PROMPT_TEMPLATES.keys())}"
            )

        prompt = cls._PROMPT_TEMPLATES[dimension]

        if category_hint:
            prompt += f"\n\n## Product Context\n产品品类: {category_hint.value}"

        if platform_hint and dimension == "scene":
            prompt += f"\n\n## Platform Preference\n用户偏好平台: {platform_hint}"

        return prompt

    @classmethod
    def register_template(cls: type["PromptTemplateManager"], dimension: str, template: str) -> None:
        """注册新的提示词模板"""
        cls._PROMPT_TEMPLATES[dimension] = template


# ═══════════════════════════════════════════════════════════
# 基础分析器抽象类
# ═══════════════════════════════════════════════════════════

class BaseAnalyzer(ABC):
    """
    维度分析器基类

    所有具体维度分析器必须继承此类并实现抽象方法。
    """

    dimension_name: str = ""
    result_model: Type = BaseModel

    def __init__(
        self,
        vision_client: GeminiVisionClient,
        cache: Optional[LRUCache] = None,
    ) -> None:
        """
        初始化分析器

        Args:
            vision_client: Gemini Vision API 客户端
            cache: 缓存实例 (可选)
        """
        self._client = vision_client
        self._cache = cache

    def analyze(
        self,
        image_data: Union[str, bytes, Any],
        category_hint: Optional[ProductCategory] = None,
        **kwargs: Any,
    ) -> Optional[BaseModel]:
        """
        执行维度分析 (同步)

        流程:
        1. 构建缓存键
        2. 检查缓存
        3. 获取提示词
        4. 调用 Gemini Vision API
        5. 解析 API 响应
        6. 验证结果
        7. 写入缓存
        """
        try:
            cache_key = self._build_cache_key(image_data, category_hint)

            if self._cache:
                cached = self._cache.get(cache_key)
                if cached:
                    return self.result_model(**cached)

            prompt = self.get_prompt(category_hint, **kwargs)
            response = self._client.analyze(
                image_data=image_data,
                prompt=prompt,
                system_prompt=SYSTEM_PROMPT,
            )

            if not response.success:
                return self._fallback_result(category_hint)

            if response.parsed_json:
                result = self.parse_result(response.parsed_json)
            else:
                result = self._parse_from_raw(response.raw_text)

            if result:
                result = self.validate_result(result)

            if self._cache and result:
                self._cache.set(
                    cache_key,
                    result.model_dump(),
                    ttl=86400,
                )

            return result

        except Exception as e:
            logger.warning(f"{self.dimension_name} 分析异常: {e}")
            return self._fallback_result(category_hint)

    async def analyze_async(
        self,
        image_data: Union[str, bytes, Any],
        category_hint: Optional[ProductCategory] = None,
        **kwargs: Any,
    ) -> Optional[BaseModel]:
        """执行维度分析 (异步)"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self.analyze, image_data, category_hint, **kwargs
        )

    @abstractmethod
    def get_prompt(
        self,
        category_hint: Optional[ProductCategory] = None,
        **kwargs: Any,
    ) -> str:
        """获取分析提示词"""
        pass

    @abstractmethod
    def parse_result(self: "BaseAnalyzer", raw_json: Dict[str, Any]) -> BaseModel:
        """解析 API 返回的 JSON"""
        pass

    @abstractmethod
    def validate_result(self: "BaseAnalyzer", result: BaseModel) -> BaseModel:
        """验证并修正分析结果"""
        pass

    def supports(self: "BaseAnalyzer", category: ProductCategory) -> bool:
        """判断是否支持该品类的分析"""
        return True

    def _build_cache_key(
        self,
        image_data: Union[str, bytes, Any],
        category_hint: Optional[ProductCategory] = None,
    ) -> str:
        """构建缓存键"""
        if isinstance(image_data, str):
            image_hash = hashlib.md5(image_data.encode()).hexdigest()[:16]
        else:
            image_hash = "binary"

        category_part = f"_{category_hint.value}" if category_hint else ""
        return f"analysis:{self.dimension_name}:{image_hash}{category_part}"

    def _fallback_result(
        self,
        category_hint: Optional[ProductCategory] = None,
    ) -> Optional[BaseModel]:
        """生成降级结果"""
        return None

    def _parse_from_raw(self: "BaseAnalyzer", raw_text: str) -> Optional[BaseModel]:
        """从原始文本解析 (备用)"""
        return None

    @staticmethod
    def _parse_enum(value: Any, enum_class: Type[Enum]) -> Optional[Enum]:
        """安全解析枚举值"""
        if value is None:
            return None
        try:
            return enum_class(value)
        except ValueError:
            for member in enum_class:
                if member.value.lower() == str(value).lower():
                    return member
            return None

    @staticmethod
    def _parse_enum_list(
        values: List[Any], enum_class: Type[Enum]
    ) -> List[Enum]:
        """安全解析枚举列表"""
        if not values:
            return []
        result = []
        for v in values:
            parsed = BaseAnalyzer._parse_enum(v, enum_class)
            if parsed:
                result.append(parsed)
        return result


# ═══════════════════════════════════════════════════════════
# 产品维度分析器
# ═══════════════════════════════════════════════════════════

class ProductAnalyzer(BaseAnalyzer):
    """
    产品维度分析器 — 分析产品的品类、类型、价格、品牌、卖点
    """

    dimension_name = "product"
    result_model = ProductAnalysisResult

    def get_prompt(
        self,
        category_hint: Optional[ProductCategory] = None,
        **kwargs: Any,
    ) -> str:
        """获取产品分析提示词"""
        return PromptTemplateManager.get_prompt("product", category_hint)

    def parse_result(self: "ProductAnalyzer", raw_json: Dict[str, Any]) -> ProductAnalysisResult:
        """解析产品分析结果"""
        category = self._parse_enum(raw_json.get("category"), ProductCategory)
        price_tier = self._parse_enum(raw_json.get("price_tier"), PriceTier)
        brand_tier = self._parse_enum(raw_json.get("brand_tier"), BrandTier)
        selling_points = self._parse_enum_list(
            raw_json.get("selling_points", []), SellingPoint)

        return ProductAnalysisResult(
            category=category,
            product_type=raw_json.get("product_type"),
            price_tier=price_tier,
            brand_tier=brand_tier,
            selling_points=selling_points,
            confidence=raw_json.get("confidence", {}),
            raw_description=raw_json.get("raw_description", ""),
        )

    def validate_result(
        self, result: ProductAnalysisResult
    ) -> ProductAnalysisResult:
        """验证并修正产品分析结果"""
        for field in ["category", "product_type", "price_tier",
                      "brand_tier", "selling_points"]:
            if field not in result.confidence:
                result.confidence[field] = 0.5 if getattr(result, field) else 0.0
        return result

    def _fallback_result(
        self,
        category_hint: Optional[ProductCategory] = None,
    ) -> ProductAnalysisResult:
        """产品分析降级结果"""
        return ProductAnalysisResult(
            category=category_hint,
            confidence={
                f: 0.0 for f in [
                    "category", "product_type", "price_tier",
                    "brand_tier", "selling_points"
                ]
            },
            raw_description="分析失败，使用降级结果",
        )


# ═══════════════════════════════════════════════════════════
# 人群维度分析器
# ═══════════════════════════════════════════════════════════

class AudienceAnalyzer(BaseAnalyzer):
    """
    人群维度分析器 — 推断目标受众特征
    """

    dimension_name = "audience"
    result_model = AudienceAnalysisResult

    def get_prompt(
        self,
        category_hint: Optional[ProductCategory] = None,
        **kwargs: Any,
    ) -> str:
        """获取人群分析提示词"""
        return PromptTemplateManager.get_prompt("audience", category_hint)

    def parse_result(self: "AudienceAnalyzer", raw_json: Dict[str, Any]) -> AudienceAnalysisResult:
        """解析人群分析结果"""
        return AudienceAnalysisResult(
            target_gender=self._parse_enum(
                raw_json.get("target_gender"), TargetGender),
            target_age=self._parse_enum(
                raw_json.get("target_age"), TargetAgeGroup),
            spending_power=self._parse_enum(
                raw_json.get("spending_power"), SpendingPower),
            persona_tags=self._parse_enum_list(
                raw_json.get("persona_tags", []), PersonaTag),
            confidence=raw_json.get("confidence", {}),
            reasoning=raw_json.get("reasoning", ""),
        )

    def validate_result(
        self, result: AudienceAnalysisResult
    ) -> AudienceAnalysisResult:
        """验证人群分析结果"""
        for field in ["target_gender", "target_age",
                      "spending_power", "persona_tags"]:
            if field not in result.confidence:
                result.confidence[field] = 0.5 if getattr(result, field) else 0.0
        return result

    def _fallback_result(
        self,
        category_hint: Optional[ProductCategory] = None,
    ) -> AudienceAnalysisResult:
        """人群分析降级结果"""
        gender = TargetGender.NOT_SPECIFIED
        age = TargetAgeGroup.ALL_AGES
        spending = SpendingPower.MIDDLE

        if category_hint:
            if category_hint in [ProductCategory.MAKEUP, ProductCategory.SKINCARE]:
                gender = TargetGender.FEMALE
                age = TargetAgeGroup.GEN_Y
            elif category_hint in [ProductCategory.PHONE, ProductCategory.COMPUTER]:
                gender = TargetGender.UNISEX
                age = TargetAgeGroup.GEN_Y

        return AudienceAnalysisResult(
            target_gender=gender,
            target_age=age,
            spending_power=spending,
            confidence={f: 0.1 for f in [
                "target_gender", "target_age",
                "spending_power", "persona_tags"
            ]},
            reasoning="分析失败，使用品类默认人群",
        )


# ═══════════════════════════════════════════════════════════
# 场景维度分析器
# ═══════════════════════════════════════════════════════════

class SceneAnalyzer(BaseAnalyzer):
    """
    场景维度分析器 — 分析营销场景、使用场景、目标平台
    """

    dimension_name = "scene"
    result_model = SceneAnalysisResult

    def get_prompt(
        self,
        category_hint: Optional[ProductCategory] = None,
        **kwargs: Any,
    ) -> str:
        """获取场景分析提示词"""
        return PromptTemplateManager.get_prompt("scene", category_hint)

    def parse_result(self: "SceneAnalyzer", raw_json: Dict[str, Any]) -> SceneAnalysisResult:
        """解析场景分析结果"""
        return SceneAnalysisResult(
            marketing_scene=self._parse_enum(
                raw_json.get("marketing_scene"), MarketingScene),
            usage_scenes=self._parse_enum_list(
                raw_json.get("usage_scenes", []), UsageScene),
            target_platform=self._parse_enum(
                raw_json.get("target_platform"), TargetPlatform),
            confidence=raw_json.get("confidence", {}),
            scene_description=raw_json.get("scene_description", ""),
        )

    def validate_result(
        self, result: SceneAnalysisResult
    ) -> SceneAnalysisResult:
        """验证场景分析结果"""
        for field in ["marketing_scene", "usage_scenes", "target_platform"]:
            if field not in result.confidence:
                result.confidence[field] = 0.5 if getattr(result, field) else 0.0
        return result

    def _fallback_result(
        self,
        category_hint: Optional[ProductCategory] = None,
    ) -> SceneAnalysisResult:
        """场景分析降级结果"""
        return SceneAnalysisResult(
            marketing_scene=MarketingScene.SOCIAL_PROOF,
            usage_scenes=[UsageScene.DAILY],
            target_platform=TargetPlatform.UNIVERSAL,
            confidence={f: 0.1 for f in [
                "marketing_scene", "usage_scenes", "target_platform"
            ]},
            scene_description="分析失败，使用默认场景",
        )


# ═══════════════════════════════════════════════════════════
# 风格维度分析器
# ═══════════════════════════════════════════════════════════

class StyleAnalyzer(BaseAnalyzer):
    """
    风格维度分析器 — 分析视觉风格、画面调性
    """

    dimension_name = "style"
    result_model = StyleAnalysisResult

    def get_prompt(
        self,
        category_hint: Optional[ProductCategory] = None,
        **kwargs: Any,
    ) -> str:
        """获取风格分析提示词"""
        return PromptTemplateManager.get_prompt("style", category_hint)

    def parse_result(self: "StyleAnalyzer", raw_json: Dict[str, Any]) -> StyleAnalysisResult:
        """解析风格分析结果"""
        return StyleAnalysisResult(
            video_style=self._parse_enum(
                raw_json.get("video_style"), VideoStyle),
            visual_tone=self._parse_enum(
                raw_json.get("visual_tone"), VisualTone),
            confidence=raw_json.get("confidence", {}),
            visual_description=raw_json.get("visual_description", ""),
            dominant_colors=raw_json.get("dominant_colors", []),
            composition_features=raw_json.get("composition_features", []),
        )

    def validate_result(
        self, result: StyleAnalysisResult
    ) -> StyleAnalysisResult:
        """验证风格分析结果"""
        for field in ["video_style", "visual_tone"]:
            if field not in result.confidence:
                result.confidence[field] = 0.5 if getattr(result, field) else 0.0
        return result

    def _fallback_result(
        self,
        category_hint: Optional[ProductCategory] = None,
    ) -> StyleAnalysisResult:
        """风格分析降级结果"""
        return StyleAnalysisResult(
            video_style=VideoStyle.PRODUCT_SHOWCASE,
            visual_tone=VisualTone.BRIGHT,
            confidence={f: 0.1 for f in ["video_style", "visual_tone"]},
            visual_description="分析失败，使用默认风格",
        )


# ═══════════════════════════════════════════════════════════
# ImageAnalyzer 主入口 (Facade)
# ═══════════════════════════════════════════════════════════

class ImageAnalyzer:
    """
    图片深度分析器 — 主入口类 (Facade 模式)

    协调4个维度分析器，聚合结果，对外提供统一接口。
    支持同步/异步分析、批量分析、缓存管理。
    """

    ANALYZER_REGISTRY: Dict[str, Type[BaseAnalyzer]] = {
        "product": ProductAnalyzer,
        "audience": AudienceAnalyzer,
        "scene": SceneAnalyzer,
        "style": StyleAnalyzer,
    }

    def __init__(
        self,
        api_key: str,
        model_name: str = "gemini-2.0-flash-exp",
        cache: Optional[LRUCache] = None,
        max_workers: int = 4,
        options: Optional[AnalysisOptions] = None,
    ) -> None:
        """
        初始化图片分析器

        Args:
            api_key: Gemini API Key
            model_name: Gemini 模型名称
            cache: 缓存实例 (可选)
            max_workers: 并行分析最大线程数
            options: 分析选项配置
        """
        if not api_key:
            raise ValueError("api_key 不能为空")

        self._options = options or AnalysisOptions()
        self._max_workers = max_workers

        # 初始化 Vision 客户端
        self._vision_client = GeminiVisionClient(
            api_key=api_key,
            model_name=model_name,
            timeout_seconds=self._options.timeout_seconds,
            max_retries=self._options.max_retries,
        )

        # 初始化缓存
        self._cache = cache
        if self._cache is None and self._options.use_cache:
            self._cache = LRUCache(capacity=256)

        # 初始化各维度分析器
        self._analyzers: Dict[str, BaseAnalyzer] = {}
        for dim_name, analyzer_class in self.ANALYZER_REGISTRY.items():
            self._analyzers[dim_name] = analyzer_class(
                vision_client=self._vision_client,
                cache=self._cache,
            )

        logger.info(
            f"ImageAnalyzer 初始化完成: model={model_name}, "
            f"analyzers={list(self._analyzers.keys())}"
        )

    def analyze(
        self,
        image: Union[str, bytes, Any],
        source_type: ImageSourceType = ImageSourceType.URL,
        dimensions: Optional[List[str]] = None,
        platform_hint: Optional[str] = None,
        **kwargs: Any,
    ) -> AnalysisResponse:
        """
        分析单张图片 (同步)

        完整分析流程:
        1. 图片预处理
        2. 计算图片哈希 → 检查全量缓存
        3. 串行分析产品维度 (获取品类提示)
        4. 并行分析其余维度
        5. 聚合四维度结果为 ImageFeatures
        6. 计算分析质量指标
        7. 写入全量缓存

        Args:
            image: 图片数据
            source_type: 图片来源类型
            dimensions: 需要分析的维度
            platform_hint: 平台偏好提示

        Returns:
            AnalysisResponse: 完整分析响应
        """
        start_time = time.time()
        dimensions_to_analyze = dimensions or ["product", "audience", "scene", "style"]
        metadata = AnalysisMetadata()

        # 1. 图片预处理
        try:
            pil_image = self._preprocess_image(image, source_type)
        except Exception as e:
            logger.error(f"图片预处理失败: {e}")
            return self._create_error_response(str(e), metadata)

        # 2. 检查全量缓存
        image_hash = self._compute_image_hash(pil_image)
        metadata.image_hash = image_hash

        if self._cache and self._options.use_cache:
            cached_result = self._cache.get(f"full:{image_hash}")
            if cached_result:
                metadata.cache_hit = True
                metadata.cache_source = "lru"
                return AnalysisResponse(**cached_result)

        # 3. 并行分析各维度
        dimension_results = DimensionResults()

        # 先分析产品维度
        product_result: Optional[ProductAnalysisResult] = None
        if "product" in dimensions_to_analyze:
            product_result = self._analyzers["product"].analyze(  # type: ignore
                pil_image, category_hint=None
            )
            if product_result:
                dimension_results.product = product_result
                dimension_results.analyzed_dimensions.append("product")
            else:
                dimension_results.failed_dimensions.append("product")

        category_hint = product_result.category if product_result else None

        # 并行分析其余维度
        remaining_dims = [d for d in dimensions_to_analyze if d != "product"]

        with ThreadPoolExecutor(max_workers=self._max_workers) as executor:
            futures: Dict[Any, str] = {}
            for dim in remaining_dims:
                future = executor.submit(
                    self._analyzers[dim].analyze,
                    pil_image,
                    category_hint,
                )
                futures[future] = dim

            for future in as_completed(futures):
                dim = futures[future]
                try:
                    result = future.result(timeout=self._options.timeout_seconds)
                    if result:
                        setattr(dimension_results, dim, result)
                        dimension_results.analyzed_dimensions.append(dim)
                    else:
                        dimension_results.failed_dimensions.append(dim)
                except Exception as e:
                    logger.error(f"{dim} 维度分析失败: {e}")
                    dimension_results.failed_dimensions.append(dim)

        # 4. 聚合结果为 ImageFeatures
        features = self._merge_dimension_results(
            dimension_results,
            platform_hint=platform_hint,
        )

        # 5. 计算分析质量
        analysis_quality = self._calculate_quality(features, dimension_results)

        # 6. 组装响应
        metadata.total_time_ms = int((time.time() - start_time) * 1000)
        metadata.model_name = self._options.model_name
        metadata.api_calls_count = len(dimensions_to_analyze)

        response = AnalysisResponse(
            features=features,
            dimension_results=dimension_results,
            metadata=metadata,
            analysis_quality=analysis_quality,
        )

        # 7. 写入全量缓存
        if self._cache and self._options.use_cache:
            self._cache.set(
                f"full:{image_hash}",
                response.model_dump(),
                ttl=self._options.cache_ttl_seconds,
            )

        return response

    async def analyze_async(
        self,
        image: Union[str, bytes, Any],
        **kwargs: Any,
    ) -> AnalysisResponse:
        """异步分析单张图片"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.analyze, image, **kwargs)

    def _preprocess_image(
        self,
        image: Union[str, bytes, Any],
        source_type: ImageSourceType,
    ) -> Any:
        """图片预处理"""
        try:
            from PIL import Image
        except ImportError:
            raise ValueError("PIL 未安装")

        if isinstance(image, Image.Image):
            return image.convert("RGB")

        if source_type == ImageSourceType.URL:
            return self._vision_client._download_image(image).convert("RGB")
        elif source_type == ImageSourceType.LOCAL_PATH:
            return Image.open(image).convert("RGB")
        elif source_type == ImageSourceType.BASE64:
            data = base64.b64decode(image)
            return Image.open(BytesIO(data)).convert("RGB")
        elif source_type in (ImageSourceType.BYTES, ImageSourceType.UPLOADED_FILE):
            return Image.open(BytesIO(image)).convert("RGB")
        else:
            raise ValueError(f"不支持的图片类型: {source_type}")

    def _compute_image_hash(self: "ImageAnalyzer", image: Any) -> str:
        """计算图片感知哈希 (用于缓存键)"""
        try:
            from PIL import Image
            small = image.resize((64, 64), Image.Resampling.LANCZOS)
        except (ImportError, AttributeError):
            small = image.resize((64, 64))
        img_bytes = small.tobytes()
        return hashlib.md5(img_bytes).hexdigest()

    def _merge_dimension_results(
        self,
        dim_results: DimensionResults,
        platform_hint: Optional[str] = None,
    ) -> ImageFeatures:
        """
        合并四维度结果为 ImageFeatures

        平台优先级: 用户指定 > AI推断 > 默认值
        """
        features = ImageFeatures()

        if dim_results.product:
            p = dim_results.product
            features.category = p.category
            features.product_type = p.product_type
            features.price_tier = p.price_tier
            features.brand_tier = p.brand_tier
            features.detected_selling_points = p.selling_points
            features.feature_confidence.update(p.confidence)

        if dim_results.audience:
            a = dim_results.audience
            features.target_gender = a.target_gender
            features.target_age = a.target_age
            features.estimated_spending_power = a.spending_power
            features.inferred_personas = a.persona_tags
            features.feature_confidence.update(a.confidence)

        if dim_results.scene:
            s = dim_results.scene
            features.inferred_marketing_scene = s.marketing_scene
            features.inferred_usage_scenes = s.usage_scenes
            if platform_hint:
                try:
                    features.target_platform = TargetPlatform(platform_hint)
                except ValueError:
                    features.target_platform = s.target_platform
            else:
                features.target_platform = s.target_platform
            features.feature_confidence.update(s.confidence)

        if dim_results.style:
            st = dim_results.style
            features.inferred_video_style = st.video_style
            features.inferred_visual_tone = st.visual_tone
            features.feature_confidence.update(st.confidence)

        return features

    def _calculate_quality(
        self,
        features: ImageFeatures,
        dim_results: DimensionResults,
    ) -> Dict[str, Any]:
        """计算分析质量指标"""
        completeness = features.get_completeness_score()
        confidences = list(features.feature_confidence.values())
        avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0

        if completeness >= 0.8 and avg_confidence >= 0.7:
            reliability = "reliable"
        elif completeness >= 0.5 and avg_confidence >= 0.5:
            reliability = "moderate"
        else:
            reliability = "unreliable"

        return {
            "completeness_score": round(completeness, 4),
            "confidence_score": round(avg_confidence, 4),
            "reliability": reliability,
            "dimensions_analyzed": len(dim_results.analyzed_dimensions),
            "dimensions_failed": len(dim_results.failed_dimensions),
        }

    def _create_error_response(
        self,
        error_message: str,
        metadata: Optional[AnalysisMetadata] = None,
    ) -> AnalysisResponse:
        """创建错误响应"""
        if metadata is None:
            metadata = AnalysisMetadata()
        metadata.errors.append({"message": error_message})

        return AnalysisResponse(
            features=ImageFeatures(),
            dimension_results=DimensionResults(),
            metadata=metadata,
            analysis_quality={
                "completeness_score": 0.0,
                "confidence_score": 0.0,
                "reliability": "error",
            },
        )


# ═══════════════════════════════════════════════════════════
# 测试代码
# ═══════════════════════════════════════════════════════════

def _test():
    """内部测试 — 验证 ImageAnalyzer 核心功能"""
    print("=" * 60)
    print("ImageAnalyzer 图片分析器测试")
    print("=" * 60)

    # 1. 测试 LRU 缓存
    cache = LRUCache(capacity=3)
    cache.set("key1", "value1")
    cache.set("key2", "value2")
    cache.set("key3", "value3")
    assert cache.size == 3
    cache.set("key4", "value4")  # 应淘汰 key1
    assert cache.size == 3
    assert cache.get("key1") is None
    assert cache.get("key2") == "value2"
    print("[1] LRU 缓存基础功能: 通过")

    # 2. 测试缓存 TTL
    cache2 = LRUCache(capacity=10, default_ttl=0)
    cache2.set("ttl_key", "ttl_value", ttl=0)
    time.sleep(0.1)
    assert cache2.get("ttl_key") is None
    print("[2] LRU 缓存 TTL 过期: 通过")

    # 3. 测试枚举解析
    from enum import Enum as PyEnum
    class TestEnum(str, PyEnum):
        A = "a"
        B = "b"

    assert BaseAnalyzer._parse_enum("a", TestEnum) == TestEnum.A
    assert BaseAnalyzer._parse_enum("A", TestEnum) == TestEnum.A  # 大小写不敏感
    assert BaseAnalyzer._parse_enum("unknown", TestEnum) is None
    assert BaseAnalyzer._parse_enum(None, TestEnum) is None
    print("[3] 枚举安全解析: 通过")

    # 4. 测试 ImageFeatures 完整度计算
    full_features = ImageFeatures(
        category=ProductCategory.MAKEUP,
        product_type="口红",
        price_tier=PriceTier.MID_RANGE,
        brand_tier=BrandTier.PREMIUM,
        target_gender=TargetGender.FEMALE,
        target_age=TargetAgeGroup.GEN_Z,
        target_platform=TargetPlatform.DOUYIN,
        inferred_video_style=VideoStyle.FAST_PACED,
        inferred_visual_tone=VisualTone.BRIGHT,
    )
    completeness = full_features.get_completeness_score()
    assert completeness == 1.0, f"完整特征完整度应为 1.0, 实际 {completeness}"

    partial_features = ImageFeatures(category=ProductCategory.MAKEUP)
    partial_completeness = partial_features.get_completeness_score()
    assert abs(partial_completeness - 1 / 9) < 0.01
    print(f"[4] 特征完整度计算: full={completeness}, partial={partial_completeness:.4f}")

    # 5. 测试降级结果
    product_fb = ProductAnalyzer(
        vision_client=GeminiVisionClient(api_key="test"),
        cache=None,
    )._fallback_result(category_hint=ProductCategory.MAKEUP)
    assert product_fb.category == ProductCategory.MAKEUP
    assert product_fb.confidence["category"] == 0.0
    print("[5] 降级结果生成: 通过")

    audience_fb = AudienceAnalyzer(
        vision_client=GeminiVisionClient(api_key="test"),
        cache=None,
    )._fallback_result(category_hint=ProductCategory.SKINCARE)
    assert audience_fb.target_gender == TargetGender.FEMALE
    assert audience_fb.target_age == TargetAgeGroup.GEN_Y
    print("[6] 人群降级结果(美妆品类): 通过")

    # 6. 测试 GeminiVisionClient 模拟模式 (使用 base64 图片数据)
    client = GeminiVisionClient(api_key="test_key")
    # 1x1 像素透明 PNG 的 base64
    test_b64_img = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
        "AAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    )
    response = client.analyze(test_b64_img, "test prompt")
    assert response.success is True, f"API 调用失败: {response.error_message}"
    assert response.parsed_json is not None
    assert "category" in response.parsed_json
    print(f"[7] VisionClient 模拟模式: 通过 (model={response.model_name})")

    # 7. 测试 PromptTemplateManager
    prompt = PromptTemplateManager.get_prompt("product")
    assert "category" in prompt
    assert "Analyze the product" in prompt

    prompt_with_hint = PromptTemplateManager.get_prompt(
        "scene", category_hint=ProductCategory.MAKEUP, platform_hint="douyin")
    assert "Product Context" in prompt_with_hint
    assert "douyin" in prompt_with_hint
    print("[8] 提示词模板管理: 通过")

    # 8. 测试分析结果验证
    product_result = ProductAnalysisResult(
        category=ProductCategory.MAKEUP,
        product_type="口红",
        confidence={"category": 0.95},
    )
    validated = ProductAnalyzer(
        vision_client=GeminiVisionClient(api_key="test"),
        cache=None,
    ).validate_result(product_result)
    assert "product_type" in validated.confidence
    assert validated.confidence["product_type"] == 0.5
    print("[9] 产品分析结果验证: 通过")

    # 9. 测试 DimensionResults 聚合
    dim_results = DimensionResults(
        product=ProductAnalysisResult(
            category=ProductCategory.MAKEUP,
            product_type="口红",
            price_tier=PriceTier.MID_RANGE,
            brand_tier=BrandTier.PREMIUM,
            selling_points=[SellingPoint.TRENDING],
        ),
        audience=AudienceAnalysisResult(
            target_gender=TargetGender.FEMALE,
            target_age=TargetAgeGroup.GEN_Z,
        ),
    )
    assert dim_results.product.category == ProductCategory.MAKEUP
    assert dim_results.audience.target_gender == TargetGender.FEMALE
    print("[10] DimensionResults 聚合: 通过")

    # 10. 测试 ImageAnalyzer 初始化
    analyzer = ImageAnalyzer(api_key="test_key", cache=LRUCache(capacity=10))
    assert analyzer._options is not None
    assert analyzer._cache is not None
    print("[11] ImageAnalyzer 初始化: 通过")

    print("\n" + "=" * 60)
    print("所有测试通过!")
    print("=" * 60)


if __name__ == "__main__":
    _test()
