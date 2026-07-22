# -*- coding: utf-8 -*-
"""
model_selector.py
智能选型器

职责:
- 根据用户需求选择最佳视频生成模型
- 实现规则引擎 (15条规则) + 多因素评分算法 + 降级链
- 支持缓存、日志、可观测性

架构:
    RuleEngine (规则引擎) + Scorer (评分模型) + FallbackChain (降级链)
    -> DecisionAggregator (决策聚合器) -> RoutingDecision

设计原则:
- 规则优先: 强制规则优先于评分
- 策略驱动: 评分权重根据 RoutingStrategy 动态调整
- 可扩展: 新增规则只需注册到规则表
- 可观测: 每个决策都有完整的 reason 说明
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

from video_generator import (
    ModelRequirements,
    ModelScore,
    ProviderRule,
    RuleEngineResult,
    RuleLevel,
    RoutingDecision,
    RoutingStrategy,
    ScoreDimension,
    ScoringWeights,
    SelectorConfig,
    VideoAspectRatio,
    VideoGenerationRequest,
    VideoModelVersion,
    VideoProvider,
    VideoResolution,
    NoSuitableProviderError,
    BudgetExceededError,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# 提供商评分基准表
# ═══════════════════════════════════════════════════════════

PROVIDER_BASELINE_SCORES: Dict[
    Tuple[VideoProvider, VideoModelVersion], Dict[ScoreDimension, float]
] = {
    # 即梦 Seedance
    (VideoProvider.SEEDANCE, VideoModelVersion.SEEDANCE_2_0_STANDARD): {
        ScoreDimension.SPEED: 5, ScoreDimension.QUALITY: 9,
        ScoreDimension.COST: 5, ScoreDimension.CHARACTER: 9,
        ScoreDimension.CHINESE: 10, ScoreDimension.CAPABILITY: 9,
    },
    (VideoProvider.SEEDANCE, VideoModelVersion.SEEDANCE_2_0_MINI): {
        ScoreDimension.SPEED: 9, ScoreDimension.QUALITY: 5,
        ScoreDimension.COST: 8, ScoreDimension.CHARACTER: 6,
        ScoreDimension.CHINESE: 8, ScoreDimension.CAPABILITY: 6,
    },
    (VideoProvider.SEEDANCE, VideoModelVersion.SEEDANCE_2_0_FAST): {
        ScoreDimension.SPEED: 9, ScoreDimension.QUALITY: 7,
        ScoreDimension.COST: 6, ScoreDimension.CHARACTER: 7,
        ScoreDimension.CHINESE: 9, ScoreDimension.CAPABILITY: 8,
    },
    # 可灵 Kling
    (VideoProvider.KLING, VideoModelVersion.KLING_3_0): {
        ScoreDimension.SPEED: 6, ScoreDimension.QUALITY: 9,
        ScoreDimension.COST: 6, ScoreDimension.CHARACTER: 10,
        ScoreDimension.CHINESE: 8, ScoreDimension.CAPABILITY: 10,
    },
    (VideoProvider.KLING, VideoModelVersion.KLING_3_0_TURBO): {
        ScoreDimension.SPEED: 8, ScoreDimension.QUALITY: 7,
        ScoreDimension.COST: 7, ScoreDimension.CHARACTER: 7,
        ScoreDimension.CHINESE: 7, ScoreDimension.CAPABILITY: 8,
    },
    (VideoProvider.KLING, VideoModelVersion.KLING_3_0_OMNI): {
        ScoreDimension.SPEED: 5, ScoreDimension.QUALITY: 8,
        ScoreDimension.COST: 5, ScoreDimension.CHARACTER: 9,
        ScoreDimension.CHINESE: 8, ScoreDimension.CAPABILITY: 9,
    },
    # 生数 Vidu
    (VideoProvider.VIDU, VideoModelVersion.VIDU_Q3_PRO): {
        ScoreDimension.SPEED: 7, ScoreDimension.QUALITY: 8,
        ScoreDimension.COST: 6, ScoreDimension.CHARACTER: 7,
        ScoreDimension.CHINESE: 7, ScoreDimension.CAPABILITY: 8,
    },
    (VideoProvider.VIDU, VideoModelVersion.VIDU_Q3_TURBO): {
        ScoreDimension.SPEED: 8, ScoreDimension.QUALITY: 7,
        ScoreDimension.COST: 10, ScoreDimension.CHARACTER: 6,
        ScoreDimension.CHINESE: 6, ScoreDimension.CAPABILITY: 7,
    },
    # Google Veo 3
    (VideoProvider.VEO3, VideoModelVersion.VEO_3_1_STANDARD): {
        ScoreDimension.SPEED: 4, ScoreDimension.QUALITY: 10,
        ScoreDimension.COST: 2, ScoreDimension.CHARACTER: 8,
        ScoreDimension.CHINESE: 4, ScoreDimension.CAPABILITY: 8,
    },
    (VideoProvider.VEO3, VideoModelVersion.VEO_3_1_FAST): {
        ScoreDimension.SPEED: 7, ScoreDimension.QUALITY: 8,
        ScoreDimension.COST: 5, ScoreDimension.CHARACTER: 7,
        ScoreDimension.CHINESE: 4, ScoreDimension.CAPABILITY: 8,
    },
    (VideoProvider.VEO3, VideoModelVersion.VEO_3_1_LITE): {
        ScoreDimension.SPEED: 9, ScoreDimension.QUALITY: 6,
        ScoreDimension.COST: 7, ScoreDimension.CHARACTER: 5,
        ScoreDimension.CHINESE: 4, ScoreDimension.CAPABILITY: 6,
    },
    # Runway
    (VideoProvider.RUNWAY, VideoModelVersion.RUNWAY_GEN_4): {
        ScoreDimension.SPEED: 4, ScoreDimension.QUALITY: 10,
        ScoreDimension.COST: 5, ScoreDimension.CHARACTER: 8,
        ScoreDimension.CHINESE: 3, ScoreDimension.CAPABILITY: 8,
    },
    (VideoProvider.RUNWAY, VideoModelVersion.RUNWAY_GEN_4_TURBO): {
        ScoreDimension.SPEED: 8, ScoreDimension.QUALITY: 7,
        ScoreDimension.COST: 8, ScoreDimension.CHARACTER: 6,
        ScoreDimension.CHINESE: 3, ScoreDimension.CAPABILITY: 7,
    },
    (VideoProvider.RUNWAY, VideoModelVersion.RUNWAY_GEN_4_5): {
        ScoreDimension.SPEED: 5, ScoreDimension.QUALITY: 9,
        ScoreDimension.COST: 4, ScoreDimension.CHARACTER: 7,
        ScoreDimension.CHINESE: 3, ScoreDimension.CAPABILITY: 7,
    },
    # Pika
    (VideoProvider.PIKA, VideoModelVersion.PIKA_2_2): {
        ScoreDimension.SPEED: 9, ScoreDimension.QUALITY: 6,
        ScoreDimension.COST: 7, ScoreDimension.CHARACTER: 5,
        ScoreDimension.CHINESE: 3, ScoreDimension.CAPABILITY: 6,
    },
}


# ═══════════════════════════════════════════════════════════
# 价格表 (USD/秒)
# ═══════════════════════════════════════════════════════════

PRICING_TABLE: Dict[Tuple[VideoProvider, VideoModelVersion], float] = {
    (VideoProvider.VIDU, VideoModelVersion.VIDU_Q3_TURBO): 0.055,
    (VideoProvider.RUNWAY, VideoModelVersion.RUNWAY_GEN_4_TURBO): 0.08,
    (VideoProvider.SEEDANCE, VideoModelVersion.SEEDANCE_2_0_MINI): 0.09,
    (VideoProvider.VEO3, VideoModelVersion.VEO_3_1_LITE): 0.10,
    (VideoProvider.VIDU, VideoModelVersion.VIDU_Q3_PRO): 0.12,
    (VideoProvider.KLING, VideoModelVersion.KLING_3_0_TURBO): 0.13,
    (VideoProvider.PIKA, VideoModelVersion.PIKA_2_2): 0.15,
    (VideoProvider.SEEDANCE, VideoModelVersion.SEEDANCE_2_0_FAST): 0.15,
    (VideoProvider.SEEDANCE, VideoModelVersion.SEEDANCE_2_0_STANDARD): 0.18,
    (VideoProvider.VEO3, VideoModelVersion.VEO_3_1_FAST): 0.18,
    (VideoProvider.KLING, VideoModelVersion.KLING_3_0): 0.20,
    (VideoProvider.KLING, VideoModelVersion.KLING_3_0_OMNI): 0.22,
    (VideoProvider.VEO3, VideoModelVersion.VEO_3_1_STANDARD): 0.25,
    (VideoProvider.RUNWAY, VideoModelVersion.RUNWAY_GEN_4_5): 0.28,
    (VideoProvider.RUNWAY, VideoModelVersion.RUNWAY_GEN_4): 0.30,
}


# ═══════════════════════════════════════════════════════════
# 预估耗时表 (秒)
# ═══════════════════════════════════════════════════════════

GENERATION_TIME_TABLE: Dict[Tuple[VideoProvider, VideoModelVersion], float] = {
    (VideoProvider.SEEDANCE, VideoModelVersion.SEEDANCE_2_0_FAST): 30.0,
    (VideoProvider.SEEDANCE, VideoModelVersion.SEEDANCE_2_0_MINI): 35.0,
    (VideoProvider.SEEDANCE, VideoModelVersion.SEEDANCE_2_0_STANDARD): 80.0,
    (VideoProvider.KLING, VideoModelVersion.KLING_3_0_TURBO): 50.0,
    (VideoProvider.KLING, VideoModelVersion.KLING_3_0): 90.0,
    (VideoProvider.KLING, VideoModelVersion.KLING_3_0_OMNI): 100.0,
    (VideoProvider.VIDU, VideoModelVersion.VIDU_Q3_TURBO): 35.0,
    (VideoProvider.VIDU, VideoModelVersion.VIDU_Q3_PRO): 70.0,
    (VideoProvider.VEO3, VideoModelVersion.VEO_3_1_LITE): 40.0,
    (VideoProvider.VEO3, VideoModelVersion.VEO_3_1_FAST): 45.0,
    (VideoProvider.VEO3, VideoModelVersion.VEO_3_1_STANDARD): 120.0,
    (VideoProvider.RUNWAY, VideoModelVersion.RUNWAY_GEN_4_TURBO): 45.0,
    (VideoProvider.RUNWAY, VideoModelVersion.RUNWAY_GEN_4): 110.0,
    (VideoProvider.RUNWAY, VideoModelVersion.RUNWAY_GEN_4_5): 100.0,
    (VideoProvider.PIKA, VideoModelVersion.PIKA_2_2): 40.0,
}


# ═══════════════════════════════════════════════════════════
# 策略权重配置
# ═══════════════════════════════════════════════════════════

DEFAULT_STRATEGY_WEIGHTS: Dict[RoutingStrategy, ScoringWeights] = {
    RoutingStrategy.SPEED_FIRST: ScoringWeights(
        strategy=RoutingStrategy.SPEED_FIRST,
        speed_weight=0.50, quality_weight=0.15,
        cost_weight=0.20, character_weight=0.0,
        chinese_weight=0.0, capability_weight=0.15,
    ),
    RoutingStrategy.QUALITY_FIRST: ScoringWeights(
        strategy=RoutingStrategy.QUALITY_FIRST,
        speed_weight=0.10, quality_weight=0.50,
        cost_weight=0.10, character_weight=0.0,
        chinese_weight=0.0, capability_weight=0.30,
    ),
    RoutingStrategy.COST_FIRST: ScoringWeights(
        strategy=RoutingStrategy.COST_FIRST,
        speed_weight=0.20, quality_weight=0.10,
        cost_weight=0.55, character_weight=0.0,
        chinese_weight=0.0, capability_weight=0.15,
    ),
    RoutingStrategy.CHARACTER_FIRST: ScoringWeights(
        strategy=RoutingStrategy.CHARACTER_FIRST,
        speed_weight=0.15, quality_weight=0.25,
        cost_weight=0.15, character_weight=0.45,
        chinese_weight=0.0, capability_weight=0.0,
    ),
    RoutingStrategy.BALANCED: ScoringWeights(
        strategy=RoutingStrategy.BALANCED,
        speed_weight=0.25, quality_weight=0.30,
        cost_weight=0.25, character_weight=0.0,
        chinese_weight=0.0, capability_weight=0.20,
    ),
    RoutingStrategy.MANUAL: ScoringWeights(
        strategy=RoutingStrategy.MANUAL,
        speed_weight=0.0, quality_weight=0.0,
        cost_weight=0.0, character_weight=0.0,
        chinese_weight=0.0, capability_weight=1.0,
    ),
}


# ═══════════════════════════════════════════════════════════
# ModelSelector 主类
# ═══════════════════════════════════════════════════════════


class ModelSelector:
    """
    智能选型器
    ==========

    根据用户需求的多维特征，通过规则引擎 + 多因素评分算法，
    选择最优的视频生成模型，并生成降级链。

    使用示例::

        selector = ModelSelector.create_default()
        req = ModelRequirements.from_video_request(video_request)
        decision = selector.select_model(req)
        print(f"选择: {decision.primary_provider.value}")
        print(f"降级链: {[p.value for p in decision.fallback_chain]}")
    """

    DEFAULT_RULE_BONUS_MAX = 15.0
    DEFAULT_RULE_PENALTY_MAX = 10.0
    SCORE_SCALE_FACTOR = 10.0

    def __init__(
        self,
        config: Optional[SelectorConfig] = None,
        provider_scores: Optional[Dict] = None,
        pricing_table: Optional[Dict] = None,
        time_table: Optional[Dict] = None,
        weights_map: Optional[Dict] = None,
    ) -> None:
        """
        初始化选型器

        Args:
            config: 选型器配置
            provider_scores: 自定义评分基准表
            pricing_table: 自定义价格表
            time_table: 自定义耗时表
            weights_map: 自定义策略权重
        """
        self.config = config or SelectorConfig()
        self.provider_scores = provider_scores or PROVIDER_BASELINE_SCORES
        self.pricing_table = pricing_table or PRICING_TABLE
        self.time_table = time_table or GENERATION_TIME_TABLE
        self.weights_map = weights_map or DEFAULT_STRATEGY_WEIGHTS

        # 日志 (必须在规则注册之前初始化)
        self._logger = logging.getLogger(self.__class__.__name__)
        if self.config.log_decision_process:
            self._logger.setLevel(getattr(logging, self.config.log_level, logging.INFO))

        # 初始化规则引擎
        self.rules: List[ProviderRule] = []
        self._register_default_rules()

        # 缓存
        self._score_cache: Dict[str, Tuple[float, List[ModelScore]]] = {}
        self._decision_log: List[Dict[str, Any]] = []

    # ═══════════════════════════════════════════════════════════
    # 公共方法
    # ═══════════════════════════════════════════════════════════

    def select_model(self: "ModelSelector", requirements: ModelRequirements) -> RoutingDecision:
        """
        根据需求选择最佳视频生成模型 (主入口)

        完整决策流程:
        1. 检查缓存
        2. 检查手动指定
        3. 应用强制规则
        4. 应用建议规则 + 评分
        5. 生成降级链
        6. 输出决策

        Args:
            requirements: 模型选型需求

        Returns:
            RoutingDecision: 路由决策结果

        Raises:
            NoSuitableProviderError: 无满足条件的提供商
            BudgetExceededError: 所有候选超出预算
        """
        self._logger.info("=" * 60)
        self._logger.info(f"开始模型选型 [strategy={requirements.strategy.value}]")

        # Step 1: 检查缓存
        cached = self._check_cache(requirements)
        if cached is not None:
            self._logger.info("命中评分缓存, 直接使用")
            best = cached[0] if cached else None
            if best and not best.disqualified:
                return self._build_decision(best, requirements, cached)

        # Step 2: 检查手动指定
        manual = self._check_manual_override(requirements)
        if manual:
            self._logger.info(f"手动指定: {manual.primary_provider.value}")
            return manual

        # Step 3: 构建初始候选列表
        all_candidates = list(self.provider_scores.keys())
        self._logger.info(f"初始候选数: {len(all_candidates)}")

        # Step 4: 应用规则引擎
        rule_result = self._apply_rules(requirements, all_candidates)
        candidates = rule_result.mandatory_filtered or all_candidates
        self._logger.info(
            f"强制规则过滤后候选数: {len(candidates)}, "
            f"应用规则: {rule_result.mandatory_rules_applied}"
        )

        if not candidates:
            raise NoSuitableProviderError(
                "没有满足强制规则的提供商",
                rules_applied=rule_result.mandatory_rules_applied,
            )

        # Step 5: 多因素评分
        scored = self._score_models(requirements, candidates, rule_result)
        self._logger.info(f"评分完成, 有效候选: {len([s for s in scored if not s.disqualified])}")

        # Step 6: 选择最佳候选
        valid_scores = [s for s in scored if not s.disqualified]
        if not valid_scores:
            reasons = [f"{s.provider.value}: {s.disqualify_reason}" for s in scored]
            raise BudgetExceededError(f"所有候选均被排除: {'; '.join(reasons[:3])}")

        best = valid_scores[0]
        self._logger.info(
            f"最佳选择: {best.provider.value}/{best.model_version.value} "
            f"得分={best.total_score:.1f}"
        )

        # Step 7: 生成降级链
        fallback = self._build_fallback_chain(
            (best.provider, best.model_version), requirements, scored
        )

        # Step 8: 构建决策
        decision = RoutingDecision(
            primary_provider=best.provider,
            primary_model=best.model_version,
            fallback_chain=fallback,
            reason=rule_result.get_reasoning(),
            estimated_cost_usd=best.estimated_cost_usd,
            estimated_time_sec=best.estimated_time_sec,
        )

        # Step 9: 记录日志
        self._log_decision(decision, scored, rule_result)

        # Step 10: 更新缓存
        if self.config.cache_scores:
            self._update_cache(requirements, scored)

        self._logger.info(f"选型完成: {decision.primary_provider.value}")
        return decision

    def select_model_with_explanation(
        self, requirements: ModelRequirements
    ) -> Tuple[RoutingDecision, str]:
        """选型并附带详细解释"""
        decision = self.select_model(requirements)
        explanation = self._generate_explanation(decision, requirements)
        return decision, explanation

    def refresh_provider_scores(
        self,
        new_scores: Dict[Tuple[VideoProvider, VideoModelVersion], Dict[ScoreDimension, float]],
    ) -> None:
        """刷新提供商评分基准表"""
        self.provider_scores = new_scores
        self.clear_cache()
        self._logger.info("提供商评分基准表已刷新, 缓存已清除")

    def get_decision_log(self: "ModelSelector") -> List[Dict[str, Any]]:
        """获取决策日志"""
        return self._decision_log.copy()

    def clear_cache(self: "ModelSelector") -> None:
        """清除评分缓存"""
        self._score_cache.clear()
        self._logger.debug("评分缓存已清除")

    def get_all_scores(self: "ModelSelector", requirements: ModelRequirements) -> List[ModelScore]:
        """获取所有候选的评分 (用于调试/分析)"""
        all_candidates = list(self.provider_scores.keys())
        rule_result = self._apply_rules(requirements, all_candidates)
        candidates = rule_result.mandatory_filtered or all_candidates
        return self._score_models(requirements, candidates, rule_result)

    # ═══════════════════════════════════════════════════════════
    # 类方法
    # ═══════════════════════════════════════════════════════════

    @classmethod
    def create_default(cls: type["ModelSelector"]) -> ModelSelector:
        """创建默认配置的选型器"""
        return cls(config=SelectorConfig())

    @classmethod
    def from_config(cls: type["ModelSelector"], config_dict: Dict[str, Any]) -> ModelSelector:
        """从字典配置创建选型器"""
        config = SelectorConfig(**config_dict)
        return cls(config=config)

    # ═══════════════════════════════════════════════════════════
    # 内部方法: 规则引擎
    # ═══════════════════════════════════════════════════════════

    def _register_default_rules(self: "ModelSelector") -> None:
        """注册所有默认规则 (15条)"""
        # ─── 强制规则 R01-R04 ───
        self.rules.extend([
            # R01: 4K画质需求
            ProviderRule(
                rule_id="R01",
                name="4K画质需求",
                description="仅支持4K的提供商可用",
                level=RuleLevel.MANDATORY,
                condition_desc="resolution == 4k",
                provider_priority=[
                    (VideoProvider.SEEDANCE, VideoModelVersion.SEEDANCE_2_0_STANDARD),
                    (VideoProvider.KLING, VideoModelVersion.KLING_3_0),
                    (VideoProvider.VEO3, VideoModelVersion.VEO_3_1_STANDARD),
                ],
                tags=["resolution", "4k", "mandatory"],
            ),
            # R02: 超长视频
            ProviderRule(
                rule_id="R02",
                name="超长视频",
                description="视频时长超过10秒时的提供商限制",
                level=RuleLevel.MANDATORY,
                condition_desc="duration > 10",
                provider_priority=[
                    (VideoProvider.VIDU, VideoModelVersion.VIDU_Q3_PRO),
                    (VideoProvider.SEEDANCE, VideoModelVersion.SEEDANCE_2_0_STANDARD),
                    (VideoProvider.KLING, VideoModelVersion.KLING_3_0),
                ],
                tags=["duration", "long", "mandatory"],
            ),
            # R03: 60fps需求
            ProviderRule(
                rule_id="R03",
                name="60fps高帧率",
                description="仅Kling 3.0支持60fps",
                level=RuleLevel.MANDATORY,
                condition_desc="fps > 30",
                provider_priority=[
                    (VideoProvider.KLING, VideoModelVersion.KLING_3_0),
                ],
                tags=["fps", "60fps", "mandatory"],
            ),
            # R04: 多模态参考
            ProviderRule(
                rule_id="R04",
                name="多模态参考输入",
                description="同时需要参考视频+音频输入",
                level=RuleLevel.MANDATORY,
                condition_desc="ref_videos + ref_audios",
                provider_priority=[
                    (VideoProvider.SEEDANCE, VideoModelVersion.SEEDANCE_2_0_STANDARD),
                ],
                tags=["multimodal", "reference", "mandatory"],
            ),
        ])

        # ─── 建议规则 R05-R15 ───
        self.rules.extend([
            # R05: 中文内容
            ProviderRule(
                rule_id="R05",
                name="中文内容优化",
                description="中文提示词优先国产模型",
                level=RuleLevel.RECOMMENDED,
                condition_desc="chinese_ratio > 0.5",
                score_bonus={
                    (VideoProvider.SEEDANCE, VideoModelVersion.SEEDANCE_2_0_STANDARD): 8,
                    (VideoProvider.KLING, VideoModelVersion.KLING_3_0): 6,
                    (VideoProvider.VIDU, VideoModelVersion.VIDU_Q3_PRO): 4,
                    (VideoProvider.VEO3, VideoModelVersion.VEO_3_1_STANDARD): 1,
                },
                tags=["chinese", "language", "recommended"],
            ),
            # R06: 人物一致性
            ProviderRule(
                rule_id="R06",
                name="人物一致性优化",
                description="需要保持人物外观一致",
                level=RuleLevel.RECOMMENDED,
                condition_desc="ref_images>=2 or has_character",
                score_bonus={
                    (VideoProvider.KLING, VideoModelVersion.KLING_3_0): 8,
                    (VideoProvider.SEEDANCE, VideoModelVersion.SEEDANCE_2_0_STANDARD): 6,
                    (VideoProvider.RUNWAY, VideoModelVersion.RUNWAY_GEN_4): 5,
                    (VideoProvider.VEO3, VideoModelVersion.VEO_3_1_STANDARD): 3,
                    (VideoProvider.VIDU, VideoModelVersion.VIDU_Q3_PRO): 3,
                },
                tags=["character", "consistency", "recommended"],
            ),
            # R07: 音频同步
            ProviderRule(
                rule_id="R07",
                name="音频唇形同步",
                description="需要音频生成和唇形同步",
                level=RuleLevel.RECOMMENDED,
                condition_desc="generate_audio + dialogue",
                score_bonus={
                    (VideoProvider.VEO3, VideoModelVersion.VEO_3_1_STANDARD): 8,
                    (VideoProvider.KLING, VideoModelVersion.KLING_3_0): 6,
                    (VideoProvider.SEEDANCE, VideoModelVersion.SEEDANCE_2_0_STANDARD): 4,
                    (VideoProvider.VIDU, VideoModelVersion.VIDU_Q3_PRO): 2,
                    (VideoProvider.RUNWAY, VideoModelVersion.RUNWAY_GEN_4): 2,
                },
                tags=["audio", "lip-sync", "recommended"],
            ),
            # R08: 多镜头叙事
            ProviderRule(
                rule_id="R08",
                name="多镜头叙事",
                description="提示词含镜头切换描述",
                level=RuleLevel.RECOMMENDED,
                condition_desc="has_camera_switching",
                score_bonus={
                    (VideoProvider.KLING, VideoModelVersion.KLING_3_0): 8,
                    (VideoProvider.RUNWAY, VideoModelVersion.RUNWAY_GEN_4): 6,
                },
                tags=["camera", "multi-shot", "recommended"],
            ),
            # R09: 数字人/口播
            ProviderRule(
                rule_id="R09",
                name="数字人口播",
                description="数字人或口播类内容",
                level=RuleLevel.RECOMMENDED,
                condition_desc="has_digital_human",
                score_bonus={
                    (VideoProvider.KLING, VideoModelVersion.KLING_3_0): 8,
                    (VideoProvider.RUNWAY, VideoModelVersion.RUNWAY_GEN_4): 6,
                    (VideoProvider.VIDU, VideoModelVersion.VIDU_Q3_PRO): 3,
                },
                tags=["digital-human", "presenter", "recommended"],
            ),
            # R10: 速度优先 (策略规则)
            ProviderRule(
                rule_id="R10",
                name="速度优先策略",
                description="用户要求快速生成",
                level=RuleLevel.RECOMMENDED,
                condition_desc="strategy == speed_first",
                score_bonus={
                    (VideoProvider.SEEDANCE, VideoModelVersion.SEEDANCE_2_0_FAST): 10,
                    (VideoProvider.SEEDANCE, VideoModelVersion.SEEDANCE_2_0_MINI): 8,
                    (VideoProvider.VIDU, VideoModelVersion.VIDU_Q3_TURBO): 7,
                    (VideoProvider.PIKA, VideoModelVersion.PIKA_2_2): 6,
                    (VideoProvider.VEO3, VideoModelVersion.VEO_3_1_LITE): 5,
                    (VideoProvider.VEO3, VideoModelVersion.VEO_3_1_FAST): 4,
                    (VideoProvider.KLING, VideoModelVersion.KLING_3_0_TURBO): 4,
                    (VideoProvider.RUNWAY, VideoModelVersion.RUNWAY_GEN_4_TURBO): 4,
                },
                tags=["strategy", "speed", "recommended"],
            ),
            # R11: 质量优先 (策略规则)
            ProviderRule(
                rule_id="R11",
                name="质量优先策略",
                description="用户要求最高画质",
                level=RuleLevel.RECOMMENDED,
                condition_desc="strategy == quality_first",
                score_bonus={
                    (VideoProvider.VEO3, VideoModelVersion.VEO_3_1_STANDARD): 10,
                    (VideoProvider.RUNWAY, VideoModelVersion.RUNWAY_GEN_4): 9,
                    (VideoProvider.SEEDANCE, VideoModelVersion.SEEDANCE_2_0_STANDARD): 7,
                    (VideoProvider.KLING, VideoModelVersion.KLING_3_0): 7,
                    (VideoProvider.VIDU, VideoModelVersion.VIDU_Q3_PRO): 5,
                    (VideoProvider.VEO3, VideoModelVersion.VEO_3_1_FAST): 4,
                },
                tags=["strategy", "quality", "recommended"],
            ),
            # R12: 成本优先 (策略规则)
            ProviderRule(
                rule_id="R12",
                name="成本优先策略",
                description="用户要求最低成本",
                level=RuleLevel.RECOMMENDED,
                condition_desc="strategy == cost_first",
                score_bonus={
                    (VideoProvider.VIDU, VideoModelVersion.VIDU_Q3_TURBO): 10,
                    (VideoProvider.RUNWAY, VideoModelVersion.RUNWAY_GEN_4_TURBO): 7,
                    (VideoProvider.SEEDANCE, VideoModelVersion.SEEDANCE_2_0_MINI): 7,
                    (VideoProvider.VEO3, VideoModelVersion.VEO_3_1_LITE): 6,
                    (VideoProvider.PIKA, VideoModelVersion.PIKA_2_2): 5,
                    (VideoProvider.VIDU, VideoModelVersion.VIDU_Q3_PRO): 4,
                    (VideoProvider.KLING, VideoModelVersion.KLING_3_0_TURBO): 4,
                },
                tags=["strategy", "cost", "recommended"],
            ),
            # R13: 错峰模式
            ProviderRule(
                rule_id="R13",
                name="错峰模式",
                description="非紧急任务选择支持错峰优惠的模型",
                level=RuleLevel.RECOMMENDED,
                condition_desc="off_peak == True",
                score_bonus={
                    (VideoProvider.VIDU, VideoModelVersion.VIDU_Q3_TURBO): 5,
                },
                tags=["off-peak", "cost-save", "recommended"],
            ),
            # R14: 平衡模式 (策略规则)
            ProviderRule(
                rule_id="R14",
                name="平衡模式策略",
                description="综合质量/速度/成本的平衡选择",
                level=RuleLevel.RECOMMENDED,
                condition_desc="strategy == balanced",
                score_bonus={
                    (VideoProvider.VEO3, VideoModelVersion.VEO_3_1_FAST): 5,
                    (VideoProvider.KLING, VideoModelVersion.KLING_3_0_TURBO): 4,
                    (VideoProvider.SEEDANCE, VideoModelVersion.SEEDANCE_2_0_FAST): 4,
                    (VideoProvider.VIDU, VideoModelVersion.VIDU_Q3_PRO): 3,
                    (VideoProvider.SEEDANCE, VideoModelVersion.SEEDANCE_2_0_STANDARD): 2,
                    (VideoProvider.KLING, VideoModelVersion.KLING_3_0): 2,
                },
                tags=["strategy", "balanced", "recommended"],
            ),
            # R15: 视频编辑
            ProviderRule(
                rule_id="R15",
                name="视频编辑需求",
                description="提示词含编辑类指令",
                level=RuleLevel.RECOMMENDED,
                condition_desc="has_editing_instruction",
                score_bonus={
                    (VideoProvider.SEEDANCE, VideoModelVersion.SEEDANCE_2_0_STANDARD): 7,
                    (VideoProvider.RUNWAY, VideoModelVersion.RUNWAY_GEN_4): 6,
                    (VideoProvider.PIKA, VideoModelVersion.PIKA_2_2): 4,
                },
                tags=["editing", "instruction", "recommended"],
            ),
        ])

        self._logger.debug(f"已注册 {len(self.rules)} 条规则")

    def _apply_rules(
        self,
        requirements: ModelRequirements,
        candidates: List[Tuple[VideoProvider, VideoModelVersion]],
    ) -> RuleEngineResult:
        """
        应用所有规则

        执行顺序:
        1. 强制规则: 过滤不满足硬性约束的候选
        2. 建议规则: 为匹配的候选加分/扣分
        """
        result = RuleEngineResult()

        # ── 强制规则 ──
        for rule in self.rules:
            if rule.level != RuleLevel.MANDATORY:
                continue
            if not self._check_rule_condition(rule, requirements):
                continue

            result.mandatory_rules_applied.append(rule.rule_id)
            result.reasoning_parts.append(f"{rule.name}: 仅 {len(rule.provider_priority)} 家提供商满足")

            # 取交集
            if not result.mandatory_filtered:
                result.mandatory_filtered = list(rule.provider_priority)
            else:
                priority_set = set(rule.provider_priority)
                result.mandatory_filtered = [
                    c for c in result.mandatory_filtered if c in priority_set
                ]

        # ── 建议规则 ──
        for rule in self.rules:
            if rule.level != RuleLevel.RECOMMENDED:
                continue
            if not self._check_rule_condition(rule, requirements):
                continue

            result.recommended_rules_applied.append(rule.rule_id)

            # 应用加分
            for (provider, model), bonus in rule.score_bonus.items():
                key = (provider, model)
                result.score_adjustments[key] = result.score_adjustments.get(key, 0) + bonus

            # 应用扣分
            for (provider, model), penalty in rule.score_penalty.items():
                key = (provider, model)
                result.score_adjustments[key] = result.score_adjustments.get(key, 0) - penalty

        return result

    def _check_rule_condition(self: "ModelSelector", rule: ProviderRule, req: ModelRequirements) -> bool:
        """检查规则是否触发"""
        # R01: 4K画质
        if rule.rule_id == "R01":
            return req.resolution == VideoResolution.P4K
        # R02: 超长视频
        if rule.rule_id == "R02":
            return req.duration > 10
        # R03: 60fps
        if rule.rule_id == "R03":
            return req.fps is not None and req.fps > 30
        # R04: 多模态参考
        if rule.rule_id == "R04":
            return req.has_reference_videos and req.has_reference_audios
        # R05: 中文内容
        if rule.rule_id == "R05":
            return req.chinese_ratio > 0.5
        # R06: 人物一致性
        if rule.rule_id == "R06":
            return req.reference_image_count >= 2 or req.has_character_description
        # R07: 音频同步
        if rule.rule_id == "R07":
            return req.generate_audio and req.has_dialogue
        # R08: 多镜头叙事
        if rule.rule_id == "R08":
            return req.has_camera_switching
        # R09: 数字人/口播
        if rule.rule_id == "R09":
            return req.has_digital_human
        # R10: 速度优先
        if rule.rule_id == "R10":
            return req.strategy == RoutingStrategy.SPEED_FIRST
        # R11: 质量优先
        if rule.rule_id == "R11":
            return req.strategy == RoutingStrategy.QUALITY_FIRST
        # R12: 成本优先
        if rule.rule_id == "R12":
            return req.strategy == RoutingStrategy.COST_FIRST
        # R13: 错峰模式
        if rule.rule_id == "R13":
            return req.off_peak
        # R14: 平衡模式
        if rule.rule_id == "R14":
            return req.strategy == RoutingStrategy.BALANCED
        # R15: 视频编辑
        if rule.rule_id == "R15":
            return req.has_editing_instruction

        return False

    # ═══════════════════════════════════════════════════════════
    # 内部方法: 评分算法
    # ═══════════════════════════════════════════════════════════

    def _score_models(
        self,
        requirements: ModelRequirements,
        candidates: List[Tuple[VideoProvider, VideoModelVersion]],
        rule_result: RuleEngineResult,
    ) -> List[ModelScore]:
        """
        对候选提供商进行多因素加权评分

        算法:
        1. 获取基准分 (1-10)
        2. 应用策略权重计算加权分
        3. 应用规则调整 (加分/扣分)
        4. 计算预估成本和耗时
        5. 预算/时间约束检查
        6. 排序输出
        """
        weights = self.weights_map.get(requirements.strategy)
        if weights is None:
            weights = self.weights_map[RoutingStrategy.BALANCED]

        scores: List[ModelScore] = []

        for (provider, model) in candidates:
            score = ModelScore(provider=provider, model_version=model)

            # Step 1: 获取基准分
            baseline = self.provider_scores.get((provider, model), {})
            if not baseline:
                score.disqualified = True
                score.disqualify_reason = "无评分基准"
                scores.append(score)
                continue

            score.raw_scores = dict(baseline)

            # Step 2: 计算加权基础分
            weighted_sum = 0.0
            dimension_breakdown: Dict[str, float] = {}

            for dimension, raw_score in baseline.items():
                w = weights.weight_dict.get(dimension, 0)
                weighted = raw_score * w * self.SCORE_SCALE_FACTOR
                weighted_sum += weighted
                dimension_breakdown[dimension.value] = round(weighted, 2)

            score.dimension_breakdown = dimension_breakdown

            # Step 3: 应用规则调整
            adjustment = rule_result.score_adjustments.get((provider, model), 0)
            adjustment = max(-self.DEFAULT_RULE_PENALTY_MAX, min(self.DEFAULT_RULE_BONUS_MAX, adjustment))
            score.rule_bonus = max(adjustment, 0)
            score.rule_penalty = abs(min(adjustment, 0))

            # Step 4: 计算总分
            score.total_score = weighted_sum + adjustment

            # Step 5: 计算预估信息
            price = self.pricing_table.get((provider, model), 0.15)
            score.estimated_cost_usd = price * requirements.duration

            time_estimate = self.time_table.get((provider, model), 120.0)
            score.estimated_time_sec = time_estimate

            # Step 6: 预算检查
            if requirements.max_budget_usd is not None and score.estimated_cost_usd > requirements.max_budget_usd:
                score.disqualified = True
                score.disqualify_reason = "超出预算上限"

            # 时间检查
            if requirements.max_wait_time_sec is not None and score.estimated_time_sec > requirements.max_wait_time_sec:
                score.disqualified = True
                score.disqualify_reason = "超出等待时间上限"

            scores.append(score)

        # Step 7: 排序 (排除项排在最后)
        valid_scores = [s for s in scores if not s.disqualified]
        disqualified_scores = [s for s in scores if s.disqualified]

        valid_scores.sort(key=lambda s: s.total_score, reverse=True)
        disqualified_scores.sort(key=lambda s: s.total_score, reverse=True)

        return valid_scores + disqualified_scores

    # ═══════════════════════════════════════════════════════════
    # 内部方法: 降级链生成
    # ═══════════════════════════════════════════════════════════

    def _build_fallback_chain(
        self,
        primary: Tuple[VideoProvider, VideoModelVersion],
        requirements: ModelRequirements,
        all_scores: List[ModelScore],
    ) -> List[VideoProvider]:
        """
        生成降级链: 当首选失败时按此顺序尝试

        降级原则:
        1. 排除已选中的首选
        2. 排除被强制排除的候选
        3. 按总分排序
        4. 限制降级链长度
        """
        primary_provider, _ = primary

        # 用户手动指定降级链
        if requirements.fallback_providers:
            return [p for p in requirements.fallback_providers if p != primary_provider]

        # 自动构建降级链
        valid_scores = [
            s for s in all_scores
            if s.provider != primary_provider
            and not s.disqualified
            and s.total_score >= self.config.min_acceptable_score
        ]

        # 按总分降序
        valid_scores.sort(key=lambda s: s.total_score, reverse=True)

        # 去重 (同一 provider 只保留一次)
        seen_providers: set[VideoProvider] = set()
        chain: List[VideoProvider] = []
        for score in valid_scores[: self.config.fallback_chain_max_length]:
            if score.provider not in seen_providers:
                chain.append(score.provider)
                seen_providers.add(score.provider)

        return chain

    # ═══════════════════════════════════════════════════════════
    # 内部方法: 手动指定检查
    # ═══════════════════════════════════════════════════════════

    def _check_manual_override(
        self, requirements: ModelRequirements
    ) -> Optional[RoutingDecision]:
        """检查用户是否手动指定了提供商/模型"""
        if requirements.preferred_provider and requirements.preferred_model:
            # 检查是否在我们的评分表中
            key = (requirements.preferred_provider, requirements.preferred_model)
            price = self.pricing_table.get(key, 0.15)
            time_est = self.time_table.get(key, 120.0)

            fallback = [p for p in requirements.fallback_providers if p != requirements.preferred_provider]

            return RoutingDecision(
                primary_provider=requirements.preferred_provider,
                primary_model=requirements.preferred_model,
                fallback_chain=fallback,
                reason="手动指定",
                estimated_cost_usd=price * requirements.duration,
                estimated_time_sec=time_est,
            )
        return None

    # ═══════════════════════════════════════════════════════════
    # 内部方法: 缓存
    # ═══════════════════════════════════════════════════════════

    def _cache_key(self: "ModelSelector", requirements: ModelRequirements) -> str:
        """生成缓存 key"""
        key_data = {
            "strategy": requirements.strategy.value,
            "resolution": requirements.resolution.value,
            "duration": requirements.duration,
            "fps": requirements.fps,
            "generate_audio": requirements.generate_audio,
            "has_first_frame": requirements.has_first_frame,
            "has_last_frame": requirements.has_last_frame,
            "reference_images": requirements.reference_image_count,
            "has_ref_videos": requirements.has_reference_videos,
            "has_ref_audios": requirements.has_reference_audios,
            "chinese_ratio": round(requirements.chinese_ratio, 2),
            "has_character": requirements.has_character_description,
            "has_camera": requirements.has_camera_switching,
            "has_digital": requirements.has_digital_human,
            "has_dialogue": requirements.has_dialogue,
            "has_editing": requirements.has_editing_instruction,
            "off_peak": requirements.off_peak,
            "preferred": requirements.preferred_provider.value if requirements.preferred_provider else None,
        }
        key_str = json.dumps(key_data, sort_keys=True)
        return hashlib.md5(key_str.encode()).hexdigest()

    def _check_cache(self: "ModelSelector", requirements: ModelRequirements) -> Optional[List[ModelScore]]:
        """检查评分缓存"""
        if not self.config.cache_scores:
            return None
        key = self._cache_key(requirements)
        if key in self._score_cache:
            cached_at, scores = self._score_cache[key]
            if time.time() - cached_at < self.config.score_cache_ttl_sec:
                return scores
            else:
                del self._score_cache[key]
        return None

    def _update_cache(self: "ModelSelector", requirements: ModelRequirements, scores: List[ModelScore]) -> None:
        """更新评分缓存"""
        key = self._cache_key(requirements)
        self._score_cache[key] = (time.time(), scores)

    # ═══════════════════════════════════════════════════════════
    # 内部方法: 决策构建与日志
    # ═══════════════════════════════════════════════════════════

    def _build_decision(
        self, best: ModelScore, requirements: ModelRequirements, all_scores: List[ModelScore]
    ) -> RoutingDecision:
        """从最佳评分构建决策"""
        fallback = self._build_fallback_chain(
            (best.provider, best.model_version), requirements, all_scores
        )
        return RoutingDecision(
            primary_provider=best.provider,
            primary_model=best.model_version,
            fallback_chain=fallback,
            reason="缓存命中",
            estimated_cost_usd=best.estimated_cost_usd,
            estimated_time_sec=best.estimated_time_sec,
        )

    def _log_decision(
        self, decision: RoutingDecision, scores: List[ModelScore], rule_result: RuleEngineResult
    ) -> None:
        """记录决策日志"""
        log_entry = {
            "timestamp": time.time(),
            "primary_provider": decision.primary_provider.value,
            "primary_model": decision.primary_model.value,
            "fallback_chain": [p.value for p in decision.fallback_chain],
            "estimated_cost_usd": decision.estimated_cost_usd,
            "estimated_time_sec": decision.estimated_time_sec,
            "reason": decision.reason,
            "top_scores": [
                {
                    "provider": s.provider.value,
                    "model": s.model_version.value,
                    "score": round(s.total_score, 2),
                    "disqualified": s.disqualified,
                }
                for s in scores[:5]
            ],
            "rules_applied": {
                "mandatory": rule_result.mandatory_rules_applied,
                "recommended": rule_result.recommended_rules_applied,
            },
        }
        self._decision_log.append(log_entry)

    def _generate_explanation(
        self, decision: RoutingDecision, requirements: ModelRequirements
    ) -> str:
        """生成选型解释文本"""
        lines = [
            f"模型选型决策:",
            f"  首选: {decision.primary_provider.value} / {decision.primary_model.value}",
            f"  降级链: {' -> '.join([p.value for p in decision.fallback_chain]) or '无'}",
            f"  预估成本: ${decision.estimated_cost_usd:.3f}",
            f"  预估耗时: {decision.estimated_time_sec:.0f}秒",
            f"  决策依据: {decision.reason}",
            f"",
            f"请求特征:",
            f"  策略: {requirements.strategy.value}",
            f"  分辨率: {requirements.resolution.value}",
            f"  时长: {requirements.duration}秒",
            f"  中文占比: {requirements.chinese_ratio:.0%}",
            f"  生成音频: {'是' if requirements.generate_audio else '否'}",
        ]
        return "\n".join(lines)

    def __repr__(self: "ModelSelector") -> str:
        return f"ModelSelector(rules={len(self.rules)}, scores_cached={len(self._score_cache)})"


__all__ = [
    "ModelSelector",
    "PROVIDER_BASELINE_SCORES",
    "PRICING_TABLE",
    "GENERATION_TIME_TABLE",
    "DEFAULT_STRATEGY_WEIGHTS",
]
