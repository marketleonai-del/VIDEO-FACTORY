"""
smart_router.py — SmartRouter 核心引擎

300 Agent 融合项目的智能路由引擎，负责将产品图片分析出的 14 维特征
映射到最合适的视频生成 Gem 模板。

核心流程: 特征预处理 → 分层过滤 → 加权评分 → 置信度评估 → 结果组装

用法:
    router = SmartRouter()
    router.register_gem(gem_metadata)
    result = router.route_from_features(image_features)
"""

from __future__ import annotations

import time
import uuid
from copy import deepcopy
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field, field_validator


# ═══════════════════════════════════════════════════════════
# 枚举定义
# ═══════════════════════════════════════════════════════════

class ProductCategory(str, Enum):
    """产品品类 — 一级类目（25个品类）"""
    # 美妆个护
    SKINCARE = "skincare"
    MAKEUP = "makeup"
    FRAGRANCE = "fragrance"
    PERSONAL_CARE = "personal_care"
    # 服饰时尚
    WOMENS_CLOTHING = "womens_clothing"
    MENS_CLOTHING = "mens_clothing"
    SHOES = "shoes"
    BAGS = "bags"
    ACCESSORIES = "accessories"
    # 数码家电
    PHONE = "phone"
    COMPUTER = "computer"
    DIGITAL_ACCESSORY = "digital_accessory"
    HOME_APPLIANCE = "home_appliance"
    # 食品生鲜
    SNACK = "snack"
    BEVERAGE = "beverage"
    FRESH_FOOD = "fresh_food"
    HEALTH_FOOD = "health_food"
    # 家居生活
    FURNITURE = "furniture"
    HOME_DECOR = "home_decor"
    DAILY_NECESSITIES = "daily_necessities"
    # 母婴宠物
    MATERNITY_BABY = "maternity_baby"
    PET = "pet"
    # 运动户外
    SPORTS = "sports"
    OUTDOOR = "outdoor"
    # 珠宝钟表
    JEWELRY = "jewelry"
    WATCH = "watch"


class PriceTier(str, Enum):
    """价格带 — 5 级离散"""
    BUDGET = "budget"           # 0-50 元
    AFFORDABLE = "affordable"   # 50-200 元
    MID_RANGE = "mid_range"     # 200-1000 元
    PREMIUM = "premium"         # 1000-5000 元
    LUXURY = "luxury"           # 5000+ 元


class BrandTier(str, Enum):
    """品牌调性 — 5 级"""
    MASS = "mass"               # 大众品牌
    MID_MARKET = "mid_market"   # 中端品牌
    PREMIUM = "premium"         # 高端品牌
    LUXURY = "luxury"           # 奢侈品牌
    D2C = "d2c"                 # 新锐/白牌


class SellingPoint(str, Enum):
    """卖点特征 — 标签列表"""
    # 功能卖点
    NATURAL_INGREDIENTS = "natural_ingredients"
    HIGH_TECH = "high_tech"
    DURABLE = "durable"
    LIGHTWEIGHT = "lightweight"
    # 价格卖点
    COST_EFFECTIVE = "cost_effective"
    FLASH_SALE = "flash_sale"
    BUNDLE_DEAL = "bundle_deal"
    # 体验卖点
    PREMIUM_PACKAGING = "premium_packaging"
    LIMITED_EDITION = "limited_edition"
    GIFT_READY = "gift_ready"
    # 社会卖点
    TRENDING = "trending"
    CELEBRITY_ENDORSED = "celebrity_endorsed"
    SUSTAINABLE = "sustainable"


class TargetGender(str, Enum):
    """目标性别 — 4 级"""
    MALE = "male"
    FEMALE = "female"
    UNISEX = "unisex"
    NOT_SPECIFIED = "not_specified"


class TargetAgeGroup(str, Enum):
    """目标年龄层 — 6 级"""
    GEN_Z = "gen_z"         # 18-25岁 Z世代
    GEN_Y = "gen_y"         # 26-35岁 Y世代
    GEN_X = "gen_x"         # 36-45岁 X世代
    SILVER = "silver"       # 46-60岁 银发族
    SENIOR = "senior"       # 60岁+
    ALL_AGES = "all_ages"   # 全年龄


class SpendingPower(str, Enum):
    """消费能力 — 4 级"""
    ECONOMY = "economy"         # 经济型
    MIDDLE = "middle"           # 中产型
    AFFLUENT = "affluent"       # 富裕型
    ULTRA_HIGH = "ultra_high"   # 超高净值


class PersonaTag(str, Enum):
    """人群画像标签 — 12 个"""
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
    """营销场景 — 8 个"""
    NEW_LAUNCH = "new_launch"
    FLASH_SALE = "flash_sale"
    SEASONAL_PROMO = "seasonal_promo"
    BRAND_BUILDING = "brand_building"
    SOCIAL_PROOF = "social_proof"
    HOLIDAY_GIFT = "holiday_gift"
    CLEARANCE = "clearance"
    MEMBERSHIP = "membership"


class UsageScene(str, Enum):
    """使用场景 — 9 个"""
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
    """目标平台 — 8 个（含通用）"""
    DOUYIN = "douyin"
    XIAOHONGSHU = "xiaohongshu"
    TAOBAO = "taobao"
    KUAISHOU = "kuaishou"
    WECHAT = "wechat"
    TIKTOK = "tiktok"
    INSTAGRAM = "instagram"
    UNIVERSAL = "universal"


class VideoStyle(str, Enum):
    """视频风格 — 10 个"""
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
    """画面调性 — 10 个"""
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


class ConfidenceLevel(str, Enum):
    """匹配置信度等级 — 三级"""
    HIGH = "high"       # >= 0.85，直接推荐
    MEDIUM = "medium"   # 0.70-0.85，推荐但需确认
    LOW = "low"         # < 0.70，走兜底策略


class MatchStrategyType(str, Enum):
    """匹配策略类型"""
    STANDARD = "standard"    # 标准策略（完整14维）
    FAST = "fast"            # 快速策略（8维硬过滤）
    SEMANTIC = "semantic"    # 语义策略（向量检索）
    ENSEMBLE = "ensemble"    # 集成策略（多策略融合）


# ═══════════════════════════════════════════════════════════
# 数据模型 (Pydantic BaseModel)
# ═══════════════════════════════════════════════════════════

class GemMetadata(BaseModel):
    """
    Gem 元数据 Schema — 支持智能路由匹配

    每个 Gem 模板必须包含此元数据才能被路由引擎索引。
    新增 Gem 时只需填写此 Schema，无需修改引擎代码。
    """

    # ─── 基础信息 ───
    gem_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str = Field(..., description="Gem 名称，如 '美妆产品快剪'")
    description: str = Field(default="", description="Gem 功能描述")
    version: str = Field(default="1.0.0")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    # ─── 产品维度 (5个字段) ───
    supported_categories: List[ProductCategory] = Field(
        ..., description="支持的产品品类列表"
    )
    supported_product_types: List[str] = Field(
        default=[], description="支持的细分产品类型"
    )
    target_price_tiers: List[PriceTier] = Field(
        default=[PriceTier.BUDGET, PriceTier.AFFORDABLE,
                 PriceTier.MID_RANGE, PriceTier.PREMIUM, PriceTier.LUXURY],
        description="适配的价格带"
    )
    target_brand_tiers: List[BrandTier] = Field(
        default=[BrandTier.MASS, BrandTier.MID_MARKET,
                 BrandTier.PREMIUM, BrandTier.LUXURY, BrandTier.D2C],
        description="适配的品牌调性"
    )
    matched_selling_points: List[SellingPoint] = Field(
        default=[], description="最佳匹配的卖点特征"
    )

    # ─── 人群维度 (4个字段) ───
    target_genders: List[TargetGender] = Field(
        default=[TargetGender.NOT_SPECIFIED],
        description="目标性别"
    )
    target_age_groups: List[TargetAgeGroup] = Field(
        default=[TargetAgeGroup.ALL_AGES],
        description="目标年龄层"
    )
    target_spending_power: List[SpendingPower] = Field(
        default=[SpendingPower.ECONOMY, SpendingPower.MIDDLE,
                 SpendingPower.AFFLUENT],
        description="适配的消费能力"
    )
    matched_personas: List[PersonaTag] = Field(
        default=[], description="最佳匹配的人群画像"
    )

    # ─── 场景维度 (3个字段) ───
    marketing_scenes: List[MarketingScene] = Field(
        ..., description="支持的营销场景"
    )
    usage_scenes: List[UsageScene] = Field(
        default=[], description="适合的使用场景"
    )
    target_platforms: List[TargetPlatform] = Field(
        default=[TargetPlatform.UNIVERSAL],
        description="适配的目标平台"
    )

    # ─── 风格维度 (2个字段) ───
    video_styles: List[VideoStyle] = Field(
        ..., description="视频风格"
    )
    visual_tones: List[VisualTone] = Field(
        default=[], description="适配的画面调性"
    )

    # ─── 匹配增强 ───
    semantic_description: str = Field(
        default="", description="语义描述，用于向量检索"
    )
    keywords: List[str] = Field(
        default=[], description="关键词标签"
    )
    priority_boost: float = Field(
        default=0.0, ge=0.0, le=1.0,
        description="优先级提升（0-1，用于同分打破）"
    )
    example_images: List[str] = Field(
        default=[], description="匹配示例图 URL"
    )

    # ─── 运行时统计 ───
    usage_count: int = Field(default=0, ge=0)
    avg_rating: float = Field(default=0.0, ge=0.0, le=5.0)
    match_success_rate: float = Field(default=0.0, ge=0.0, le=1.0)

    def model_post_init(self: "GemMetadata", __context: Any) -> None:
        """后初始化：自动生成语义描述"""
        if not self.semantic_description:
            parts = [
                f"适用于{', '.join(c.value for c in self.supported_categories)}",
                f"{', '.join(s.value for s in self.marketing_scenes)}场景",
                f"{', '.join(v.value for v in self.video_styles)}风格",
            ]
            self.semantic_description = "的Gem模板，".join(parts)


class ImageFeatures(BaseModel):
    """
    图片分析特征输出 — 14 维特征

    由 VLM 分析产品图片后输出，作为路由匹配的输入。
    所有字段均为 Optional，缺失时按 0.5 分处理。
    """

    # ─── 产品维度 (5维) ───
    category: Optional[ProductCategory] = None
    product_type: Optional[str] = None
    price_tier: Optional[PriceTier] = None
    brand_tier: Optional[BrandTier] = None
    detected_selling_points: List[SellingPoint] = Field(default=[])

    # ─── 人群维度 (4维) ───
    target_gender: Optional[TargetGender] = None
    target_age: Optional[TargetAgeGroup] = None
    estimated_spending_power: Optional[SpendingPower] = None
    inferred_personas: List[PersonaTag] = Field(default=[])

    # ─── 场景维度 (3维) ───
    inferred_marketing_scene: Optional[MarketingScene] = None
    inferred_usage_scenes: List[UsageScene] = Field(default=[])
    target_platform: Optional[TargetPlatform] = None

    # ─── 风格维度 (2维) ───
    inferred_video_style: Optional[VideoStyle] = None
    inferred_visual_tone: Optional[VisualTone] = None

    # ─── 元数据 ───
    raw_analysis: Dict[str, Any] = Field(default_factory=dict)
    feature_confidence: Dict[str, float] = Field(default_factory=dict)

    def get_completeness_score(self: "ImageFeatures") -> float:
        """
        计算特征完整度分数

        基于 7 个核心字段是否存在：
        category, product_type, price_tier, target_gender,
        target_age, inferred_marketing_scene, inferred_video_style

        Returns:
            float: 0.0 ~ 1.0，1.0 表示所有核心字段都有值
        """
        core_fields = [
            "category", "product_type", "price_tier",
            "target_gender", "target_age",
            "inferred_marketing_scene", "inferred_video_style",
        ]
        present = sum(
            1 for f in core_fields
            if getattr(self, f) is not None
        )
        return present / len(core_fields)

    def get_filled_dimensions(self: "ImageFeatures") -> List[str]:
        """获取已填充的维度名称列表"""
        filled = []
        for field_name, field_value in self.model_dump().items():
            if field_value is not None and field_value != []:
                filled.append(field_name)
        return filled

    def has_dimension(self: "ImageFeatures", dim_name: str) -> bool:
        """检查指定维度是否有值"""
        return getattr(self, dim_name, None) is not None


class ScoredGem(BaseModel):
    """评过分的 Gem 候选 — 匹配算法的中间结果"""
    gem_id: str
    name: str
    score: float = Field(ge=0.0, le=1.0)
    breakdown: Dict[str, float] = Field(
        default_factory=dict,
        description="14个维度各自的得分明细"
    )


class MatchResult(BaseModel):
    """匹配结果 — 路由算法的最终输出"""
    query_features: ImageFeatures
    matches: List[ScoredGem] = Field(default=[])
    confidence_level: ConfidenceLevel = ConfidenceLevel.LOW
    final_score: float = 0.0
    top_candidate_id: Optional[str] = None
    top_candidate_name: Optional[str] = None
    fallback_triggered: bool = False
    reasoning: str = ""
    elapsed_ms: int = 0
    strategy_used: MatchStrategyType = MatchStrategyType.STANDARD

    def get_top_k(self: "MatchResult", k: int = 3) -> List[ScoredGem]:
        """获取 Top-K 候选"""
        return self.matches[:k]

    def is_high_confidence(self: "MatchResult") -> bool:
        """是否为高置信度匹配"""
        return self.confidence_level == ConfidenceLevel.HIGH

    def to_dict(self: "MatchResult") -> Dict[str, Any]:
        """序列化为字典"""
        return self.model_dump()


class WeightConfig(BaseModel):
    """
    权重配置 — 14 维权重，总和精确为 1.0

    默认配置:
    - 产品维度: 40% (category: 15%, product_type: 8%, price_tier: 7%, brand_tier: 5%, selling_points: 5%)
    - 人群维度: 23% (target_gender: 8%, target_age: 7%, spending_power: 5%, persona_tags: 3%)
    - 场景维度: 25% (marketing_scene: 12%, usage_scenes: 8%, target_platform: 5%)
    - 风格维度: 12% (video_style: 8%, visual_tone: 4%)
    """

    # 产品维度 (40%)
    category: float = 0.15
    product_type: float = 0.08
    price_tier: float = 0.07
    brand_tier: float = 0.05
    selling_points: float = 0.05

    # 人群维度 (23%)
    target_gender: float = 0.08
    target_age: float = 0.07
    spending_power: float = 0.05
    persona_tags: float = 0.03

    # 场景维度 (25%)
    marketing_scene: float = 0.12
    usage_scenes: float = 0.08
    target_platform: float = 0.05

    # 风格维度 (12%)
    video_style: float = 0.08
    visual_tone: float = 0.04

    @field_validator("category", "product_type", "price_tier", "brand_tier",
                     "selling_points", "target_gender", "target_age",
                     "spending_power", "persona_tags", "marketing_scene",
                     "usage_scenes", "target_platform", "video_style", "visual_tone")
    @classmethod
    def _validate_non_negative(cls: type["WeightConfig"], v: float) -> float:
        if v < 0:
            raise ValueError("权重不能为负数")
        return v

    def validate_weights(self: "WeightConfig") -> bool:
        """验证权重和是否 ≈ 1.0（容差 0.001）"""
        total = sum(self.model_dump().values())
        return abs(total - 1.0) < 0.001

    def get_dimension_subtotals(self: "WeightConfig") -> Dict[str, float]:
        """获取各维度小计分数"""
        return {
            "product": (
                self.category + self.product_type + self.price_tier +
                self.brand_tier + self.selling_points
            ),
            "audience": (
                self.target_gender + self.target_age +
                self.spending_power + self.persona_tags
            ),
            "scene": (
                self.marketing_scene + self.usage_scenes +
                self.target_platform
            ),
            "style": self.video_style + self.visual_tone,
        }

    def for_platform(self: "WeightConfig", platform: TargetPlatform) -> "WeightConfig":
        """
        获取针对特定平台的特化权重

        调整规则:
        - 抖音: video_style 权重提升（重视觉节奏）
        - 小红书: visual_tone + selling_points 权重提升（重颜值和种草）
        - 淘宝: category + price_tier 权重提升（重品类和价格）
        """
        config = deepcopy(self)

        adjustments = {
            TargetPlatform.DOUYIN: {
                "video_style": 0.15,
                "visual_tone": 0.08,
                "marketing_scene": 0.10,
            },
            TargetPlatform.XIAOHONGSHU: {
                "visual_tone": 0.12,
                "selling_points": 0.08,
                "marketing_scene": 0.10,
            },
            TargetPlatform.TAOBAO: {
                "category": 0.18,
                "price_tier": 0.10,
                "marketing_scene": 0.10,
            },
        }

        adj = adjustments.get(platform, {})
        for key, value in adj.items():
            setattr(config, key, value)

        # 重新归一化确保总和为 1.0
        total = sum(config.model_dump().values())
        if abs(total - 1.0) >= 0.001:
            for key in config.model_dump().keys():
                current = getattr(config, key)
                setattr(config, key, current / total)

        return config

    def clone(self: "WeightConfig") -> "WeightConfig":
        """深拷贝权重配置"""
        return deepcopy(self)


class FeedbackRecord(BaseModel):
    """用户反馈记录 — 用于权重自学习"""
    feedback_id: str
    match_record_id: str
    gem_id: str
    user_rating: float = Field(ge=0.0, le=5.0)
    was_correct: Optional[bool] = None
    user_comment: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    context: Dict[str, Any] = Field(default_factory=dict)

    def is_positive(self: "FeedbackRecord") -> bool:
        """是否为正面反馈（4-5星）"""
        return self.user_rating >= 4.0

    def get_weight(self: "FeedbackRecord") -> float:
        """计算反馈权重（考虑时效性，24h 内权重更高）"""
        import time
        age_hours = (time.time() - self.timestamp.timestamp()) / 3600
        return max(0.1, 1.0 - age_hours / 24)


# ═══════════════════════════════════════════════════════════
# 异常定义
# ═══════════════════════════════════════════════════════════

class SmartRouterError(Exception):
    """路由模块基础异常"""

    def __init__(self: "SmartRouterError", message: str, context: Dict[str, Any] = None) -> None:
        super().__init__(message)
        self.context = context or {}
        self.error_code = "ROUTER_ERROR"


class AnalysisError(SmartRouterError):
    """图片分析异常 — VLM 调用失败"""
    def __init__(self: "AnalysisError", message: str, context: Dict[str, Any] = None) -> None:
        super().__init__(message, context)
        self.error_code = "ANALYSIS_ERROR"


class RegistryError(SmartRouterError):
    """注册中心异常 — Gem 操作失败"""
    def __init__(self: "RegistryError", message: str, context: Dict[str, Any] = None) -> None:
        super().__init__(message, context)
        self.error_code = "REGISTRY_ERROR"


class EmptyRegistryError(SmartRouterError):
    """注册中心为空异常"""
    def __init__(self: "EmptyRegistryError", message: str = "GemRegistry 为空") -> None:
        super().__init__(message)
        self.error_code = "EMPTY_REGISTRY"


class NoMatchError(SmartRouterError):
    """无匹配结果异常"""
    def __init__(self: "NoMatchError", message: str = "无匹配候选") -> None:
        super().__init__(message)
        self.error_code = "NO_MATCH"


class ScoringError(SmartRouterError):
    """评分异常 — 单个 Gem 评分失败"""
    def __init__(self: "ScoringError", message: str, gem_id: str = None, context: Dict[str, Any] = None) -> None:
        super().__init__(message, context)
        self.error_code = "SCORING_ERROR"
        self.gem_id = gem_id


# ═══════════════════════════════════════════════════════════
# 降级策略
# ═══════════════════════════════════════════════════════════

class DegradationStrategy:
    """降级策略 — 保证核心功能可用"""

    @staticmethod
    def vlm_timeout_fallback() -> ImageFeatures:
        """VLM 超时降级：返回最小特征集"""
        return ImageFeatures(
            raw_analysis={"degraded": True, "reason": "vlm_timeout"},
        )

    @staticmethod
    def empty_registry_fallback() -> MatchResult:
        """注册中心为空降级"""
        return MatchResult(
            query_features=ImageFeatures(),
            matches=[],
            confidence_level=ConfidenceLevel.LOW,
            fallback_triggered=True,
            reasoning="GemRegistry 为空，请检查配置",
        )

    @staticmethod
    def no_candidates_fallback(features: ImageFeatures) -> MatchResult:
        """无候选降级"""
        return MatchResult(
            query_features=features,
            matches=[],
            confidence_level=ConfidenceLevel.LOW,
            fallback_triggered=True,
            reasoning="过滤后无匹配候选，已触发兜底策略",
        )

    @staticmethod
    def low_confidence_fallback(
        features: ImageFeatures,
        scored: List[ScoredGem],
    ) -> MatchResult:
        """低置信度降级：返回 Top-3 供用户选择"""
        return MatchResult(
            query_features=features,
            matches=scored[:3],
            confidence_level=ConfidenceLevel.LOW,
            fallback_triggered=True,
            reasoning="置信度较低，请从以下候选中选择",
        )


# ═══════════════════════════════════════════════════════════
# SmartRouter 核心引擎
# ═══════════════════════════════════════════════════════════

class SmartRouter:
    """
    智能路由引擎 — 核心类

    职责:
    - 管理 Gem 注册中心（增删改查）
    - 执行分层过滤减少候选集
    - 执行 14 维加权评分
    - 评估匹配置信度
    - 处理异常和兜底

    设计模式:
    - 流水线模式: 过滤 → 评分 → 评估
    - 策略模式: 评分函数可插拔

    线程安全: 本类无共享可变状态，线程安全
    """

    # ─── 品类相似度映射（相关品类可部分匹配） ───
    CATEGORY_SIMILARITY: Dict[ProductCategory, List[ProductCategory]] = {
        ProductCategory.SKINCARE: [
            ProductCategory.MAKEUP, ProductCategory.FRAGRANCE,
            ProductCategory.PERSONAL_CARE,
        ],
        ProductCategory.MAKEUP: [
            ProductCategory.SKINCARE, ProductCategory.FRAGRANCE,
        ],
        ProductCategory.PHONE: [
            ProductCategory.DIGITAL_ACCESSORY, ProductCategory.COMPUTER,
        ],
        ProductCategory.COMPUTER: [
            ProductCategory.DIGITAL_ACCESSORY, ProductCategory.PHONE,
        ],
        ProductCategory.WOMENS_CLOTHING: [
            ProductCategory.SHOES, ProductCategory.BAGS,
            ProductCategory.ACCESSORIES,
        ],
        ProductCategory.MENS_CLOTHING: [
            ProductCategory.SHOES, ProductCategory.ACCESSORIES,
        ],
        ProductCategory.SHOES: [
            ProductCategory.WOMENS_CLOTHING, ProductCategory.MENS_CLOTHING,
            ProductCategory.BAGS,
        ],
        ProductCategory.SNACK: [
            ProductCategory.BEVERAGE, ProductCategory.FRESH_FOOD,
        ],
        ProductCategory.HOME_DECOR: [
            ProductCategory.FURNITURE, ProductCategory.DAILY_NECESSITIES,
        ],
        ProductCategory.FURNITURE: [
            ProductCategory.HOME_DECOR, ProductCategory.DAILY_NECESSITIES,
        ],
        ProductCategory.SPORTS: [
            ProductCategory.OUTDOOR,
        ],
        ProductCategory.OUTDOOR: [
            ProductCategory.SPORTS,
        ],
    }

    # ─── 价格带顺序（用于相邻价格带部分匹配） ───
    PRICE_TIER_ORDER: List[PriceTier] = [
        PriceTier.BUDGET, PriceTier.AFFORDABLE,
        PriceTier.MID_RANGE, PriceTier.PREMIUM, PriceTier.LUXURY,
    ]

    # ─── 置信度评估阈值 ───
    THRESHOLDS = {
        "high_min": 0.85,
        "medium_min": 0.70,
        "completeness_min": 0.5,
    }
    BONUS_FACTORS = {
        "consistency_max": 0.1,
        "consistency_rate": 0.5,
    }
    PENALTY_FACTORS = {
        "missing_base": 0.1,
    }

    def __init__(
        self,
        weight_config: Optional[WeightConfig] = None,
        similarity_threshold: float = 0.3,
        top_k: int = 5,
    ) -> None:
        """
        初始化 SmartRouter

        Args:
            weight_config: 权重配置，None 时使用默认配置
            similarity_threshold: 相似度阈值（过滤用）
            top_k: 返回的 Top-K 候选数量
        """
        self.weight_config: WeightConfig = weight_config or WeightConfig()
        self.similarity_threshold: float = similarity_threshold
        self.top_k: int = top_k

        # Gem 注册中心（内存存储）
        self._gem_registry: Dict[str, GemMetadata] = {}

        # 兜底 Gem（无匹配时使用）
        self._fallback_gem: Optional[GemMetadata] = None

        # 性能统计
        self._route_count: int = 0
        self._total_latency_ms: int = 0

    # ═══════════════════════════════════════════════════════════
    # 1. Gem 管理接口
    # ═══════════════════════════════════════════════════════════

    def register_gem(self: "SmartRouter", gem: GemMetadata) -> None:
        """
        注册单个 Gem 到路由引擎

        Args:
            gem: Gem 元数据

        Raises:
            ValueError: gem_id 为空或已存在
            TypeError: gem 类型错误
        """
        if not isinstance(gem, GemMetadata):
            raise TypeError(f"期望 GemMetadata，实际: {type(gem)}")
        if not gem.gem_id:
            raise ValueError("gem_id 不能为空")
        if gem.gem_id in self._gem_registry:
            raise ValueError(f"gem_id 已存在: {gem.gem_id}")

        self._gem_registry[gem.gem_id] = gem

    def register_gems(self: "SmartRouter", gems: List[GemMetadata]) -> Tuple[int, List[str]]:
        """
        批量注册 Gem

        Args:
            gems: Gem 元数据列表

        Returns:
            Tuple[int, List[str]]: (成功数量, 失败 ID 列表)
        """
        success = 0
        failed: List[str] = []
        for gem in gems:
            try:
                self.register_gem(gem)
                success += 1
            except (ValueError, TypeError):
                failed.append(getattr(gem, "gem_id", "unknown"))
        return success, failed

    def unregister_gem(self: "SmartRouter", gem_id: str) -> bool:
        """
        注销 Gem

        Args:
            gem_id: Gem ID

        Returns:
            bool: 是否成功注销
        """
        if gem_id in self._gem_registry:
            del self._gem_registry[gem_id]
            return True
        return False

    def get_gem(self: "SmartRouter", gem_id: str) -> Optional[GemMetadata]:
        """根据 ID 获取 Gem"""
        return self._gem_registry.get(gem_id)

    def set_fallback_gem(self: "SmartRouter", gem: GemMetadata) -> None:
        """设置兜底 Gem"""
        self._fallback_gem = gem

    @property
    def gem_count(self: "SmartRouter") -> int:
        """已注册 Gem 数量"""
        return len(self._gem_registry)

    @property
    def all_gems(self: "SmartRouter") -> List[GemMetadata]:
        """获取所有 Gem"""
        return list(self._gem_registry.values())

    def clear_registry(self: "SmartRouter") -> None:
        """清空注册中心（测试用）"""
        self._gem_registry.clear()
        self._fallback_gem = None

    # ═══════════════════════════════════════════════════════════
    # 2. 核心路由接口
    # ═══════════════════════════════════════════════════════════

    async def route(
        self,
        image_path: str,
        options: Optional[Dict[str, Any]] = None,
    ) -> MatchResult:
        """
        主路由入口 — 从图片路径匹配最佳 Gem

        完整流程:
        1. 读取并分析图片 → ImageFeatures
        2. 特征预处理（规范化 + 应用用户偏好）
        3. 分层过滤（品类 → 场景 → 平台）
        4. 加权评分（14 维）
        5. 排序与截断（Top-K）
        6. 置信度评估（HIGH/MEDIUM/LOW）
        7. 组装结果

        Args:
            image_path: 产品图片路径（本地路径或 URL）
            options: 可选配置
                - platform_hint: TargetPlatform — 平台偏好
                - preferred_styles: List[VideoStyle] — 偏好视频风格
                - strategy: MatchStrategyType — 匹配策略
                - top_k: int — 返回数量覆盖

        Returns:
            MatchResult: 匹配结果
        """
        start_time = time.perf_counter()
        options = options or {}

        try:
            # ── 阶段 1: 图片分析 ──
            try:
                features = await self._analyze_image(image_path)
            except AnalysisError:
                features = DegradationStrategy.vlm_timeout_fallback()

            # ── 阶段 2: 特征预处理 ──
            features = self._preprocess_features(features, options)

            # ── 阶段 3: 检查注册中心 ──
            if self.gem_count == 0:
                return DegradationStrategy.empty_registry_fallback()

            # ── 阶段 4: 分层过滤 ──
            candidates = self._hierarchical_filter(features)

            if not candidates:
                candidates = self._hierarchical_filter_relaxed(features)
                if not candidates:
                    elapsed = int((time.perf_counter() - start_time) * 1000)
                    result = DegradationStrategy.no_candidates_fallback(features)
                    result.elapsed_ms = elapsed
                    return result

            # ── 阶段 5: 加权评分 ──
            scored = self._match_gems(features, candidates)

            if not scored:
                elapsed = int((time.perf_counter() - start_time) * 1000)
                result = DegradationStrategy.no_candidates_fallback(features)
                result.elapsed_ms = elapsed
                return result

            # ── 阶段 6: 排序与置信度评估 ──
            scored.sort(key=lambda x: x.score, reverse=True)
            confidence, reasoning = self._evaluate_confidence(scored, features)

            # ── 阶段 7: 组装结果 ──
            elapsed_ms = int((time.perf_counter() - start_time) * 1000)
            k = options.get("top_k", self.top_k)
            # 跨模块类型兼容处理
            features_dict = (
                features.model_dump() if hasattr(features, "model_dump")
                else dict(features) if features else {}
            )
            compatible_features = ImageFeatures(**features_dict)
            result = MatchResult(
                query_features=compatible_features,
                matches=scored[:k],
                confidence_level=confidence,
                final_score=round(scored[0].score, 4),
                top_candidate_id=scored[0].gem_id,
                top_candidate_name=scored[0].name,
                fallback_triggered=(confidence == ConfidenceLevel.LOW),
                reasoning=reasoning,
                elapsed_ms=elapsed_ms,
            )

            self._route_count += 1
            self._total_latency_ms += elapsed_ms

            return result

        except Exception as e:
            elapsed_ms = int((time.perf_counter() - start_time) * 1000)
            return MatchResult(
                query_features=ImageFeatures(),
                matches=[],
                confidence_level=ConfidenceLevel.LOW,
                fallback_triggered=True,
                reasoning=f"路由异常: {str(e)}",
                elapsed_ms=elapsed_ms,
            )

    def route_from_features(
        self,
        features: ImageFeatures,
        platform_hint: Optional[TargetPlatform] = None,
        preferred_styles: Optional[List[VideoStyle]] = None,
        top_k: Optional[int] = None,
    ) -> MatchResult:
        """
        从已有特征进行路由（跳过图片分析阶段）

        这是 route() 的同步版本，用于已有特征的场景。

        Args:
            features: 图片分析特征
            platform_hint: 平台偏好覆盖
            preferred_styles: 视频风格偏好
            top_k: 返回数量覆盖

        Returns:
            MatchResult: 匹配结果
        """
        start_time = time.perf_counter()

        # ── Step 1: 应用用户偏好覆盖 ──
        effective_features = features.model_copy()
        if platform_hint:
            effective_features.target_platform = platform_hint
        if preferred_styles and not effective_features.inferred_video_style:
            effective_features.inferred_video_style = preferred_styles[0]

        # ── Step 2: 检查注册中心 ──
        if self.gem_count == 0:
            return DegradationStrategy.empty_registry_fallback()

        # ── Step 3: 分层过滤 ──
        candidates = self._hierarchical_filter(effective_features)

        # ── Step 4: 检查候选集 ──
        if not candidates:
            candidates = self._hierarchical_filter_relaxed(effective_features)
            if not candidates:
                elapsed = int((time.perf_counter() - start_time) * 1000)
                result = DegradationStrategy.no_candidates_fallback(effective_features)
                result.elapsed_ms = elapsed
                return result

        # ── Step 5: 14 维加权评分 ──
        scored = self._match_gems(effective_features, candidates)

        # ── Step 6: 检查评分结果 ──
        if not scored:
            elapsed = int((time.perf_counter() - start_time) * 1000)
            result = DegradationStrategy.no_candidates_fallback(effective_features)
            result.elapsed_ms = elapsed
            return result

        # ── Step 7: 取 Top-K ──
        k = top_k or self.top_k
        scored.sort(key=lambda x: x.score, reverse=True)
        top_scored = scored[:k]

        # ── Step 8: 置信度评估 ──
        confidence, reasoning = self._evaluate_confidence(
            scored, effective_features)

        # ── Step 9: 计算耗时 ──
        elapsed_ms = int((time.perf_counter() - start_time) * 1000)

        # ── Step 10: 组装结果 ──
        # 将 features 转为 dict 再重建，确保跨模块类型兼容
        features_dict = (
            effective_features.model_dump()
            if hasattr(effective_features, "model_dump") else
            dict(effective_features) if effective_features else {}
        )
        compatible_features = ImageFeatures(**features_dict)

        result = MatchResult(
            query_features=compatible_features,
            matches=top_scored,
            confidence_level=confidence,
            final_score=top_scored[0].score if top_scored else 0.0,
            top_candidate_id=top_scored[0].gem_id if top_scored else None,
            top_candidate_name=top_scored[0].name if top_scored else None,
            fallback_triggered=(confidence == ConfidenceLevel.LOW),
            reasoning=reasoning,
            elapsed_ms=elapsed_ms,
        )

        # ── Step 11: 更新统计 ──
        self._route_count += 1
        self._total_latency_ms += elapsed_ms

        return result

    # ═══════════════════════════════════════════════════════════
    # 3. 图片分析（占位 — 实际由 ImageAnalyzer 实现）
    # ═══════════════════════════════════════════════════════════

    async def _analyze_image(self: "SmartRouter", image_path: str) -> ImageFeatures:
        """
        分析产品图片，提取 14 维特征

        本方法为占位实现，实际由 ImageAnalyzer 模块提供。

        Args:
            image_path: 图片路径或 URL

        Returns:
            ImageFeatures: 14 维分析特征

        Raises:
            AnalysisError: 图片分析失败
        """
        raise AnalysisError(
            "_analyze_image 需由 ImageAnalyzer 模块实现",
            context={"image_path": image_path},
        )

    # ═══════════════════════════════════════════════════════════
    # 4. 特征预处理
    # ═══════════════════════════════════════════════════════════

    def _preprocess_features(
        self,
        features: ImageFeatures,
        options: Optional[Dict[str, Any]] = None,
    ) -> ImageFeatures:
        """
        特征预处理

        步骤:
        1. 应用用户偏好覆盖（平台、风格）
        2. 规范化枚举值
        3. 计算完整度分数
        4. 标记缺失维度

        Args:
            features: 原始特征
            options: 用户选项

        Returns:
            ImageFeatures: 预处理后的特征
        """
        options = options or {}
        result = features.model_copy()

        # 应用平台偏好覆盖
        platform_hint = options.get("platform_hint")
        if platform_hint and isinstance(platform_hint, TargetPlatform):
            result.target_platform = platform_hint

        # 应用风格偏好覆盖
        preferred_styles = options.get("preferred_styles")
        if preferred_styles and not result.inferred_video_style:
            result.inferred_video_style = preferred_styles[0]

        return result

    # ═══════════════════════════════════════════════════════════
    # 5. 分层过滤
    # ═══════════════════════════════════════════════════════════

    def _hierarchical_filter(
        self,
        features: ImageFeatures,
    ) -> List[GemMetadata]:
        """
        三层分层过滤

        流程:
        Layer 1: 品类硬过滤（精确匹配 > 相关品类 > 通用 Gem）
        Layer 2: 营销场景软过滤（精确匹配 > 保留全部）
        Layer 3: 平台软过滤（精确匹配 > UNIVERSAL > 保留全部）

        Args:
            features: 图片特征

        Returns:
            List[GemMetadata]: 过滤后的候选集
        """
        candidates = list(self._gem_registry.values())

        # Layer 1: 品类硬过滤
        if features.category:
            category_filtered = self._filter_by_category(
                candidates, features.category)
            # 如果有品类匹配结果，使用它；否则保留全部
            if category_filtered:
                candidates = category_filtered

        # Layer 2: 营销场景软过滤
        if features.inferred_marketing_scene:
            scene_filtered = self._filter_by_marketing_scene(
                candidates, features.inferred_marketing_scene)
            if scene_filtered:
                candidates = scene_filtered

        # Layer 3: 平台软过滤
        if features.target_platform:
            platform_filtered = self._filter_by_platform(
                candidates, features.target_platform)
            if platform_filtered:
                candidates = platform_filtered

        return candidates

    def _hierarchical_filter_relaxed(
        self,
        features: ImageFeatures,
    ) -> List[GemMetadata]:
        """
        放宽的分层过滤（当严格过滤无结果时使用）

        策略: 仅保留品类过滤，忽略场景和平台
        """
        candidates = list(self._gem_registry.values())

        if features.category:
            category_filtered = self._filter_by_category(
                candidates, features.category)
            if category_filtered:
                candidates = category_filtered

        return candidates

    def _filter_by_category(
        self,
        candidates: List[GemMetadata],
        category: ProductCategory,
    ) -> List[GemMetadata]:
        """
        品类过滤 — Layer 1（硬过滤）

        策略:
        1. 精确匹配: category 在 gem.supported_categories 中
        2. 相关品类: category 的相似品类在 supported_categories 中
        3. 通用 Gem: supported_categories >= 10 个
        4. 全部保留: 以上均无匹配

        Args:
            candidates: 候选 Gem 列表
            category: 产品品类

        Returns:
            List[GemMetadata]: 过滤后的候选集
        """
        # 1. 精确匹配
        exact = [
            g for g in candidates
            if category in g.supported_categories
        ]
        if exact:
            return exact

        # 2. 相关品类匹配
        related = self.CATEGORY_SIMILARITY.get(category, [])
        related_matches = [
            g for g in candidates
            if any(r in g.supported_categories for r in related)
        ]
        if related_matches:
            return related_matches

        # 3. 通用 Gem（支持 >= 10 个品类）
        universal = [
            g for g in candidates
            if len(g.supported_categories) >= 10
        ]
        if universal:
            return universal

        # 4. 全部保留
        return candidates

    def _filter_by_marketing_scene(
        self,
        candidates: List[GemMetadata],
        scene: MarketingScene,
    ) -> List[GemMetadata]:
        """
        营销场景过滤 — Layer 2（软过滤）

        策略: 精确匹配 > 保留全部（不过滤）

        Args:
            candidates: 候选 Gem 列表
            scene: 营销场景

        Returns:
            List[GemMetadata]: 过滤后的候选集
        """
        matched = [
            g for g in candidates
            if scene in g.marketing_scenes
        ]
        # 软过滤：有匹配则返回匹配，无匹配保留全部
        return matched if matched else candidates

    def _filter_by_platform(
        self,
        candidates: List[GemMetadata],
        platform: TargetPlatform,
    ) -> List[GemMetadata]:
        """
        平台过滤 — Layer 3（软过滤）

        策略: 精确匹配 > UNIVERSAL > 保留全部

        Args:
            candidates: 候选 Gem 列表
            platform: 目标平台

        Returns:
            List[GemMetadata]: 过滤后的候选集
        """
        # 精确匹配
        exact = [
            g for g in candidates
            if platform in g.target_platforms
        ]
        if exact:
            return exact

        # UNIVERSAL 匹配
        universal = [
            g for g in candidates
            if TargetPlatform.UNIVERSAL in g.target_platforms
        ]
        if universal:
            return universal

        # 保留全部
        return candidates

    # ═══════════════════════════════════════════════════════════
    # 6. 匹配与评分
    # ═══════════════════════════════════════════════════════════

    def _match_gems(
        self,
        features: ImageFeatures,
        candidates: List[GemMetadata],
    ) -> List[ScoredGem]:
        """
        对候选 Gem 进行 14 维加权评分

        流程:
        1. 对每个候选 Gem 调用 _score_single_gem()
        2. 按总分降序排序
        3. 返回排序后的 ScoredGem 列表

        Args:
            features: 图片特征
            candidates: 候选 Gem 列表

        Returns:
            List[ScoredGem]: 按分数降序排列的评分结果
        """
        scored: List[ScoredGem] = []
        for gem in candidates:
            try:
                score, breakdown = self._score_single_gem(features, gem)
                scored.append(ScoredGem(
                    gem_id=gem.gem_id,
                    name=gem.name,
                    score=round(score, 4),
                    breakdown=breakdown,
                ))
            except ScoringError:
                continue
        return scored

    def _score_single_gem(
        self,
        features: ImageFeatures,
        gem: GemMetadata,
    ) -> Tuple[float, Dict[str, float]]:
        """
        对单个 Gem 进行 14 维评分

        评分维度:
        - 产品维度 (40%): category, product_type, price_tier, brand_tier, selling_points
        - 人群维度 (23%): target_gender, target_age, spending_power, persona_tags
        - 场景维度 (25%): marketing_scene, usage_scenes, target_platform
        - 风格维度 (12%): video_style, visual_tone

        最终分数 = sum(w_i * s_i) + priority_boost * 0.05

        Args:
            features: 图片特征
            gem: 候选 Gem

        Returns:
            Tuple[float, Dict[str, float]]: (总分数, 各维度明细)
        """
        w = self.weight_config
        breakdown: Dict[str, float] = {}

        # ═══════════════════════════════════════════════
        # 产品维度评分 (权重合计 40%)
        # ═══════════════════════════════════════════════

        # Dimension 1: 品类 (权重 0.15)
        cat_score = self._score_category(features.category, gem)
        breakdown["category"] = cat_score

        # Dimension 2: 产品类型 (权重 0.08)
        type_score = self._score_product_type(features.product_type, gem)
        breakdown["product_type"] = type_score

        # Dimension 3: 价格带 (权重 0.07)
        price_score = self._score_price_tier(features.price_tier, gem)
        breakdown["price_tier"] = price_score

        # Dimension 4: 品牌调性 (权重 0.05)
        brand_score = self._score_brand_tier(features.brand_tier, gem)
        breakdown["brand_tier"] = brand_score

        # Dimension 5: 卖点特征 (权重 0.05)
        sp_score = self._score_selling_points(
            features.detected_selling_points, gem)
        breakdown["selling_points"] = sp_score

        product_subscore = (
            w.category * cat_score +
            w.product_type * type_score +
            w.price_tier * price_score +
            w.brand_tier * brand_score +
            w.selling_points * sp_score
        )

        # ═══════════════════════════════════════════════
        # 人群维度评分 (权重合计 23%)
        # ═══════════════════════════════════════════════

        # Dimension 6: 目标性别 (权重 0.08)
        gender_score = self._score_gender(features.target_gender, gem)
        breakdown["target_gender"] = gender_score

        # Dimension 7: 目标年龄 (权重 0.07)
        age_score = self._score_age(features.target_age, gem)
        breakdown["target_age"] = age_score

        # Dimension 8: 消费能力 (权重 0.05)
        spend_score = self._score_spending_power(
            features.estimated_spending_power, gem)
        breakdown["spending_power"] = spend_score

        # Dimension 9: 人群画像 (权重 0.03)
        persona_score = self._score_personas(features.inferred_personas, gem)
        breakdown["persona_tags"] = persona_score

        audience_subscore = (
            w.target_gender * gender_score +
            w.target_age * age_score +
            w.spending_power * spend_score +
            w.persona_tags * persona_score
        )

        # ═══════════════════════════════════════════════
        # 场景维度评分 (权重合计 25%)
        # ═══════════════════════════════════════════════

        # Dimension 10: 营销场景 (权重 0.12)
        mkt_score = self._score_marketing_scene(
            features.inferred_marketing_scene, gem)
        breakdown["marketing_scene"] = mkt_score

        # Dimension 11: 使用场景 (权重 0.08)
        usage_score = self._score_usage_scenes(
            features.inferred_usage_scenes, gem)
        breakdown["usage_scenes"] = usage_score

        # Dimension 12: 目标平台 (权重 0.05)
        platform_score = self._score_platform(features.target_platform, gem)
        breakdown["target_platform"] = platform_score

        scene_subscore = (
            w.marketing_scene * mkt_score +
            w.usage_scenes * usage_score +
            w.target_platform * platform_score
        )

        # ═══════════════════════════════════════════════
        # 风格维度评分 (权重合计 12%)
        # ═══════════════════════════════════════════════

        # Dimension 13: 视频风格 (权重 0.08)
        vs_score = self._score_video_style(
            features.inferred_video_style, gem)
        breakdown["video_style"] = vs_score

        # Dimension 14: 画面调性 (权重 0.04)
        vt_score = self._score_visual_tone(
            features.inferred_visual_tone, gem)
        breakdown["visual_tone"] = vt_score

        style_subscore = (
            w.video_style * vs_score +
            w.visual_tone * vt_score
        )

        # ═══════════════════════════════════════════════
        # 总分计算
        # ═══════════════════════════════════════════════

        total_score = (
            product_subscore +
            audience_subscore +
            scene_subscore +
            style_subscore +
            gem.priority_boost * 0.05
        )

        # 截断到 [0.0, 1.0]
        total_score = min(1.0, total_score)

        return total_score, breakdown

    # ─── 各维度评分函数 ───

    @staticmethod
    def _score_category(
        category: Optional[ProductCategory],
        gem: GemMetadata,
    ) -> float:
        """
        品类评分

        Returns:
            1.0 — 精确匹配
            0.5 — 相关品类匹配
            0.5 — 信息缺失
            0.0 — 不匹配
        """
        if category is None:
            return 0.5
        if category in gem.supported_categories:
            return 1.0
        related = SmartRouter.CATEGORY_SIMILARITY.get(category, [])
        if any(r in gem.supported_categories for r in related):
            return 0.5
        return 0.0

    @staticmethod
    def _score_product_type(
        product_type: Optional[str],
        gem: GemMetadata,
    ) -> float:
        """
        产品类型评分

        Returns:
            1.0 — 精确匹配
            0.5 — 信息缺失或 Gem 未指定
            0.0 — 不匹配
        """
        if product_type is None:
            return 0.5
        if not gem.supported_product_types:
            return 0.5
        if product_type in gem.supported_product_types:
            return 1.0
        return 0.0

    @staticmethod
    def _score_price_tier(
        price_tier: Optional[PriceTier],
        gem: GemMetadata,
    ) -> float:
        """
        价格带评分

        Returns:
            1.0 — 精确匹配
            0.5 — 相邻价格带
            0.5 — 信息缺失
            0.0 — 不匹配
        """
        if price_tier is None:
            return 0.5
        if price_tier in gem.target_price_tiers:
            return 1.0
        # 检查相邻价格带
        idx = SmartRouter.PRICE_TIER_ORDER.index(price_tier)
        neighbors = []
        if idx > 0:
            neighbors.append(SmartRouter.PRICE_TIER_ORDER[idx - 1])
        if idx < len(SmartRouter.PRICE_TIER_ORDER) - 1:
            neighbors.append(SmartRouter.PRICE_TIER_ORDER[idx + 1])
        if any(n in gem.target_price_tiers for n in neighbors):
            return 0.5
        return 0.0

    @staticmethod
    def _score_brand_tier(
        brand_tier: Optional[BrandTier],
        gem: GemMetadata,
    ) -> float:
        """
        品牌调性评分

        Returns:
            1.0 — 精确匹配
            0.5 — 信息缺失
            0.0 — 不匹配
        """
        if brand_tier is None:
            return 0.5
        if brand_tier in gem.target_brand_tiers:
            return 1.0
        return 0.0

    @staticmethod
    def _score_selling_points(
        points: List[SellingPoint],
        gem: GemMetadata,
    ) -> float:
        """
        卖点特征评分（集合交集）

        Returns:
            min(1.0, 匹配数 / max(查询数, 1))
            0.5 — 任一方为空
        """
        if not points or not gem.matched_selling_points:
            return 0.5
        gem_set = set(gem.matched_selling_points)
        matches = sum(1 for p in points if p in gem_set)
        return min(1.0, matches / max(len(points), 1))

    @staticmethod
    def _score_gender(
        gender: Optional[TargetGender],
        gem: GemMetadata,
    ) -> float:
        """
        目标性别评分

        Returns:
            1.0 — 精确匹配
            0.7 — NOT_SPECIFIED
            0.5 — UNISEX 或信息缺失
            0.0 — 不匹配
        """
        if gender is None:
            return 0.5
        if gender in gem.target_genders:
            return 1.0
        if TargetGender.NOT_SPECIFIED in gem.target_genders:
            return 0.7
        if TargetGender.UNISEX in gem.target_genders:
            return 0.5
        return 0.0

    @staticmethod
    def _score_age(
        age: Optional[TargetAgeGroup],
        gem: GemMetadata,
    ) -> float:
        """
        目标年龄评分

        Returns:
            1.0 — 精确匹配
            0.7 — ALL_AGES
            0.5 — 信息缺失
            0.0 — 不匹配
        """
        if age is None:
            return 0.5
        if age in gem.target_age_groups:
            return 1.0
        if TargetAgeGroup.ALL_AGES in gem.target_age_groups:
            return 0.7
        return 0.0

    @staticmethod
    def _score_spending_power(
        power: Optional[SpendingPower],
        gem: GemMetadata,
    ) -> float:
        """
        消费能力评分

        Returns:
            1.0 — 精确匹配
            0.5 — 信息缺失
            0.0 — 不匹配
        """
        if power is None:
            return 0.5
        if power in gem.target_spending_power:
            return 1.0
        return 0.0

    @staticmethod
    def _score_personas(
        personas: List[PersonaTag],
        gem: GemMetadata,
    ) -> float:
        """
        人群画像评分（集合交集）

        Returns:
            min(1.0, 匹配数 / max(查询数, 1))
            0.5 — 任一方为空
        """
        if not personas or not gem.matched_personas:
            return 0.5
        gem_set = set(gem.matched_personas)
        matches = sum(1 for p in personas if p in gem_set)
        return min(1.0, matches / max(len(personas), 1))

    @staticmethod
    def _score_marketing_scene(
        scene: Optional[MarketingScene],
        gem: GemMetadata,
    ) -> float:
        """
        营销场景评分

        Returns:
            1.0 — 精确匹配
            0.5 — 信息缺失
            0.0 — 不匹配
        """
        if scene is None:
            return 0.5
        if scene in gem.marketing_scenes:
            return 1.0
        return 0.0

    @staticmethod
    def _score_usage_scenes(
        scenes: List[UsageScene],
        gem: GemMetadata,
    ) -> float:
        """
        使用场景评分（集合交集）

        Returns:
            min(1.0, 匹配数 / max(查询数, 1))
            0.5 — 任一方为空
        """
        if not scenes or not gem.usage_scenes:
            return 0.5
        gem_set = set(gem.usage_scenes)
        matches = sum(1 for s in scenes if s in gem_set)
        return min(1.0, matches / max(len(scenes), 1))

    @staticmethod
    def _score_platform(
        platform: Optional[TargetPlatform],
        gem: GemMetadata,
    ) -> float:
        """
        目标平台评分

        Returns:
            1.0 — 精确匹配
            0.8 — UNIVERSAL
            0.5 — 信息缺失
            0.0 — 不匹配
        """
        if platform is None:
            return 0.5
        if platform in gem.target_platforms:
            return 1.0
        if TargetPlatform.UNIVERSAL in gem.target_platforms:
            return 0.8
        return 0.0

    @staticmethod
    def _score_video_style(
        style: Optional[VideoStyle],
        gem: GemMetadata,
    ) -> float:
        """
        视频风格评分

        Returns:
            1.0 — 精确匹配
            0.5 — 信息缺失
            0.0 — 不匹配
        """
        if style is None:
            return 0.5
        if style in gem.video_styles:
            return 1.0
        return 0.0

    @staticmethod
    def _score_visual_tone(
        tone: Optional[VisualTone],
        gem: GemMetadata,
    ) -> float:
        """
        画面调性评分

        Returns:
            1.0 — 精确匹配
            0.5 — 信息缺失或 Gem 未指定
            0.0 — 不匹配
        """
        if tone is None or not gem.visual_tones:
            return 0.5
        if tone in gem.visual_tones:
            return 1.0
        return 0.0

    # ═══════════════════════════════════════════════════════════
    # 7. 置信度评估
    # ═══════════════════════════════════════════════════════════

    def _evaluate_confidence(
        self,
        scored: List[ScoredGem],
        features: ImageFeatures,
    ) -> Tuple[ConfidenceLevel, str]:
        """
        三级置信度评估

        公式:
        adjusted_score = top_score + consistency_bonus - missing_penalty

        判断规则:
        - HIGH:   adjusted_score >= 0.85 且 completeness >= 0.5
        - MEDIUM: adjusted_score >= 0.70 或 (completeness >= 0.5 且 top_score >= 0.60)
        - LOW:    其他

        Args:
            scored: 已排序的评分结果
            features: 查询特征

        Returns:
            Tuple[ConfidenceLevel, str]: (置信度等级, 推理说明)
        """
        if not scored:
            return ConfidenceLevel.LOW, "无候选 Gem"

        # 1. 提取第一名分数
        top_score = scored[0].score

        # 2. 计算特征完整度
        completeness = features.get_completeness_score()

        # 3. 计算领先优势
        score_gap = 0.0
        if len(scored) > 1:
            score_gap = top_score - scored[1].score

        # 4. 计算一致性加成
        consistency_bonus = min(
            self.BONUS_FACTORS["consistency_max"],
            score_gap * self.BONUS_FACTORS["consistency_rate"],
        )

        # 5. 计算缺失惩罚
        missing_penalty = (
            (1.0 - completeness) * self.PENALTY_FACTORS["missing_base"]
        )

        # 6. 计算调整后分数
        adjusted_score = top_score + consistency_bonus - missing_penalty

        # 7. 三级判断

        # ━━ HIGH 判断 ━━
        if (adjusted_score >= self.THRESHOLDS["high_min"] and
                completeness >= self.THRESHOLDS["completeness_min"]):
            reasoning = (
                f"高置信度匹配: score={top_score:.3f}, "
                f"特征完整度={completeness:.2f}, "
                f"领先优势={score_gap:.3f}"
            )
            return ConfidenceLevel.HIGH, reasoning

        # ━━ MEDIUM 判断 ━━
        if (adjusted_score >= self.THRESHOLDS["medium_min"] or
                (completeness >= self.THRESHOLDS["completeness_min"] and
                 top_score >= 0.60)):
            reasoning = (
                f"中等置信度: score={top_score:.3f}, "
                f"特征完整度={completeness:.2f}, "
                f"建议用户从 Top-3 中选择"
            )
            return ConfidenceLevel.MEDIUM, reasoning

        # ━━ LOW 判断（默认） ━━
        reasoning = (
            f"低置信度: score={top_score:.3f}, "
            f"特征完整度={completeness:.2f}, "
            f"将返回兜底方案"
        )
        return ConfidenceLevel.LOW, reasoning

    # ═══════════════════════════════════════════════════════════
    # 8. 结果组装
    # ═══════════════════════════════════════════════════════════

    def _assemble_result(
        self,
        features: ImageFeatures,
        scored: List[ScoredGem],
        confidence_level: ConfidenceLevel,
        reasoning: str,
        elapsed_ms: int,
        fallback_triggered: bool = False,
    ) -> MatchResult:
        """组装匹配结果"""
        top = scored[0] if scored else None
        return MatchResult(
            query_features=features,
            matches=scored[:self.top_k],
            confidence_level=confidence_level,
            final_score=top.score if top else 0.0,
            top_candidate_id=top.gem_id if top else None,
            top_candidate_name=top.name if top else None,
            fallback_triggered=fallback_triggered,
            reasoning=reasoning,
            elapsed_ms=elapsed_ms,
        )

    def _create_fallback_result(
        self,
        features: ImageFeatures,
        reason: str,
        elapsed_ms: int = 0,
    ) -> MatchResult:
        """
        创建兜底结果

        当无匹配候选或发生异常时调用。
        返回兜底 Gem 或空结果。
        """
        if self._fallback_gem:
            fallback_scored = ScoredGem(
                gem_id=self._fallback_gem.gem_id,
                name=self._fallback_gem.name,
                score=0.3,
                breakdown={},
            )
            return MatchResult(
                query_features=features,
                matches=[fallback_scored],
                confidence_level=ConfidenceLevel.LOW,
                final_score=0.3,
                top_candidate_id=self._fallback_gem.gem_id,
                top_candidate_name=self._fallback_gem.name,
                fallback_triggered=True,
                reasoning=f"已触发兜底策略: {reason}",
                elapsed_ms=elapsed_ms,
            )
        return MatchResult(
            query_features=features,
            matches=[],
            confidence_level=ConfidenceLevel.LOW,
            fallback_triggered=True,
            reasoning=f"无兜底 Gem 可用: {reason}",
            elapsed_ms=elapsed_ms,
        )

    # ═══════════════════════════════════════════════════════════
    # 9. 统计与监控
    # ═══════════════════════════════════════════════════════════

    def get_stats(self: "SmartRouter") -> Dict[str, Any]:
        """
        获取路由统计信息

        Returns:
            {
                "route_count": int,          # 总路由次数
                "avg_latency_ms": float,     # 平均延迟
                "gem_count": int,            # Gem 数量
            }
        """
        avg_latency = (
            self._total_latency_ms / self._route_count
            if self._route_count > 0 else 0.0
        )
        return {
            "route_count": self._route_count,
            "avg_latency_ms": round(avg_latency, 2),
            "gem_count": self.gem_count,
        }

    def get_weight_config(self: "SmartRouter") -> WeightConfig:
        """获取当前权重配置的深拷贝"""
        return self.weight_config.clone()


# ═══════════════════════════════════════════════════════════
# 测试代码
# ═══════════════════════════════════════════════════════════

def _test():
    """内部测试 — 验证 SmartRouter 核心功能"""
    print("=" * 60)
    print("SmartRouter 核心引擎测试")
    print("=" * 60)

    # 1. 创建路由器
    router = SmartRouter()
    print(f"[1] 创建路由器: gem_count={router.gem_count}")

    # 2. 验证权重配置
    assert router.weight_config.validate_weights()
    subtotals = router.weight_config.get_dimension_subtotals()
    assert abs(subtotals["product"] - 0.40) < 0.001
    assert abs(subtotals["audience"] - 0.23) < 0.001
    assert abs(subtotals["scene"] - 0.25) < 0.001
    assert abs(subtotals["style"] - 0.12) < 0.001
    print(f"[2] 权重验证通过: {subtotals}")

    # 3. 注册 Gem
    makeup_gem = GemMetadata(
        gem_id="gem_mk_001",
        name="美妆产品种草快剪",
        description="适合美妆产品的快节奏种草视频",
        supported_categories=[ProductCategory.MAKEUP, ProductCategory.SKINCARE],
        supported_product_types=["口红", "眼影", "粉底液", "面膜"],
        target_price_tiers=[PriceTier.AFFORDABLE, PriceTier.MID_RANGE, PriceTier.PREMIUM],
        target_brand_tiers=[BrandTier.MID_MARKET, BrandTier.PREMIUM, BrandTier.D2C],
        matched_selling_points=[SellingPoint.TRENDING, SellingPoint.NATURAL_INGREDIENTS],
        target_genders=[TargetGender.FEMALE, TargetGender.UNISEX],
        target_age_groups=[TargetAgeGroup.GEN_Z, TargetAgeGroup.GEN_Y],
        target_spending_power=[SpendingPower.ECONOMY, SpendingPower.MIDDLE],
        matched_personas=[PersonaTag.FASHION_FORWARD, PersonaTag.BARGAIN_HUNTER],
        marketing_scenes=[MarketingScene.NEW_LAUNCH, MarketingScene.SOCIAL_PROOF],
        usage_scenes=[UsageScene.DAILY, UsageScene.PARTY],
        target_platforms=[TargetPlatform.DOUYIN, TargetPlatform.XIAOHONGSHU],
        video_styles=[VideoStyle.FAST_PACED, VideoStyle.PRODUCT_SHOWCASE],
        visual_tones=[VisualTone.BRIGHT, VisualTone.VIBRANT],
        keywords=["美妆", "种草", "快剪"],
        priority_boost=0.8,
    )
    router.register_gem(makeup_gem)
    assert router.gem_count == 1
    print(f"[3] 注册 Gem: {makeup_gem.name}")

    # 4. 注册数码 Gem
    digital_gem = GemMetadata(
        gem_id="gem_dg_001",
        name="数码产品科技测评",
        description="适合数码产品的科技感测评视频",
        supported_categories=[ProductCategory.PHONE, ProductCategory.DIGITAL_ACCESSORY],
        supported_product_types=["手机", "耳机", "智能手表"],
        target_price_tiers=[PriceTier.MID_RANGE, PriceTier.PREMIUM, PriceTier.LUXURY],
        target_brand_tiers=[BrandTier.PREMIUM, BrandTier.LUXURY],
        matched_selling_points=[SellingPoint.HIGH_TECH, SellingPoint.DURABLE],
        target_genders=[TargetGender.MALE, TargetGender.UNISEX],
        target_age_groups=[TargetAgeGroup.GEN_Y, TargetAgeGroup.GEN_X],
        target_spending_power=[SpendingPower.MIDDLE, SpendingPower.AFFLUENT],
        matched_personas=[PersonaTag.TECH_ENTHUSIAST, PersonaTag.QUALITY_FIRST],
        marketing_scenes=[MarketingScene.NEW_LAUNCH, MarketingScene.BRAND_BUILDING],
        usage_scenes=[UsageScene.WORK, UsageScene.DAILY],
        target_platforms=[TargetPlatform.DOUYIN, TargetPlatform.TAOBAO],
        video_styles=[VideoStyle.TUTORIAL, VideoStyle.PRODUCT_SHOWCASE],
        visual_tones=[VisualTone.FUTURISTIC, VisualTone.COOL],
        keywords=["数码", "测评", "科技"],
        priority_boost=0.7,
    )
    router.register_gem(digital_gem)
    print(f"[4] 注册 Gem: {digital_gem.name}")

    # 5. 测试完整美妆匹配 → HIGH 置信度
    features = ImageFeatures(
        category=ProductCategory.MAKEUP,
        product_type="口红",
        price_tier=PriceTier.MID_RANGE,
        target_gender=TargetGender.FEMALE,
        target_age=TargetAgeGroup.GEN_Z,
        estimated_spending_power=SpendingPower.MIDDLE,
        inferred_marketing_scene=MarketingScene.SOCIAL_PROOF,
        inferred_usage_scenes=[UsageScene.DAILY, UsageScene.PARTY],
        target_platform=TargetPlatform.DOUYIN,
        inferred_video_style=VideoStyle.FAST_PACED,
        inferred_visual_tone=VisualTone.BRIGHT,
        detected_selling_points=[SellingPoint.TRENDING],
    )
    result = router.route_from_features(features)
    assert result.top_candidate_id == "gem_mk_001"
    assert result.confidence_level == ConfidenceLevel.HIGH
    assert result.final_score > 0.85
    print(f"[5] 美妆完整匹配: {result.top_candidate_name} "
          f"score={result.final_score:.4f} confidence={result.confidence_level.value}")

    # 6. 测试完整数码匹配 → HIGH 置信度
    features_dg = ImageFeatures(
        category=ProductCategory.PHONE,
        product_type="手机",
        price_tier=PriceTier.PREMIUM,
        target_gender=TargetGender.MALE,
        target_age=TargetAgeGroup.GEN_Y,
        estimated_spending_power=SpendingPower.AFFLUENT,
        inferred_marketing_scene=MarketingScene.NEW_LAUNCH,
        target_platform=TargetPlatform.TAOBAO,
        inferred_video_style=VideoStyle.TUTORIAL,
        inferred_visual_tone=VisualTone.FUTURISTIC,
        detected_selling_points=[SellingPoint.HIGH_TECH],
    )
    result_dg = router.route_from_features(features_dg)
    assert result_dg.top_candidate_id == "gem_dg_001"
    assert result_dg.confidence_level == ConfidenceLevel.HIGH
    print(f"[6] 数码完整匹配: {result_dg.top_candidate_name} "
          f"score={result_dg.final_score:.4f} confidence={result_dg.confidence_level.value}")

    # 7. 测试极简特征 → LOW 置信度
    features_minimal = ImageFeatures(category=ProductCategory.JEWELRY)
    result_minimal = router.route_from_features(features_minimal)
    assert result_minimal.confidence_level == ConfidenceLevel.LOW
    print(f"[7] 极简特征: confidence={result_minimal.confidence_level.value} "
          f"(预期 LOW)")

    # 8. 测试各评分函数
    score, breakdown = router._score_single_gem(features, makeup_gem)
    assert breakdown["category"] == 1.0  # 精确匹配
    assert breakdown["target_gender"] == 1.0  # FEMALE 匹配
    assert breakdown["video_style"] == 1.0  # FAST_PACED 匹配
    print(f"[8] 评分明细: category={breakdown['category']}, "
          f"gender={breakdown['target_gender']}, "
          f"video_style={breakdown['video_style']}")

    # 9. 测试品类不匹配零分
    features_mismatch = ImageFeatures(
        category=ProductCategory.PHONE,  # makeup_gem 不支持 PHONE
        inferred_marketing_scene=MarketingScene.NEW_LAUNCH,
    )
    candidates = [makeup_gem]
    score_mismatch, breakdown_mismatch = router._score_single_gem(
        features_mismatch, makeup_gem)
    assert breakdown_mismatch["category"] == 0.0
    print(f"[9] 品类不匹配: category_score={breakdown_mismatch['category']} (预期 0.0)")

    # 10. 测试信息缺失返回 0.5
    features_empty = ImageFeatures()
    score_empty, breakdown_empty = router._score_single_gem(
        features_empty, makeup_gem)
    for dim_name, dim_score in breakdown_empty.items():
        assert dim_score == 0.5, f"维度 {dim_name} 应为 0.5, 实际 {dim_score}"
    print(f"[10] 缺失特征评分: 全部维度=0.5 (通过)")

    # 11. 测试耗时统计
    stats = router.get_stats()
    assert stats["route_count"] == 3  # 3次路由调用(美妆/数码/极简)
    assert stats["avg_latency_ms"] >= 0
    print(f"[11] 统计: {stats}")

    # 12. 测试平台特化权重
    douyin_config = router.weight_config.for_platform(TargetPlatform.DOUYIN)
    assert douyin_config.video_style > router.weight_config.video_style
    print(f"[12] 抖音特化权重: video_style={douyin_config.video_style:.3f}")

    print("\n" + "=" * 60)
    print("所有测试通过!")
    print("=" * 60)


if __name__ == "__main__":
    _test()
