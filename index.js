/**
 * =============================================================================
 * VIDEO-FACTORY v3.0 — 主入口文件
 * =============================================================================
 *
 * 一句话定位：输入「产品（图/名）或一句话」→ 自动分析卖点 → 写带货脚本
 * → 拆故事板分镜 → 逐镜出 AI 视频 → ffmpeg 拼接成片 → 本地配音 → 矩阵多条
 * 300-Agent 蜂群协作，越用越好（SkillOpt 自进化）。
 *
 * 本文件导出 VIDEO-FACTORY 的所有核心模块，供外部编程调用。
 *
 * 使用方式：
 * ```javascript
 * const { HookEngine, HybridCutEngine, EvolutionEngine, AgentCoordinator } = require('./index');
 * ```
 *
 * 模块分类：
 *   - 创意引擎：HookEngine, MatrixEngine, PromptTemplateEngine, PlatformAdapter
 *   - 视频处理：HybridCutEngine, FFmpegUtils, VideoDownloader
 *   - 音频处理：VoiceLockManager, TTSAdapter, LocalTTSAdapter, ElevenLabsAdapter
 *   - 质检系统：QualityGate
 *   - 自进化：SkillParams, EvolutionEngine, QualitySignals, Telemetry, VersionChecker
 *   - 蜂群：AgentProtocol, DataLoopback
 *   - HQ总部：AgentCoordinator
 *
 * 技术栈：纯 Node.js（零运行时依赖），仅依赖 Node 内置模块 + ffmpeg 系统二进制
 * 许可证：MIT
 *
 * @author VIDEO-FACTORY Team
 * @version 3.0.0
 * @license MIT
 * =============================================================================
 */

'use strict';

// ============================================================================
// 创意引擎层（Creative Layer）
// ============================================================================

/**
 * HookEngine —— 钩子角度生成引擎
 *
 * 6大角度族(A-F) + 10大钩子族(80条模板) → 最优带货角度卡
 * 核心能力：产品洞察、角度扫描、钩子匹配、WinScore评分、差异化筛选
 */
const HookEngine = require('./core/creative/hook-engine');

/**
 * MatrixEngine —— 矩阵内容引擎
 *
 * 1条精品母版 → N条差异化变体（4大轮换轴：钩子/人设/场景/节奏）
 * 核心能力：赢家拆解、变体策略、跨账号差异化、防限流自检、平台适配
 */
const MatrixEngine = require('./core/creative/matrix-engine');

/**
 * PromptTemplateEngine —— 提示词模板引擎
 *
 * 管理所有提示词模板：产品分析、带货脚本、故事板分镜、矩阵差异化、质检合规
 * 支持模板变量填充和多语言切换
 */
const PromptTemplateEngine = require('./core/creative/prompt-templates');

/**
 * PlatformAdapter —— 平台适配器
 *
 * 适配不同平台的创意约束：抖音/快手/小红书/视频号/TikTok
 * 提供画幅、节奏、CTA、禁忌等平台化参数
 */
const PlatformAdapter = require('./core/creative/platform-adapter');

// ============================================================================
// 视频处理层（Video Processing Layer）
// ============================================================================

/**
 * HybridCutEngine —— AI + 真实素材混剪引擎
 *
 * 核心降本模块。AI只做0-3s钩子，3s后有序拼接真实素材。
 * 包含素材入库、逐镜决策、AI补拍提示词、一致性锚定、人感护栏、ffmpeg混剪执行。
 */
const HybridCutEngine = require('./core/video/hybrid-cut');

/**
 * FFmpegUtils —— ffmpeg 工具封装
 *
 * 视频归一化（720x1280/24fps）、concat拼接、竖屏裁切、音频提取、混流
 * 纯 Node.js 调用系统 ffmpeg 二进制，零额外依赖
 */
const FFmpegUtils = require('./core/video/ffmpeg-utils');

/**
 * VideoDownloader —— 视频下载工具
 *
 * 支持 http/https 视频下载、断点续传、批量下载
 */
const VideoDownloader = require('./core/video/video-downloader');

// ============================================================================
// 音频处理层（Audio Processing Layer）
// ============================================================================

/**
 * VoiceLockManager —— 声纹锁管理器
 *
 * 全片锁定单一声纹 @voice1：AI画面静音 → 统一配音轨 → 贴到拼好画面上
 * 解决"AI钩子+真素材拼接"的音色不一致问题
 */
const { VoiceLockManager, TTSAdapter, LocalTTSAdapter, ElevenLabsAdapter } = require('./core/audio/voice-lock');

// ============================================================================
// 质检系统层（Quality Assurance Layer）
// ============================================================================

/**
 * QualityGate —— 120 门质量检测
 *
 * 6大维度 120 分制：<100 自动重修（≤3轮）
 *   - 内容(30): 角度真不同/钩子前3秒成立/卖点可视化
 *   - 真实(25): 信任点用真素材/无恐怖谷/非完美细节保留
 *   - 多样(20): 段内≤15%/跨账号≤20%/维度覆盖
 *   - 合规(20): AI标识/分区清单/零禁用词/功效合规
 *   - 闭环(15): 每条带WinScore/预留actual回填
 *   - 技术(10): @image锚定/@voice1声纹锁/模型无关提示词
 */
const QualityGate = require('./core/quality/QualityGate');

// ============================================================================
// 自进化层（Evolution Layer）
// ============================================================================

/**
 * SkillParams —— 可训练参数
 *
 * 角度权重/模型偏置/钩子模板权重/提示词后缀/质检门阈值
 * 版本化管理，支持序列化/反序列化/快照/差异比较
 */
const SkillParams = require('./core/evolution/SkillParams');

/**
 * EvolutionEngine —— SkillOpt 进化引擎
 *
 * Bandit + ε-greedy 参数自优化：
 *   propose(候选) → validate(验证门控) → applyLearningRate(限幅采纳) → commit
 * 防跑偏机制：严格胜出(no ties) + 学习率 + 负样本缓冲 + 一键回滚
 */
const EvolutionEngine = require('./core/evolution/EvolutionEngine');

/**
 * QualitySignals —— 隐式质量信号计算
 *
 * 从生成结果中提取隐式信号：成功率/重试率/质检分/拼接成功/耗时
 * 合成 0-1 的 ImplicitQuality 分数，驱动进化引擎
 */
const QualitySignals = require('./core/evolution/QualitySignals');

/**
 * Telemetry —— 匿名遥测
 *
 * Opt-in（默认关闭），白名单字段，无 PII
 * 批量 POST 隐式信号到 HQ，支持限频/离群剔除
 */
const Telemetry = require('./core/evolution/Telemetry');

/**
 * VersionChecker —— 版本检查器
 *
 * 定期拉取 HQ 冠军参数，本地验证门控后才采纳
 * 即使 HQ 发了坏参数，本地门控会拦下；已采纳后变坏可回滚
 */
const VersionChecker = require('./core/evolution/VersionChecker');

// ============================================================================
// 蜂群通信层（Swarm Communication Layer）
// ============================================================================

/**
 * AgentProtocol —— Agent 通信协议
 *
 * 定义 Agent 与 HQ 之间的消息格式：注册/心跳/任务分配/结果上报/参数同步
 * 支持 JSON over HTTP 和二进制 over WebSocket
 */
const AgentProtocol = require('./core/swarm/agent-protocol');

/**
 * DataLoopback —— 数据回流
 *
 * 任务结果 → 隐式信号提取 → 遥测上报 → HQ聚合 → 参数更新 → Agent拉取
 * 形成"生成-反馈-进化"闭环
 */
const DataLoopback = require('./core/swarm/data-loopback');

// ============================================================================
// HQ 总部层（HQ Coordinator Layer）
// ============================================================================

/**
 * AgentCoordinator —— 300-Agent 蜂群协调器
 *
 * 核心能力：
 *   - Agent注册与心跳管理（支持4种角色：generator/evolver/qa/creative）
 *   - 任务队列与负载均衡分配
 *   - 批量任务分解（200条视频自动拆分为差异化子任务）
 *   - 结果收集与隐式信号提取
 *   - 蜂群健康检查（每15秒）与故障恢复
 *   - 参数同步与版本管理
 *
 * 角色配额：generator=200, evolver=50, qa=30, creative=20（总计300）
 */
const AgentCoordinator = require('./hq/agent-coordinator');

// ============================================================================
// 统一导出
// ============================================================================

module.exports = {
  // ── 创意引擎 ──────────────────────────────────────────
  HookEngine,
  MatrixEngine,
  PromptTemplateEngine,
  PlatformAdapter,

  // ── 视频处理 ──────────────────────────────────────────
  HybridCutEngine,
  FFmpegUtils,
  VideoDownloader,

  // ── 音频处理 ──────────────────────────────────────────
  VoiceLockManager,
  TTSAdapter,
  LocalTTSAdapter,
  ElevenLabsAdapter,

  // ── 质检系统 ──────────────────────────────────────────
  QualityGate,

  // ── 自进化 ────────────────────────────────────────────
  SkillParams,
  EvolutionEngine,
  QualitySignals,
  Telemetry,
  VersionChecker,

  // ── 蜂群通信 ──────────────────────────────────────────
  AgentProtocol,
  DataLoopback,

  // ── HQ 总部 ───────────────────────────────────────────
  AgentCoordinator,

  // ── 元信息 ────────────────────────────────────────────
  META: {
    name: 'VIDEO-FACTORY',
    version: '3.0.0',
    description: '300-Agent蜂群协作的AI视频生产工厂，越用越好（SkillOpt自进化）',
    license: 'MIT',
    homepage: 'https://github.com/marketleonai-del/VIDEO-FACTORY',
    nodeRequirement: '>=18.0.0',
    dependencies: '零运行时依赖（仅 Node 内置 + ffmpeg 系统二进制）'
  }
};
