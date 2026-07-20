#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
prompt_generator.py — 提示词生成器

职责:
- 选择 Gem → 构建 prompt → 调用 Gemini → 解析 → 校验
- 支持 JSON 和文本两种响应格式解析
- 支持 API 调用失败自动重试（指数退避）
- 支持校验失败自动重试（附加反馈）
- 完整的错误处理和结构化日志
- 通过 Protocol 解耦，便于 Mock 测试

依赖:
    - pydantic >= 2.0
    - gemini_client.py（同目录下的数据模型）

使用示例:
    ```python
    generator = PromptGenerator(gemini_client, gem_manager)
    result = await generator.generate(
        gem_id="luxury-video-gem",
        image_path="/path/to/watch.jpg",
        extra_requirements="体现高端质感",
    )
    print(result.prompt)  # 生成的视频提示词
    ```

作者: 300 Agent 融合项目
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from string import Template
from typing import Any, Protocol, Union, runtime_checkable

from pydantic import BaseModel, Field

# 从 gemini_client 导入共享数据模型
from gemini_client import (
    GeminiClientConfig,
    ImageInput,
    ImageSourceType,
    GemInfo,
    GenerationResult,
)

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════
# 自定义异常
# ═══════════════════════════════════════════════════════════════════════════


class GeminiModuleError(Exception):
    """模块基础异常"""

    pass


class PromptGenerationError(GeminiModuleError):
    """提示词生成失败

    当 API 调用重试全部失败或返回无效响应时抛出。
    """

    pass


class PromptValidationError(GeminiModuleError):
    """提示词校验失败

    当生成的提示词未通过质量校验时抛出。
    """

    pass


class GemNotFoundError(GeminiModuleError):
    """未找到指定 Gem"""

    pass


# ═══════════════════════════════════════════════════════════════════════════
# 数据模型
# ═══════════════════════════════════════════════════════════════════════════


class VideoPrompt(BaseModel):
    """视频提示词结构化输出

    PromptGenerator 内部解析使用的结构化模型，
    最终会被转换为 PromptResult 返回给调用方。
    """

    prompt: str = Field(description="完整的视频生成提示词（英文）")
    scene_description: str = Field(default="", description="场景描述摘要")
    style_tags: list[str] = Field(default_factory=list, description="风格标签列表")
    duration_estimate: str = Field(default="", description="预估时长")
    subject: str = Field(default="", description="主体描述")
    style: str = Field(default="", description="风格描述")
    motion: str = Field(default="", description="运动描述")
    camera: str = Field(default="", description="镜头描述")
    lighting: str = Field(default="", description="光影描述")
    atmosphere: str = Field(default="", description="氛围描述")
    negative_prompt: str = Field(default="", description="负面提示词")

    def to_flat_prompt(self: "VideoPrompt") -> str:
        """将结构化提示词扁平化为单段文本"""
        parts = [self.prompt]
        if self.negative_prompt:
            parts.append(f"Negative prompt: {self.negative_prompt}")
        return "\n".join(parts)


@dataclass
class PromptGeneratorConfig:
    """提示词生成器配置

    所有参数都有合理的默认值，可通过构造时传入覆盖。

    Attributes:
        max_retries: API 调用最大重试次数
        retry_delay: 重试延迟基数（秒）
        backoff_factor: 指数退避基数
        backoff_max: 最大退避时间（秒）
        validation_retry_max: 校验失败额外重试次数
        prompt_timeout: 生成超时（秒）
        min_prompt_length: 最小提示词长度
        max_prompt_length: 最大提示词长度
        require_json_output: 是否要求 JSON 输出
        default_gem_tag: 默认 Gem 标签
    """

    max_retries: int = 3
    retry_delay: float = 2.0
    backoff_factor: float = 2.0
    backoff_max: float = 60.0
    validation_retry_max: int = 2
    prompt_timeout: int = 60
    min_prompt_length: int = 10
    max_prompt_length: int = 2000
    require_json_output: bool = True
    default_gem_tag: str = "video-prompt"


class PromptValidation(BaseModel):
    """提示词校验结果"""

    is_valid: bool = Field(default=False, description="是否通过校验")
    has_subject: bool = Field(default=False, description="是否包含主体描述")
    has_style: bool = Field(default=False, description="是否包含风格描述")
    has_motion: bool = Field(default=False, description="是否包含运动描述")
    has_camera: bool = Field(default=False, description="是否包含镜头描述")
    keyword_count: int = Field(default=0, description="关键词数量")
    estimated_length: int = Field(default=0, description="预估长度")
    errors: list[str] = Field(default_factory=list, description="错误列表")
    warnings: list[str] = Field(default_factory=list, description="警告列表")


class PromptResult(BaseModel):
    """提示词生成结果"""

    prompt: str = Field(default="", description="生成的视频提示词")
    raw_text: str = Field(default="", description="原始响应文本")
    gem_used: str = Field(default="", description="使用的 Gem ID")
    gem_name: str = Field(default="", description="使用的 Gem 名称")
    model_used: str = Field(default="", description="使用的模型")
    generation_time_ms: int = Field(default=0, description="生成耗时（毫秒）")
    is_valid: bool = Field(default=False, description="是否通过校验")
    validation: PromptValidation = Field(
        default_factory=PromptValidation, description="校验结果"
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="附加元数据"
    )
    retry_count: int = Field(default=0, description="重试次数")


class GemMetadata(BaseModel):
    """Gem 元数据"""

    negative_prompt: str = Field(default="", description="负面提示词")
    style_keywords: list[str] = Field(default_factory=list, description="风格关键词")
    quality_requirements: str = Field(default="", description="质量要求")


# ═══════════════════════════════════════════════════════════════════════════
# Protocol 定义（用于类型提示和 Mock 测试）
# ═══════════════════════════════════════════════════════════════════════════


@runtime_checkable
class GeminiClientProtocol(Protocol):
    """GeminiClient 协议接口

    任何实现此协议的对象都可作为 PromptGenerator 的客户端，
    便于单元测试时注入 Mock 对象。
    """

    async def generate(
        self,
        prompt: str,
        files: list[Any] | None = None,
        gem_id: str | None = None,
        model: str | None = None,
        temporary: bool = True,
    ) -> Any:
        """调用 Gemini 生成内容"""
        ...


@runtime_checkable
class GemManagerProtocol(Protocol):
    """GemManager 协议接口"""

    async def get_gem_by_tag(self: "GemManagerProtocol", tag: str) -> GemInfo | None:
        """根据标签获取 Gem"""
        ...

    async def get_gem_by_id(self: "GemManagerProtocol", gem_id: str) -> GemInfo | None:
        """根据 ID 获取 Gem"""
        ...

    async def list_gems(self: "GemManagerProtocol") -> list[GemInfo]:
        """列出所有 Gems"""
        ...

    async def get_gem_metadata(self: "GemManagerProtocol", gem_id: str) -> GemMetadata | None:
        """获取 Gem 元数据"""
        ...


# ═══════════════════════════════════════════════════════════════════════════
# 提示词模板（类级别常量）
# ═══════════════════════════════════════════════════════════════════════════

_SYSTEM_PROMPT_TEMPLATE = Template(
    """你是一位专业的 AI 视频提示词工程师。你的任务是根据提供的产品图片和用户需求，生成一段高质量的英文视频生成提示词。

## 核心要求

1. **主体描述 (Subject)**: 清晰描述产品/人物/场景的主体特征
2. **场景设定 (Scene)**: 描述背景环境、空间布局
3. **镜头语言 (Camera)**: 包含镜头运动、景别变化、拍摄角度
4. **光影风格 (Lighting)**: 描述光源、色调、明暗对比
5. **氛围情绪 (Atmosphere)**: 传达情感基调和氛围感
6. **运动描述 (Motion)**: 描述主体或镜头的动态变化

## 输出规范

- 使用 **英文** 输出（AI 视频模型对英文理解最佳）
- 提示词长度控制在 **100-500 词** 之间
- 使用 **逗号分隔的关键词风格**，便于模型解析
- 按重要性排序：主体 > 场景 > 镜头 > 光影 > 氛围 > 运动
- 避免使用品牌名、商标、版权内容
- 避免使用抽象概念，用具体的视觉描述替代

## 格式要求

请以 **JSON 格式** 返回结果，确保可以被标准 JSON 解析器解析：

```json
{
    "video_prompt": "完整的视频提示词（英文，逗号分隔的关键词风格）",
    "scene_description": "场景描述摘要",
    "style_tags": ["风格标签1", "风格标签2", "风格标签3"],
    "duration_estimate": "预估时长（秒）",
    "subject": "主体描述",
    "style": "风格描述",
    "motion": "运动描述",
    "camera": "镜头描述",
    "lighting": "光影描述",
    "atmosphere": "氛围描述",
    "negative_prompt": "负面提示词（可选）"
}
```

## 质量标准

- 提示词应具体到可以指导视频生成，而非笼统描述
- 每个描述元素都应该是可视觉化的
- 使用专业视频/摄影术语提升生成质量
- 确保描述逻辑一致，不要出现矛盾

$negative_prompt_section
"""
)

_USER_MESSAGE_TEMPLATE = Template(
    """请根据以下信息生成视频提示词：

**产品需求**: $requirement
$style_hint_section
$extra_requirements_section
请仔细分析产品图片的特征，生成高质量的视频提示词。务必以 JSON 格式返回。"""
)



# ═══════════════════════════════════════════════════════════════════════════
# PromptGenerator 主类
# ═══════════════════════════════════════════════════════════════════════════


class PromptGenerator:
    """提示词生成器

    核心业务流程: 选择 Gem → 构建 prompt → 调用 Gemini → 解析 → 校验

    特性:
    - 支持 JSON 和文本两种响应格式解析
    - API 调用失败自动重试（指数退避）
    - 校验失败自动重试（附加反馈）
    - 完整的错误处理和结构化日志
    - 通过 Protocol 解耦，便于 Mock 测试

    Args:
        gemini_client: Gemini 客户端实例（必须实现 generate 方法）
        gem_manager: Gem 管理器实例（可选，用于获取 Gem 元数据）
        config: 生成器配置（可选，使用默认配置）

    Example:
        >>> generator = PromptGenerator(gemini_client, gem_manager)
        >>> result = await generator.generate(
        ...     gem_id="luxury-video-gem",
        ...     image_path="/path/to/watch.jpg",
        ...     extra_requirements="体现高端质感",
        ... )
        >>> print(result.prompt)
        'A luxurious golden wristwatch...'
    """

    _system_template: Template = _SYSTEM_PROMPT_TEMPLATE
    _user_template: Template = _USER_MESSAGE_TEMPLATE

    def __init__(
        self,
        gemini_client: GeminiClientProtocol,
        gem_manager: GemManagerProtocol | None = None,
        config: PromptGeneratorConfig | None = None,
    ) -> None:
        self._client = gemini_client
        self._gem_manager = gem_manager
        self._config = config or PromptGeneratorConfig()
        self._logger = logger.getChild("PromptGenerator")

    # ───────────────────────────────────────────────────────────────────
    # 公共 API
    # ───────────────────────────────────────────────────────────────────

    async def generate(
        self,
        gem_id: str,
        image_path: str | Path | ImageInput,
        extra_requirements: str = "",
        style_hint: str | None = None,
        model: str | None = None,
    ) -> PromptResult:
        """生成视频提示词（主入口）

        完整流程:
        1. 获取 Gem 信息和元数据
        2. 构建系统提示词和用户消息
        3. 调用 Gemini（带 API 重试）
        4. 解析响应（JSON → 文本降级）
        5. 校验结果（带校验重试）
        6. 返回 PromptResult

        Args:
            gem_id: Gem ID（用于选择系统提示词）
            image_path: 产品图片路径或 ImageInput 对象
            extra_requirements: 额外需求描述
            style_hint: 风格提示（如 "cinematic, dark"）
            model: 指定模型（可选）

        Returns:
            PromptResult: 包含生成的提示词和元数据

        Raises:
            PromptGenerationError: 生成失败（已重试所有次数）
        """
        start_time = time.monotonic()
        api_retry_count = 0
        validation_retry_count = 0
        last_validation_errors: list[str] = []

        self._logger.info(
            "开始生成视频提示词: gem_id=%s, image=%s, requirements=%s, style=%s",
            gem_id,
            str(image_path)[:50],
            extra_requirements[:50] if extra_requirements else "",
            style_hint or "",
        )

        # Phase 1: 获取 Gem 信息
        gem_info = await self._resolve_gem(gem_id)
        gem_metadata = None
        if self._gem_manager:
            try:
                gem_metadata = await self._gem_manager.get_gem_metadata(gem_id)
            except Exception as e:
                self._logger.debug("无法获取 Gem 元数据: %s", e)

        # Phase 2: 准备图片
        image_input = self._resolve_image_input(image_path)

        # Phase 3: 构建提示词
        system_prompt = self._build_system_prompt(gem_metadata)

        # Phase 4: 生成循环（含重试）
        while True:
            user_message = self._build_user_message(
                requirement=extra_requirements
                or "根据产品图片生成视频提示词",
                style_hint=style_hint,
                extra_requirements=extra_requirements,
                validation_feedback=last_validation_errors
                if validation_retry_count > 0
                else None,
            )

            # 调用 Gemini（带 API 重试）
            try:
                raw_text, api_retry_count = await self._call_gemini_with_retry(
                    system_prompt=system_prompt,
                    user_message=user_message,
                    image_input=image_input,
                    model=model,
                )
            except Exception as e:
                generation_time_ms = int((time.monotonic() - start_time) * 1000)
                self._logger.error("Gemini 调用失败: %s", str(e))
                raise PromptGenerationError(
                    f"提示词生成失败，已重试 {self._config.max_retries} 次: {e}"
                ) from e

            # Phase 5: 解析响应
            video_prompt = self._parse_response(raw_text)

            # Phase 6: 校验结果
            validation = self._validate_prompt(video_prompt.prompt)

            if validation.is_valid:
                generation_time_ms = int(
                    (time.monotonic() - start_time) * 1000
                )
                self._logger.info(
                    "视频提示词生成成功: length=%d, gem=%s, "
                    "time=%dms, retries=%d",
                    len(video_prompt.prompt),
                    gem_info.id,
                    generation_time_ms,
                    api_retry_count + validation_retry_count,
                )
                return PromptResult(
                    prompt=video_prompt.prompt,
                    raw_text=raw_text,
                    gem_used=gem_info.id,
                    gem_name=gem_info.name,
                    model_used=model or "default",
                    generation_time_ms=generation_time_ms,
                    is_valid=True,
                    validation=validation,
                    metadata=video_prompt.model_dump(),
                    retry_count=api_retry_count + validation_retry_count,
                )

            # Phase 7: 校验失败处理
            validation_retry_count += 1
            if validation_retry_count <= self._config.validation_retry_max:
                last_validation_errors = (
                    validation.errors + validation.warnings
                )
                self._logger.warning(
                    "提示词校验失败，第 %d 次重试: errors=%s",
                    validation_retry_count,
                    last_validation_errors,
                )
                continue
            else:
                generation_time_ms = int(
                    (time.monotonic() - start_time) * 1000
                )
                self._logger.error(
                    "提示词校验失败，已达最大重试次数: errors=%s",
                    validation.errors,
                )
                return PromptResult(
                    prompt=video_prompt.prompt,
                    raw_text=raw_text,
                    gem_used=gem_info.id,
                    gem_name=gem_info.name,
                    model_used=model or "default",
                    generation_time_ms=generation_time_ms,
                    is_valid=False,
                    validation=validation,
                    metadata=video_prompt.model_dump(),
                    retry_count=api_retry_count + validation_retry_count,
                )

    # ───────────────────────────────────────────────────────────────────
    # 内部方法: Gem 和图片解析
    # ───────────────────────────────────────────────────────────────────

    async def _resolve_gem(self: "PromptGenerator", gem_id: str) -> GemInfo:
        """解析 Gem ID 为 GemInfo

        优先从 GemManager 获取，如失败则创建默认 GemInfo。

        Args:
            gem_id: Gem ID

        Returns:
            GemInfo: Gem 信息
        """
        if self._gem_manager:
            try:
                gem = await self._gem_manager.get_gem_by_id(gem_id)
                if gem:
                    return gem
            except Exception as e:
                self._logger.debug("从 GemManager 获取失败: %s", e)

        return GemInfo(id=gem_id, name=gem_id, source="manual")

    def _resolve_image_input(
        self, image_path: str | Path | ImageInput
    ) -> ImageInput:
        """统一解析为 ImageInput

        Args:
            image_path: 图片路径、Path 对象或 ImageInput

        Returns:
            ImageInput: 标准化的图片输入

        Raises:
            FileNotFoundError: 图片文件不存在
        """
        if isinstance(image_path, ImageInput):
            return image_path
        return ImageInput.from_path(image_path)

    # ───────────────────────────────────────────────────────────────────
    # 内部方法: 提示词构建
    # ───────────────────────────────────────────────────────────────────

    def _build_system_prompt(
        self, gem_metadata: GemMetadata | None = None
    ) -> str:
        """构建系统提示词

        Args:
            gem_metadata: Gem 元数据（包含风格关键词、负面提示词等）

        Returns:
            str: 完整的系统提示词
        """
        negative_section = self._build_negative_prompt_section(gem_metadata)
        return self._system_template.substitute(
            negative_prompt_section=negative_section
        )

    def _build_user_message(
        self,
        requirement: str,
        style_hint: str | None = None,
        extra_requirements: str | None = None,
        validation_feedback: list[str] | None = None,
    ) -> str:
        """构建用户消息

        Args:
            requirement: 主要需求描述
            style_hint: 风格提示
            extra_requirements: 额外要求
            validation_feedback: 之前的校验反馈（用于重试时改进）

        Returns:
            str: 完整的用户消息
        """
        style_hint_section = (
            f"\n**风格偏好**: {style_hint}\n" if style_hint else ""
        )
        extra_requirements_section = ""
        if extra_requirements:
            extra_requirements_section = (
                f"\n**额外要求**: {extra_requirements}\n"
            )
        if validation_feedback:
            feedback_text = "; ".join(validation_feedback)
            extra_requirements_section += (
                f"\n**改进要求**: 请修正以下问题: {feedback_text}\n"
            )

        return self._user_template.substitute(
            requirement=requirement,
            style_hint_section=style_hint_section,
            extra_requirements_section=extra_requirements_section,
        )

    def _build_negative_prompt_section(
        self, gem_metadata: GemMetadata | None
    ) -> str:
        """构建负面提示词段落

        合并 Gem 元数据中的负面提示词和默认负面提示词。

        Args:
            gem_metadata: Gem 元数据

        Returns:
            str: 负面提示词段落文本
        """
        negative = ""
        if gem_metadata and gem_metadata.negative_prompt:
            negative = gem_metadata.negative_prompt

        default_negative = (
            " blurry, low quality, distorted, deformed, ugly, duplicate, "
            "watermark, signature, text, logo, cropped, out of frame, "
            "worst quality, bad anatomy, extra limbs, missing fingers"
        )

        if negative:
            return (
                f"## 负面提示词（请避免以下内容）\n\n"
                f"{negative}, {default_negative}\n"
            )
        return (
            f"## 负面提示词（请避免以下内容）\n\n"
            f"{default_negative}\n"
        )

    # ───────────────────────────────────────────────────────────────────
    # 内部方法: Gemini 调用（带重试）
    # ───────────────────────────────────────────────────────────────────

    async def _call_gemini_with_retry(
        self,
        system_prompt: str,
        user_message: str,
        image_input: ImageInput,
        model: str | None = None,
    ) -> tuple[str, int]:
        """调用 Gemini 并自动重试

        使用指数退避策略，可重试的异常包括 TimeoutError、
        ConnectionError、APIError 等。

        Args:
            system_prompt: 系统提示词
            user_message: 用户消息
            image_input: 图片输入
            model: 模型选择

        Returns:
            tuple[str, int]: (原始文本响应, API 重试次数)

        Raises:
            PromptGenerationError: 所有重试都失败
        """
        last_exception: Exception | None = None

        for attempt in range(1, self._config.max_retries + 1):
            try:
                self._logger.debug("第 %d 次调用 Gemini...", attempt)

                full_prompt = f"{system_prompt}\n\n{user_message}"

                result = await asyncio.wait_for(
                    self._client.generate(
                        prompt=full_prompt,
                        files=[image_input],
                        gem_id=None,
                        model=model,
                        temporary=True,
                    ),
                    timeout=self._config.prompt_timeout,
                )

                raw_text = self._extract_text_from_result(result)

                if raw_text and len(raw_text.strip()) > 0:
                    self._logger.debug(
                        "Gemini 调用成功: text_length=%d", len(raw_text)
                    )
                    return raw_text, attempt - 1
                else:
                    raise PromptGenerationError("Gemini 返回空响应")

            except asyncio.TimeoutError as e:
                last_exception = e
                self._logger.warning("第 %d 次调用超时", attempt)
            except PromptGenerationError:
                raise
            except Exception as e:
                if not self._should_retry(e):
                    self._logger.error(
                        "不可重试的异常: %s: %s",
                        type(e).__name__,
                        str(e),
                    )
                    raise PromptGenerationError(f"Gemini 调用失败: {e}") from e

                last_exception = e
                self._logger.warning(
                    "第 %d 次调用失败: %s: %s",
                    attempt,
                    type(e).__name__,
                    str(e),
                )

            if attempt < self._config.max_retries:
                delay = min(
                    self._config.retry_delay
                    * (self._config.backoff_factor ** (attempt - 1)),
                    self._config.backoff_max,
                )
                self._logger.info("等待 %.1fs 后重试...", delay)
                await asyncio.sleep(delay)

        raise PromptGenerationError(
            f"Gemini 调用失败，已重试 {self._config.max_retries} 次"
        ) from last_exception

    def _extract_text_from_result(self: "PromptGenerator", result: Any) -> str:
        """从 Gemini 返回结果中提取文本

        适配不同的返回格式:
        - str 类型: 直接返回
        - dict 类型: 取 "text" 或 "response" 字段
        - 对象类型: 取 .text 或 .response.text 属性

        Args:
            result: Gemini 返回结果

        Returns:
            str: 提取的文本
        """
        if isinstance(result, str):
            return result

        if isinstance(result, dict):
            return (
                result.get("text", "")
                or result.get("response", "")
                or ""
            )

        if hasattr(result, "text"):
            return getattr(result, "text", "") or ""
        if hasattr(result, "response"):
            resp = getattr(result, "response")
            if hasattr(resp, "text"):
                return getattr(resp, "text", "") or ""

        return str(result) if result else ""

    def _should_retry(self: "PromptGenerator", error: Exception) -> bool:
        """判断异常是否值得重试

        可重试: TimeoutError, ConnectionError, APIError 等临时错误
        不可重试: AuthError, ValueError, ModelInvalid 等永久性错误

        Args:
            error: 异常对象

        Returns:
            bool: 是否值得重试
        """
        error_name = type(error).__name__
        error_str = str(error).lower()

        no_retry_errors = [
            "AuthError",
            "AuthenticationError",
            "Unauthorized",
            "ModelInvalid",
            "ModelNotFound",
            "LOCATION_REJECTED",
            "LocationError",
            "RegionBlocked",
            "PermissionError",
            "ValueError",
        ]

        if error_name in no_retry_errors:
            return False

        no_retry_keywords = [
            "auth",
            "unauthorized",
            "permission",
            "location",
            "region",
        ]
        if any(kw in error_str for kw in no_retry_keywords):
            return False

        return True

    # ───────────────────────────────────────────────────────────────────
    # 内部方法: 响应解析
    # ───────────────────────────────────────────────────────────────────

    def _parse_response(self: "PromptGenerator", raw_text: str) -> VideoPrompt:
        """解析 Gemini 响应为 VideoPrompt

        解析策略:
        1. 先尝试 JSON 解析（带代码块和不带代码块）
        2. JSON 解析失败则使用文本提取

        Args:
            raw_text: Gemini 原始响应文本

        Returns:
            VideoPrompt: 解析后的视频提示词对象
        """
        self._logger.debug(
            "解析响应: text_length=%d",
            len(raw_text) if raw_text else 0,
        )

        if not raw_text or not raw_text.strip():
            self._logger.warning("Gemini 返回空响应")
            return VideoPrompt(prompt="")

        json_result = self._try_parse_json(raw_text)
        if json_result:
            self._logger.debug("JSON 解析成功")
            return self._json_to_video_prompt(json_result)

        self._logger.debug("JSON 解析失败，降级到文本提取")
        text_prompt = self._try_parse_text(raw_text)
        return VideoPrompt(prompt=text_prompt)

    def _try_parse_json(self: "PromptGenerator", raw_text: str) -> dict[str, Any] | None:
        """尝试从响应文本中解析 JSON

        策略:
        1. 提取 ```json ... ``` 代码块
        2. 提取最外层 {...}
        3. json.loads() 解析

        Args:
            raw_text: 原始响应文本

        Returns:
            dict | None: 解析后的字典，失败返回 None
        """
        text = raw_text.strip()
        json_str = None

        # 策略1: 提取代码块
        code_block_pattern = r"```(?:json)?\s*\n?(.*?)\n?\s*```"
        matches = re.findall(code_block_pattern, text, re.DOTALL)
        if matches:
            json_str = max(matches, key=len).strip()

        # 策略2: 提取最外层 {...}
        if not json_str:
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1 and end > start:
                json_str = text[start : end + 1]

        if not json_str:
            return None

        try:
            result = json.loads(json_str)
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass

        return None

    def _json_to_video_prompt(self: "PromptGenerator", data: dict[str, Any]) -> VideoPrompt:
        """将 JSON 字典转换为 VideoPrompt

        Args:
            data: 解析后的 JSON 字典

        Returns:
            VideoPrompt: 视频提示词对象
        """
        return VideoPrompt(
            prompt=data.get("video_prompt", ""),
            scene_description=data.get("scene_description", ""),
            style_tags=data.get("style_tags", []),
            duration_estimate=data.get("duration_estimate", ""),
            subject=data.get("subject", ""),
            style=data.get("style", ""),
            motion=data.get("motion", ""),
            camera=data.get("camera", ""),
            lighting=data.get("lighting", ""),
            atmosphere=data.get("atmosphere", ""),
            negative_prompt=data.get("negative_prompt", ""),
        )

    def _try_parse_text(self: "PromptGenerator", raw_text: str) -> str:
        """从文本响应中提取提示词

        策略:
        1. 清理 Markdown 标记
        2. 去除常见前缀
        3. 提取最长且有意义的段落

        Args:
            raw_text: 原始响应文本

        Returns:
            str: 提取的提示词文本
        """
        if not raw_text:
            return ""

        text = raw_text

        # 步骤1: 清理 Markdown 标记
        text = re.sub(r"\*\*(.*?)\*\*", r"\1", text)
        text = re.sub(r"\*(.*?)\*", r"\1", text)
        text = re.sub(r"`(.*?)`", r"\1", text)
        text = re.sub(r"```.*?```", "", text, flags=re.DOTALL)

        # 步骤2: 去除常见前缀
        prefixes = [
            r"^(?:video\s*)?prompt[:：]\s*",
            r"^here\s+(?:is\s+)?(?:the\s+)?(?:video\s+)?prompt[:：]?\s*",
            r"^generated\s+(?:video\s+)?prompt[:：]?\s*",
        ]
        for prefix in prefixes:
            text = re.sub(prefix, "", text, flags=re.IGNORECASE)

        # 步骤3: 提取最长段落
        paragraphs = [
            p.strip() for p in text.split("\n\n") if p.strip()
        ]

        video_keywords = [
            "camera",
            "shot",
            "scene",
            "lighting",
            "cinematic",
            "close-up",
            "wide shot",
            "tracking",
            "zoom",
        ]

        best_paragraph = ""
        for para in paragraphs:
            lines = [
                l.strip() for l in para.split("\n") if len(l.strip()) > 50
            ]
            for line in lines:
                para_lower = line.lower()
                score = sum(
                    1 for kw in video_keywords if kw in para_lower
                )
                if score >= 2 and len(line) > len(best_paragraph):
                    best_paragraph = line

        if not best_paragraph:
            for para in paragraphs:
                lines = [
                    l.strip()
                    for l in para.split("\n")
                    if len(l.strip()) > 50
                ]
                for line in lines:
                    if len(line) > len(best_paragraph):
                        best_paragraph = line

        if not best_paragraph:
            best_paragraph = text.strip()

        return best_paragraph

    # ───────────────────────────────────────────────────────────────────
    # 内部方法: 校验
    # ───────────────────────────────────────────────────────────────────

    def _validate_prompt(self: "PromptGenerator", prompt: str) -> PromptValidation:
        """校验生成的提示词质量

        校验维度:
        - 基础: 长度、空值
        - 内容: 主体、风格、运动、镜头
        - 质量: 关键词数量

        通过条件: has_subject == True AND errors 为空

        Args:
            prompt: 提示词文本

        Returns:
            PromptValidation: 校验结果
        """
        validation = PromptValidation(estimated_length=len(prompt))

        if not prompt:
            validation.errors.append("提示词为空")
            return validation

        prompt_stripped = prompt.strip()
        if len(prompt_stripped) < self._config.min_prompt_length:
            validation.errors.append(
                f"提示词过短（{len(prompt_stripped)} 字符，"
                f"最少 {self._config.min_prompt_length}）"
            )
            return validation

        if len(prompt_stripped) > self._config.max_prompt_length:
            validation.warnings.append(
                f"提示词较长（{len(prompt_stripped)} 字符，"
                f"建议不超过 {self._config.max_prompt_length}）"
            )

        prompt_lower = prompt_stripped.lower()

        # 内容校验: 主体描述
        subject_keywords = [
            "subject",
            "character",
            "person",
            "product",
            "object",
            "scene",
            "landscape",
            "woman",
            "man",
            "watch",
            "phone",
            "car",
            "bottle",
            "earbuds",
            "perfume",
        ]
        validation.has_subject = any(
            kw in prompt_lower for kw in subject_keywords
        )

        # 内容校验: 风格描述
        style_keywords = [
            "style",
            "realistic",
            "anime",
            "3d",
            "cartoon",
            "cinematic",
            "photorealistic",
            "luxury",
            "elegant",
            "modern",
            "minimalist",
            "professional",
        ]
        validation.has_style = any(
            kw in prompt_lower for kw in style_keywords
        )

        # 内容校验: 运动描述
        motion_keywords = [
            "moving",
            "walking",
            "running",
            "rotating",
            "flying",
            "dancing",
            "motion",
            "animation",
            "orbit",
            "pan",
            "tracking",
            "push-in",
            "pull-back",
        ]
        validation.has_motion = any(
            kw in prompt_lower for kw in motion_keywords
        )

        # 内容校验: 镜头描述
        camera_keywords = [
            "camera",
            "zoom",
            "pan",
            "close-up",
            "wide shot",
            "tracking",
            "aerial",
            "drone",
            "shot",
            "macro",
            "depth of field",
            "bokeh",
        ]
        validation.has_camera = any(
            kw in prompt_lower for kw in camera_keywords
        )

        # 关键词数量
        validation.keyword_count = len(
            [w for w in prompt_stripped.split() if len(w) > 2]
        )

        # 质量建议
        if len(prompt_stripped) < 50:
            validation.warnings.append(
                "提示词较短，建议增加更多细节描述"
            )
        if validation.keyword_count < 10:
            validation.warnings.append(
                "关键词较少，建议丰富描述内容"
            )
        if not validation.has_style:
            validation.warnings.append(
                "缺少风格描述，建议添加风格关键词"
            )
        if not validation.has_motion:
            validation.warnings.append(
                "缺少运动描述，建议添加镜头或主体运动"
            )

        # 综合判定: 至少要有主体描述
        validation.is_valid = (
            validation.has_subject and len(validation.errors) == 0
        )

        if not validation.has_subject:
            validation.errors.append(
                "缺少主体描述，提示词应包含主体（人物/产品/场景等）"
            )

        self._logger.debug(
            "校验结果: valid=%s, subject=%s, style=%s, motion=%s, "
            "camera=%s, keywords=%d",
            validation.is_valid,
            validation.has_subject,
            validation.has_style,
            validation.has_motion,
            validation.has_camera,
            validation.keyword_count,
        )

        return validation



# ═══════════════════════════════════════════════════════════════════════════
# 单元测试
# ═══════════════════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════════════════
# 单元测试（模块级别，供 pytest 自动发现）
# ═══════════════════════════════════════════════════════════════════════════


import asyncio
import tempfile
from unittest.mock import AsyncMock, MagicMock

import pytest

# ── Mock 类 ──

class MockGeminiClient:
    """Mock Gemini 客户端"""

    def __init__(
        self,
        response_text: str = "",
        fail_count: int = 0,
        exception_type: type = Exception,
    ) -> None:
        self.response_text = response_text
        self.call_count = 0
        self.fail_count = fail_count
        self.exception_type = exception_type
        self.last_call_args: dict | None = None

    async def generate(
        self,
        prompt: str = "",
        files: list[Any] | None = None,
        gem_id: str | None = None,
        model: str | None = None,
        temporary: bool = True,
    ) -> Any:
        self.call_count += 1
        self.last_call_args = {
            "prompt": prompt,
            "files": files,
            "gem_id": gem_id,
            "model": model,
            "temporary": temporary,
        }
        if self.call_count <= self.fail_count:
            raise self.exception_type(
                f"Mock 第 {self.call_count} 次失败"
            )
        return self.response_text

class MockGemManager:
    """Mock Gem 管理器"""

    def __init__(
        self, gems: dict[str, GemInfo] | None = None
    ) -> None:
        self.gems = gems or {}
        self.metadata: dict[str, GemMetadata] = {}

    async def get_gem_by_id(self: "MockGemManager", gem_id: str) -> GemInfo | None:
        return self.gems.get(gem_id)

    async def get_gem_metadata(
        self, gem_id: str
    ) -> GemMetadata | None:
        return self.metadata.get(gem_id)

    async def list_gems(self: "MockGemManager") -> list[GemInfo]:
        return list(self.gems.values())

    async def get_gem_by_tag(self: "MockGemManager", tag: str) -> GemInfo | None:
        for gem in self.gems.values():
            if tag in gem.tags:
                return gem
        return None

# ── Fixtures ──

@pytest.fixture
def mock_gem_manager() -> None:
    """Mock Gem 管理器夹具"""
    gm = MockGemManager()
    gm.gems["gem1"] = GemInfo(id="gem1", name="Gem1")
    return gm

@pytest.fixture
def fast_config() -> None:
    """快速测试配置"""
    return PromptGeneratorConfig(
        max_retries=3,
        retry_delay=0.1,
        backoff_factor=2.0,
        validation_retry_max=2,
        prompt_timeout=5,
    )

@pytest.fixture
def tmp_image(tmp_path: Path) -> None:
    """临时图片文件"""
    img_file = tmp_path / "test.jpg"
    img_file.write_bytes(b"fake image data for testing")
    return str(img_file)

# ── 测试数据模型 ──

class TestVideoPrompt:
    """VideoPrompt 测试"""

    def test_create(self: "TestVideoPrompt") -> None:
        """创建 VideoPrompt"""
        vp = VideoPrompt(
            prompt="A cinematic shot of a watch",
            scene_description="Luxury watch showcase",
            style_tags=["cinematic", "luxury"],
        )
        assert vp.prompt == "A cinematic shot of a watch"
        assert vp.scene_description == "Luxury watch showcase"
        assert vp.style_tags == ["cinematic", "luxury"]

    def test_to_flat_prompt(self: "TestVideoPrompt") -> None:
        """扁平化提示词"""
        vp = VideoPrompt(
            prompt="A cinematic shot",
            negative_prompt="blurry, low quality",
        )
        flat = vp.to_flat_prompt()
        assert "A cinematic shot" in flat
        assert "Negative prompt:" in flat

    def test_defaults(self: "TestVideoPrompt") -> None:
        """默认值"""
        vp = VideoPrompt(prompt="test")
        assert vp.scene_description == ""
        assert vp.style_tags == []
        assert vp.subject == ""
        assert vp.negative_prompt == ""

class TestPromptGeneratorConfig:
    """PromptGeneratorConfig 测试"""

    def test_defaults(self: "TestPromptGeneratorConfig") -> None:
        """默认配置"""
        config = PromptGeneratorConfig()
        assert config.max_retries == 3
        assert config.retry_delay == 2.0
        assert config.backoff_factor == 2.0
        assert config.validation_retry_max == 2
        assert config.min_prompt_length == 10
        assert config.max_prompt_length == 2000
        assert config.require_json_output is True

    def test_custom_values(self: "TestPromptGeneratorConfig") -> None:
        """自定义配置"""
        config = PromptGeneratorConfig(
            max_retries=5,
            retry_delay=1.0,
            min_prompt_length=20,
        )
        assert config.max_retries == 5
        assert config.retry_delay == 1.0
        assert config.min_prompt_length == 20

class TestPromptValidation:
    """PromptValidation 测试"""

    def test_create(self: "TestPromptValidation") -> None:
        """创建校验结果"""
        pv = PromptValidation(
            is_valid=True,
            has_subject=True,
            has_style=True,
            keyword_count=15,
        )
        assert pv.is_valid is True
        assert pv.has_subject is True
        assert pv.errors == []

    def test_invalid(self: "TestPromptValidation") -> None:
        """无效结果"""
        pv = PromptValidation(
            is_valid=False,
            errors=["提示词为空"],
        )
        assert pv.is_valid is False
        assert len(pv.errors) == 1

class TestPromptResult:
    """PromptResult 测试"""

    def test_create(self: "TestPromptResult") -> None:
        """创建结果"""
        pr = PromptResult(
            prompt="A cinematic shot",
            gem_used="gem1",
            is_valid=True,
            retry_count=0,
        )
        assert pr.prompt == "A cinematic shot"
        assert pr.is_valid is True

class TestGemMetadata:
    """GemMetadata 测试"""

    def test_create(self: "TestGemMetadata") -> None:
        """创建元数据"""
        gm = GemMetadata(
            negative_prompt="blurry",
            style_keywords=["cinematic"],
        )
        assert gm.negative_prompt == "blurry"
        assert gm.style_keywords == ["cinematic"]

# ── 测试提示词构建 ──

class TestPromptBuilding:
    """提示词构建测试"""

    def test_build_system_prompt(self: "TestPromptBuilding", fast_config: PromptGeneratorConfig) -> None:
        """构建系统提示词"""
        client = MockGeminiClient()
        gen = PromptGenerator(client, config=fast_config)
        prompt = gen._build_system_prompt()
        assert "视频提示词工程师" in prompt
        assert "JSON 格式" in prompt
        assert "负面提示词" in prompt

    def test_build_system_prompt_with_metadata(self: "TestPromptBuilding", fast_config: PromptGeneratorConfig) -> None:
        """带元数据的系统提示词"""
        client = MockGeminiClient()
        meta = GemMetadata(negative_prompt="watermark, blurry")
        gen = PromptGenerator(client, config=fast_config)
        prompt = gen._build_system_prompt(meta)
        assert "watermark" in prompt

    def test_build_user_message_basic(self: "TestPromptBuilding", fast_config: PromptGeneratorConfig) -> None:
        """基本用户消息"""
        client = MockGeminiClient()
        gen = PromptGenerator(client, config=fast_config)
        msg = gen._build_user_message(requirement="高端手表展示")
        assert "高端手表展示" in msg
        assert "JSON 格式返回" in msg

    def test_build_user_message_with_style(self: "TestPromptBuilding", fast_config: PromptGeneratorConfig) -> None:
        """带风格的用户消息"""
        client = MockGeminiClient()
        gen = PromptGenerator(client, config=fast_config)
        msg = gen._build_user_message(
            requirement="手表", style_hint="cinematic, dark"
        )
        assert "cinematic, dark" in msg

    def test_build_user_message_with_feedback(self: "TestPromptBuilding", fast_config: PromptGeneratorConfig) -> None:
        """带校验反馈的用户消息"""
        client = MockGeminiClient()
        gen = PromptGenerator(client, config=fast_config)
        msg = gen._build_user_message(
            requirement="手表",
            validation_feedback=["提示词过短", "缺少主体"],
        )
        assert "改进要求" in msg
        assert "提示词过短" in msg

    def test_build_negative_prompt_section_default(self: "TestPromptBuilding", fast_config: PromptGeneratorConfig) -> None:
        """默认负面提示词"""
        client = MockGeminiClient()
        gen = PromptGenerator(client, config=fast_config)
        section = gen._build_negative_prompt_section(None)
        assert "blurry" in section
        assert "low quality" in section

    def test_build_negative_prompt_section_with_meta(self: "TestPromptBuilding", fast_config: PromptGeneratorConfig) -> None:
        """带元数据的负面提示词"""
        client = MockGeminiClient()
        gen = PromptGenerator(client, config=fast_config)
        meta = GemMetadata(negative_prompt="cartoon style")
        section = gen._build_negative_prompt_section(meta)
        assert "cartoon style" in section

# ── 测试 Gemini 调用 ──

class TestGeminiCall:
    """Gemini 调用测试"""

    @pytest.mark.asyncio
    async def test_call_success(self: "TestGeminiCall", fast_config, tmp_image: ImageInput) -> None:
        """成功调用"""
        mock = MockGeminiClient(
            response_text='{"video_prompt": "test prompt"}'
        )
        gen = PromptGenerator(mock, config=fast_config)
        img = ImageInput.from_path(tmp_image)

        text, retries = await gen._call_gemini_with_retry(
            system_prompt="sys", user_message="user", image_input=img
        )
        assert text == '{"video_prompt": "test prompt"}'
        assert retries == 0
        assert mock.call_count == 1

    @pytest.mark.asyncio
    async def test_call_retry_then_success(
        self, fast_config: PromptGeneratorConfig, tmp_image
    ) -> None:
        """重试后成功"""
        mock = MockGeminiClient(
            response_text='{"video_prompt": "success"}',
            fail_count=2,
            exception_type=TimeoutError,
        )
        config = PromptGeneratorConfig(
            max_retries=3,
            retry_delay=0.01,
            backoff_factor=1.0,
            prompt_timeout=5,
        )
        gen = PromptGenerator(mock, config=config)
        img = ImageInput.from_path(tmp_image)

        text, retries = await gen._call_gemini_with_retry(
            system_prompt="sys", user_message="user", image_input=img
        )
        assert "success" in text
        assert retries == 2
        assert mock.call_count == 3

    @pytest.mark.asyncio
    async def test_call_non_retryable_error(
        self, fast_config: PromptGeneratorConfig, tmp_image
    ) -> None:
        """不可重试异常"""
        mock = MockGeminiClient(
            fail_count=1, exception_type=ValueError
        )
        gen = PromptGenerator(mock, config=fast_config)
        img = ImageInput.from_path(tmp_image)

        with pytest.raises(PromptGenerationError):
            await gen._call_gemini_with_retry(
                system_prompt="sys",
                user_message="user",
                image_input=img,
            )
        assert mock.call_count == 1

    @pytest.mark.asyncio
    async def test_call_all_retries_failed(
        self, fast_config: PromptGeneratorConfig, tmp_image
    ) -> None:
        """所有重试都失败"""
        mock = MockGeminiClient(
            fail_count=5, exception_type=ConnectionError
        )
        config = PromptGeneratorConfig(
            max_retries=3,
            retry_delay=0.01,
            backoff_factor=1.0,
            prompt_timeout=5,
        )
        gen = PromptGenerator(mock, config=config)
        img = ImageInput.from_path(tmp_image)

        with pytest.raises(PromptGenerationError):
            await gen._call_gemini_with_retry(
                system_prompt="sys",
                user_message="user",
                image_input=img,
            )
        assert mock.call_count == 3

    def test_extract_text_from_str(self: "TestGeminiCall") -> None:
        """从字符串提取"""
        gen = PromptGenerator(MockGeminiClient())
        assert gen._extract_text_from_result("hello") == "hello"

    def test_extract_text_from_dict(self: "TestGeminiCall") -> None:
        """从字典提取"""
        gen = PromptGenerator(MockGeminiClient())
        assert (
            gen._extract_text_from_result({"text": "hello"}) == "hello"
        )
        assert (
            gen._extract_text_from_result({"response": "hi"}) == "hi"
        )

    def test_extract_text_from_object(self: "TestGeminiCall") -> None:
        """从对象提取"""
        gen = PromptGenerator(MockGeminiClient())
        obj = MagicMock()
        obj.text = "hello"
        assert gen._extract_text_from_result(obj) == "hello"

    def test_should_retry(self: "TestGeminiCall") -> None:
        """重试判断"""
        gen = PromptGenerator(MockGeminiClient())
        assert gen._should_retry(TimeoutError("timeout")) is True
        assert gen._should_retry(ConnectionError("conn")) is True
        assert gen._should_retry(ValueError("invalid")) is False
        assert gen._should_retry(RuntimeError("api")) is True

# ── 测试响应解析 ──

class TestResponseParsing:
    """响应解析测试"""

    def test_parse_json_code_block(self: "TestResponseParsing") -> None:
        """JSON 代码块解析"""
        gen = PromptGenerator(MockGeminiClient())
        raw = (
            '```json\n{"video_prompt": "test prompt", '
            '"style_tags": []}\n```'
        )
        result = gen._try_parse_json(raw)
        assert result == {
            "video_prompt": "test prompt",
            "style_tags": [],
        }

    def test_parse_json_plain(self: "TestResponseParsing") -> None:
        """纯 JSON 解析"""
        gen = PromptGenerator(MockGeminiClient())
        raw = '{"video_prompt": "plain json", "scene_description": "test"}'
        result = gen._try_parse_json(raw)
        assert result["video_prompt"] == "plain json"

    def test_parse_json_invalid(self: "TestResponseParsing") -> None:
        """无效 JSON"""
        gen = PromptGenerator(MockGeminiClient())
        result = gen._try_parse_json("not json at all")
        assert result is None

    def test_json_to_video_prompt(self: "TestResponseParsing") -> None:
        """JSON 转 VideoPrompt"""
        gen = PromptGenerator(MockGeminiClient())
        data = {
            "video_prompt": "A cinematic shot of a luxury watch",
            "scene_description": "Luxury showcase",
            "style_tags": ["cinematic", "luxury"],
            "subject": "luxury watch",
            "negative_prompt": "blurry",
        }
        vp = gen._json_to_video_prompt(data)
        assert vp.prompt == "A cinematic shot of a luxury watch"
        assert vp.scene_description == "Luxury showcase"
        assert vp.style_tags == ["cinematic", "luxury"]
        assert vp.negative_prompt == "blurry"

    def test_try_parse_text(self: "TestResponseParsing") -> None:
        """文本提取"""
        gen = PromptGenerator(MockGeminiClient())
        raw = "The video prompt: A cinematic shot of a product with camera tracking and professional lighting setup."
        result = gen._try_parse_text(raw)
        assert len(result) > 0

    def test_try_parse_text_with_markdown(self: "TestResponseParsing") -> None:
        """清理 Markdown 后提取"""
        gen = PromptGenerator(MockGeminiClient())
        raw = (
            "**Video Prompt:**\n\n"
            "A cinematic tracking shot of a luxury golden wristwatch "
            "with dramatic lighting and close-up camera angles."
        )
        result = gen._try_parse_text(raw)
        assert "cinematic" in result.lower()

    def test_parse_response_json(self: "TestResponseParsing") -> None:
        """完整解析流程 - JSON"""
        gen = PromptGenerator(MockGeminiClient())
        raw = '{"video_prompt": "A cinematic luxury watch showcase with tracking shot and dramatic lighting"}'
        vp = gen._parse_response(raw)
        assert "luxury watch" in vp.prompt

    def test_parse_response_text_fallback(self: "TestResponseParsing") -> None:
        """完整解析流程 - 文本降级"""
        gen = PromptGenerator(MockGeminiClient())
        raw = (
            "Here is the video prompt:\n\n"
            "A cinematic tracking shot of a luxury golden wristwatch "
            "with dramatic lighting, close-up camera angles, "
            "elegant style, professional photography."
        )
        vp = gen._parse_response(raw)
        assert len(vp.prompt) > 50

    def test_parse_response_empty(self: "TestResponseParsing") -> None:
        """空响应"""
        gen = PromptGenerator(MockGeminiClient())
        vp = gen._parse_response("")
        assert vp.prompt == ""

# ── 测试校验 ──

class TestValidation:
    """校验测试"""

    def test_validate_empty(self: "TestValidation", fast_config: PromptGeneratorConfig) -> None:
        """空提示词"""
        gen = PromptGenerator(MockGeminiClient(), config=fast_config)
        result = gen._validate_prompt("")
        assert result.is_valid is False
        assert "提示词为空" in result.errors

    def test_validate_too_short(self: "TestValidation", fast_config: PromptGeneratorConfig) -> None:
        """提示词过短"""
        gen = PromptGenerator(MockGeminiClient(), config=fast_config)
        result = gen._validate_prompt("short")
        assert result.is_valid is False
        assert any("过短" in e for e in result.errors)

    def test_validate_valid_prompt(self: "TestValidation", fast_config: PromptGeneratorConfig) -> None:
        """有效提示词"""
        gen = PromptGenerator(MockGeminiClient(), config=fast_config)
        prompt = (
            "A cinematic tracking shot of a luxury golden wristwatch, "
            "elegant style, professional lighting, close-up camera, "
            "dramatic atmosphere, slow rotating motion, bokeh background"
        )
        result = gen._validate_prompt(prompt)
        assert result.is_valid is True
        assert result.has_subject is True
        assert result.keyword_count >= 10

    def test_validate_no_subject(self: "TestValidation", fast_config: PromptGeneratorConfig) -> None:
        """缺少主体"""
        gen = PromptGenerator(MockGeminiClient(), config=fast_config)
        # 刻意避免所有 subject_keywords 中的词
        prompt = (
            "cinematic style with beautiful lighting and camera tracking, "
            "elegant atmosphere, professional photography, dramatic mood"
        )
        result = gen._validate_prompt(prompt)
        # 这个提示词应该缺少明确的主体
        assert result.has_subject is False
        assert result.is_valid is False

    def test_validate_warnings(self: "TestValidation", fast_config: PromptGeneratorConfig) -> None:
        """校验警告"""
        gen = PromptGenerator(MockGeminiClient(), config=fast_config)
        # 有效但较短的提示词
        prompt = (
            "A product showcase with cinematic lighting and camera angles, "
            "professional style, elegant atmosphere, beautiful scene setup"
        )
        result = gen._validate_prompt(prompt)
        assert result.is_valid is True  # 有 product 主体
        assert result.has_subject is True

# ── 测试完整生成流程 ──

class TestGenerateFlow:
    """完整生成流程测试"""

    @pytest.mark.asyncio
    async def test_generate_json_success(self: "TestGenerateFlow", tmp_image, fast_config: PromptGeneratorConfig) -> None:
        """TC-01: JSON 格式成功生成"""
        json_response = '''```json
{
"video_prompt": "A luxurious golden wristwatch with cinematic tracking shot, elegant style, professional lighting, close-up camera angles, dramatic atmosphere, slow rotating motion, bokeh background, luxury product showcase",
"scene_description": "高端手表展示",
"style_tags": ["cinematic", "luxury"]
}
```'''
        mock_client = MockGeminiClient(response_text=json_response)
        mock_gm = MockGemManager()
        mock_gm.gems["gem1"] = GemInfo(id="gem1", name="Gem1")

        generator = PromptGenerator(mock_client, mock_gm, fast_config)
        result = await generator.generate(
            gem_id="gem1",
            image_path=tmp_image,
            extra_requirements="高端手表展示视频",
        )

        assert result.is_valid is True
        assert len(result.prompt) > 50
        assert result.gem_used == "gem1"
        assert result.retry_count == 0

    @pytest.mark.asyncio
    async def test_generate_text_success(self: "TestGenerateFlow", tmp_image, fast_config: PromptGeneratorConfig) -> None:
        """TC-02: 文本格式成功生成"""
        text_response = (
            "The video prompt: A cinematic tracking shot of a luxury "
            "golden wristwatch with dramatic lighting, close-up camera "
            "angles, elegant style, professional photography, "
            "slow motion rotation, beautiful bokeh background."
        )
        mock_client = MockGeminiClient(response_text=text_response)

        generator = PromptGenerator(mock_client, config=fast_config)
        result = await generator.generate(
            gem_id="gem1",
            image_path=tmp_image,
            extra_requirements="手表视频",
        )

        assert len(result.prompt) > 50

    @pytest.mark.asyncio
    async def test_generate_retry_success(self: "TestGenerateFlow", tmp_image: ImageInput) -> None:
        """TC-03: API 重试后成功"""
        mock_client = MockGeminiClient(
            response_text='{"video_prompt": "cinematic product shot of a luxury watch with tracking camera, professional lighting, elegant style, dramatic atmosphere, close-up angles, slow motion"}',
            fail_count=2,
            exception_type=TimeoutError,
        )
        config = PromptGeneratorConfig(
            max_retries=3, retry_delay=0.01, prompt_timeout=5
        )
        generator = PromptGenerator(mock_client, config=config)

        result = await generator.generate(
            gem_id="gem1",
            image_path=tmp_image,
            extra_requirements="测试",
        )

        assert mock_client.call_count == 3
        assert result.is_valid is True
        assert result.retry_count == 2

    @pytest.mark.asyncio
    async def test_generate_all_retries_failed(self: "TestGenerateFlow", tmp_image: ImageInput) -> None:
        """TC-04: 全部重试失败"""
        mock_client = MockGeminiClient(
            fail_count=5,
            exception_type=ConnectionError,
        )
        config = PromptGeneratorConfig(
            max_retries=3, retry_delay=0.01, prompt_timeout=5
        )
        generator = PromptGenerator(mock_client, config=config)

        with pytest.raises(PromptGenerationError):
            await generator.generate(
                gem_id="gem1",
                image_path=tmp_image,
                extra_requirements="测试",
            )
        assert mock_client.call_count == 3

    @pytest.mark.asyncio
    async def test_generate_non_retryable_error(self: "TestGenerateFlow", tmp_image: ImageInput) -> None:
        """TC-05: 不可重试异常"""
        mock_client = MockGeminiClient(
            fail_count=1,
            exception_type=ValueError,
        )
        config = PromptGeneratorConfig(
            max_retries=3, retry_delay=0.01, prompt_timeout=5
        )
        generator = PromptGenerator(mock_client, config=config)

        with pytest.raises(PromptGenerationError):
            await generator.generate(
                gem_id="gem1",
                image_path=tmp_image,
                extra_requirements="测试",
            )
        assert mock_client.call_count == 1

    @pytest.mark.asyncio
    async def test_validation_retry(self: "TestGenerateFlow", tmp_image: ImageInput) -> None:
        """TC-06: 校验失败重试"""

        class BadThenGoodClient:
            def __init__(self: "BadThenGoodClient") -> None:
                self.call_count = 0

            async def generate(self: "BadThenGoodClient", **kwargs: Any):
                self.call_count += 1
                if self.call_count == 1:
                    return "short"  # 太短，校验失败
                return '{"video_prompt": "Beautiful cinematic product showcase of a luxury golden wristwatch with dramatic lighting, elegant style, professional camera tracking shot, close-up angles, slow rotating motion, beautiful bokeh background"}'

        client = BadThenGoodClient()
        config = PromptGeneratorConfig(
            validation_retry_max=2,
            retry_delay=0.01,
            prompt_timeout=5,
        )
        generator = PromptGenerator(client, config=config)

        result = await generator.generate(
            gem_id="gem1",
            image_path=tmp_image,
            extra_requirements="测试",
        )

        assert client.call_count == 2
        assert result.is_valid is True
        assert result.retry_count == 1

    @pytest.mark.asyncio
    async def test_generate_with_style_hint(self: "TestGenerateFlow", tmp_image, fast_config: PromptGeneratorConfig) -> None:
        """TC-12: 风格提示注入"""
        mock_client = MockGeminiClient(
            response_text='{"video_prompt": "A cinematic dark style product shot of a luxury watch with dramatic lighting and professional camera tracking"}'
        )
        generator = PromptGenerator(mock_client, config=fast_config)

        result = await generator.generate(
            gem_id="gem1",
            image_path=tmp_image,
            extra_requirements="手表视频",
            style_hint="cinematic, dark",
        )

        assert result.is_valid is True
        # 检查用户消息中是否包含风格提示
        assert "cinematic" in mock_client.last_call_args["prompt"]

# ── 测试异常 ──

class TestExceptions:
    """异常测试"""

    def test_prompt_generation_error(self: "TestExceptions") -> None:
        """PromptGenerationError"""
        with pytest.raises(PromptGenerationError):
            raise PromptGenerationError("test error")

    def test_prompt_validation_error(self: "TestExceptions") -> None:
        """PromptValidationError"""
        with pytest.raises(PromptValidationError):
            raise PromptValidationError("validation failed")

    def test_gem_not_found_error(self: "TestExceptions") -> None:
        """GemNotFoundError"""
        with pytest.raises(GemNotFoundError):
            raise GemNotFoundError("gem not found")

    def test_error_inheritance(self: "TestExceptions") -> None:
        """异常继承关系"""
        assert issubclass(PromptGenerationError, GeminiModuleError)
        assert issubclass(PromptValidationError, GeminiModuleError)
        assert issubclass(GemNotFoundError, GeminiModuleError)

# ── 运行测试 ──




if __name__ == "__main__":
    print("prompt_generator.py - 使用 pytest 运行: pytest prompt_generator.py -v")
