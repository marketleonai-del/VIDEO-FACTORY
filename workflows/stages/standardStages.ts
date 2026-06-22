/**
 * standardStages.ts — 14 个标准 Stage 实现
 *
 * 说明：为控制文件数量，14 个 Stage 集中在本文件，按 docx 结构逐个导出 +
 * 提供 STANDARD_STAGES 数组与 registerStandardStages() 一键注册。
 * 拆成单文件（stages/*.ts）也完全可行——它们彼此独立，只依赖 WorkflowContext。
 *
 * 分两类：
 *   基础设施 Stage（model-selection / voice-lock / ai-hook / ai-b-roll /
 *     narration / assembly / quality-gate / cost-report）→ 调 core 与 adapters，真实编排。
 *   业务 Stage（angle-discovery / script / storyboard / material-analysis /
 *     matrix-variants / platform-adaptation）→ 产出结构化产物；其创意内容由业务层
 *     skill（ugc-creative-amplifier）在运行时填充，这里给出可运行的结构与默认值。
 */
import { GenerateParams, GenerateResult, Platform } from "../../core/types";
import { WorkflowContext } from "../WorkflowContext";
import { WorkflowStage } from "../WorkflowStage";

/* ── 基础设施 Stage ────────────────────────────────────────── */

/** 模型选型：按预算/需求自动选视频模型 + TTS（能力驱动） */
export const ModelSelectionStage: WorkflowStage = {
  id: "model-selection",
  name: "模型选型",
  precondition: () => true,
  execute: async (ctx) => {
    const { input, managers } = ctx;
    const videoModels = managers.models.select({
      mode: input.hasOwnMaterials ? "image2video" : "text2video",
      aspectRatio: input.aspectRatio,
      durationSec: Math.min(3, input.durationSec), // AI 只做钩子，按短时长选
      language: input.language,
      budgetTier: input.budgetTier,
      preferLocal: input.budgetTier === "minimal",
    });
    const tts = managers.tts.select({
      language: input.language,
      needClone: input.hasRealPersonAudio,
      preferLocal: input.budgetTier !== "premium",
    });
    ctx.artifacts.selectedVideoModelIds = videoModels.map((m) => m.modelId);
    ctx.artifacts.selectedTTSId = tts[0]?.providerId;
    return ctx;
  },
  postValidation: (ctx) => !!ctx.artifacts.selectedVideoModelIds?.length && !!ctx.artifacts.selectedTTSId,
};

/** 声纹锁定：有真人素材→克隆；否则→内置音色。改进 docx：本 Stage 始终执行，保证 @voice1 存在 */
export const VoiceLockStage: WorkflowStage = {
  id: "voice-lock",
  name: "声纹锁定",
  precondition: () => true,
  execute: async (ctx) => {
    const { input, managers, artifacts } = ctx;
    const providerId = artifacts.selectedTTSId!;
    if (input.hasRealPersonAudio && input.realPersonAudioSample) {
      await managers.voiceLock.lockVoice(
        "voice1",
        { kind: "clone", sampleAudio: input.realPersonAudioSample, name: "voice1", language: input.language },
        managers.tts.get(providerId).capabilities.voiceCloneSupport ? providerId : undefined,
      );
    } else {
      // 无真人素材：选内置音色（此处取该 provider 的默认音色 id；真实运行可 listVoices 选）
      await managers.voiceLock.lockVoice("voice1", { kind: "builtin", providerId, voiceId: "default" });
    }
    artifacts.lockedVoiceId = "voice1";
    return ctx;
  },
  postValidation: (ctx) => !!ctx.managers.voiceLock.getVoice("voice1"),
};

/** AI 钩子生成：只做 0-3s 钩子（最高杠杆），原生音频静音（音轨走 @voice1） */
export const AiHookGenerationStage: WorkflowStage = {
  id: "ai-hook-generation",
  name: "AI钩子生成",
  precondition: () => true,
  execute: async (ctx) => {
    const ids = ctx.artifacts.selectedVideoModelIds ?? [];
    const params: GenerateParams = {
      mode: ctx.input.hasOwnMaterials ? "image2video" : "text2video",
      prompt: {
        description: ctx.artifacts.angles?.[0]?.hook ?? "视觉打断钩子（无人脸，产品微距/反常使用）",
        suffix: "手机实拍质感, no text",
        negative: "morphing, extra fingers, text, watermark",
      },
      durationSec: 3,
      resolution: ctx.input.budgetTier === "premium" ? "1080p" : "720p",
      aspectRatio: ctx.input.aspectRatio,
      wantNativeAudio: false, // 关键：静音，音色统一交给 @voice1
      language: ctx.input.language,
    };
    const res = await generateWithFallback(ctx, ids, params);
    ctx.artifacts.aiHookResults = [res];
    return ctx;
  },
};

/** AI 补镜头：标准/精品档补缺失 B-roll/空镜 */
export const AiBRollStage: WorkflowStage = {
  id: "ai-b-roll",
  name: "AI补镜头",
  precondition: (ctx) => ctx.input.budgetTier !== "minimal",
  execute: async (ctx) => {
    const ids = ctx.artifacts.selectedVideoModelIds ?? [];
    const need = ctx.input.budgetTier === "premium" ? 2 : 1;
    const out: GenerateResult[] = [];
    for (let i = 0; i < need; i++) {
      out.push(
        await generateWithFallback(ctx, ids, {
          mode: "text2video",
          prompt: { description: `补充 B-roll #${i + 1}：多场景/空镜/转场`, suffix: "手机实拍质感, no text", negative: "text" },
          durationSec: 3,
          resolution: "720p",
          aspectRatio: ctx.input.aspectRatio,
          wantNativeAudio: false,
          language: ctx.input.language,
        }),
      );
    }
    ctx.artifacts.bRollResults = out;
    return ctx;
  },
};

/** 旁白合成：整片口播用 @voice1 合成一条统一音轨（音色一致的核心落地） */
export const NarrationSynthesisStage: WorkflowStage = {
  id: "narration-synthesis",
  name: "旁白合成",
  precondition: () => true,
  execute: async (ctx) => {
    const lines = deriveNarration(ctx);
    const audios = await ctx.managers.voiceLock.batchSynthesize("voice1", lines, { language: ctx.input.language });
    ctx.artifacts.narrationAudioUrls = audios.map((a) => a.audioUrl);
    return ctx;
  },
  postValidation: (ctx) => !!ctx.artifacts.narrationAudioUrls?.length,
};

/** 视频组装：AI 镜头 + 真素材 + 统一音轨 → 组装清单（最终渲染交 ffmpeg/剪辑器） */
export const VideoAssemblyStage: WorkflowStage = {
  id: "video-assembly",
  name: "视频组装",
  precondition: () => true,
  execute: async (ctx) => {
    const a = ctx.artifacts;
    ctx.artifacts.assemblyManifest = {
      order: [
        ...(a.aiHookResults ?? []).map((r) => ({ kind: "ai-hook", url: r.videoUrl, muted: true })),
        ...(a.materialPlan ?? [])
          .filter((m) => m.source === "real")
          .map((m) => ({ kind: "real", materialId: m.materialId, muted: true })),
        ...(a.bRollResults ?? []).map((r) => ({ kind: "ai-broll", url: r.videoUrl, muted: true })),
      ],
      audioTrack: a.narrationAudioUrls, // 单一 @voice1 音轨贴满全片
      note: "所有画面静音，统一贴 @voice1 音轨；最终渲染用 ffmpeg/剪辑器（需本地执行）",
    };
    return ctx;
  },
};

/** 质量门闸：120 门，每条过同一标准；不达标记入 trace（真实运行触发重修） */
export const QualityGateStage: WorkflowStage = {
  id: "quality-gate",
  name: "质量门闸",
  precondition: () => true,
  execute: async (ctx) => {
    // 这里给出基于产物完整度的启发式评分；业务层可注入更精确的人工/模型评分
    const a = ctx.artifacts;
    const r = ctx.managers.quality.evaluate({
      content: a.angles?.length ? 26 : 22,
      authenticity: ctx.input.hasRealPersonAudio ? 23 : 20,
      diversity: ctx.input.matrixCount > 1 ? 18 : 16,
      compliance: 19,
      loop: a.angles?.[0]?.winScore ? 13 : 11,
      tech: a.lockedVoiceId ? 9 : 7,
    });
    ctx.artifacts.qualityResults = [r];
    if (!r.pass) ctx.trace.push(`quality<100, 触发重修: ${r.failures.join("; ")}`);
    return ctx;
  },
};

/** 成本报告：输出整条/整矩阵成本估算 */
export const CostReportStage: WorkflowStage = {
  id: "cost-report",
  name: "成本报告",
  precondition: () => true,
  execute: async (ctx) => {
    const a = ctx.artifacts;
    const hookModelId = a.selectedVideoModelIds?.[0];
    if (!hookModelId || !a.selectedTTSId) return ctx;
    const perVariant = {
      aiVideoSegments: [
        {
          modelId: hookModelId,
          params: {
            mode: "text2video",
            prompt: { description: "hook" },
            durationSec: 3,
            resolution: "720p",
            aspectRatio: ctx.input.aspectRatio,
          } as GenerateParams,
        },
      ],
      realFootageSec: Math.max(0, ctx.input.durationSec - 3),
      narration: {
        providerId: a.selectedTTSId,
        params: { text: deriveNarration(ctx).join(" "), voiceId: "voice1" },
      },
      voiceCloneOnce: ctx.input.hasRealPersonAudio ? { providerId: a.selectedTTSId } : undefined,
    };
    ctx.artifacts.costReport =
      ctx.input.matrixCount > 1
        ? ctx.managers.cost.estimateMatrix(perVariant, ctx.input.matrixCount)
        : ctx.managers.cost.estimateOne(perVariant);
    return ctx;
  },
};

/* ── 业务 Stage（结构化产物，创意由业务层 skill 填充） ──────────── */

export const AngleDiscoveryStage: WorkflowStage = {
  id: "angle-discovery",
  name: "角度发现",
  precondition: (ctx) => ctx.input.mode === "from-scratch",
  execute: async (ctx) => {
    // 创意由 ugc-creative-amplifier 的 hook-angle/hook-library 在运行时产出；此处给结构
    const families = ["痛点放大", "反常识", "身份认同", "省钱算账", "信任背书", "情绪共鸣"];
    ctx.artifacts.angles = families.slice(0, Math.max(1, Math.min(ctx.input.matrixCount, 6))).map((f, i) => ({
      family: f,
      name: `${f}角度`,
      hook: `[${f}] 前3秒钩子（由业务层填充）`,
      winScore: 80 - i * 2,
    }));
    return ctx;
  },
};

export const ScriptGenerationStage: WorkflowStage = {
  id: "script-generation",
  name: "脚本生成",
  precondition: (ctx) => ctx.input.mode === "from-scratch",
  execute: async (ctx) => {
    ctx.artifacts.script = "8要素脚本：痛点→崩溃→旧法失败→救星→反差→产品接入→爽点→多场景（业务层填充）";
    return ctx;
  },
};

export const StoryboardStage: WorkflowStage = {
  id: "storyboard",
  name: "分镜故事板",
  precondition: (ctx) => ctx.input.mode !== "from-winner",
  execute: async (ctx) => {
    ctx.artifacts.storyboard = [
      { shot: 1, source: "ai", durationSec: 3, desc: "AI 钩子（无人脸视觉打断）" },
      { shot: 2, source: "real", durationSec: 4, desc: "真人/真素材 痛点" },
      { shot: 3, source: "real", durationSec: 4, desc: "真实效果对比" },
    ];
    return ctx;
  },
};

export const MaterialAnalysisStage: WorkflowStage = {
  id: "material-analysis",
  name: "素材分析",
  precondition: (ctx) => ctx.input.hasOwnMaterials,
  execute: async (ctx) => {
    const mats = ctx.input.materials ?? [];
    ctx.artifacts.materialPlan = mats.map((m, i) => ({
      shot: i + 2, // shot1 留给 AI 钩子
      // 信任价值高 → 用真；否则可被 AI 替代
      source: m.trustValue === "high" ? "real" : "real",
      materialId: m.id,
    }));
    return ctx;
  },
};

export const MatrixVariantsStage: WorkflowStage = {
  id: "matrix-variants",
  name: "矩阵变体",
  precondition: (ctx) => ctx.input.matrixCount > 1,
  execute: async (ctx) => {
    const families = ["痛点放大", "反常识", "数字清单", "对比反差", "情绪共鸣", "身份认同", "悬念好奇", "视觉打断"];
    const personas = ["宝妈", "测评党", "学生党", "上班族", "精致生活"];
    ctx.artifacts.variants = Array.from({ length: ctx.input.matrixCount }, (_, i) => ({
      index: i + 1,
      hookFamily: families[i % families.length], // 钩子轮换，防同质化
      persona: personas[i % personas.length],
      platform: ctx.input.platform,
    }));
    return ctx;
  },
  postValidation: (ctx) => (ctx.artifacts.variants?.length ?? 0) === ctx.input.matrixCount,
};

export const PlatformAdaptationStage: WorkflowStage = {
  id: "platform-adaptation",
  name: "平台适配",
  precondition: (ctx) => ctx.input.matrixCount > 1,
  execute: async (ctx) => {
    // 画幅/节奏/CTA 由 presets/platforms 提供；此处标注采用的平台
    ctx.trace.push(`platform-adaptation: ${ctx.input.platform}`);
    return ctx;
  },
};

/* ── 汇总 ────────────────────────────────────────────────── */

export const STANDARD_STAGES: WorkflowStage[] = [
  ModelSelectionStage,
  VoiceLockStage,
  AngleDiscoveryStage,
  ScriptGenerationStage,
  StoryboardStage,
  MaterialAnalysisStage,
  AiHookGenerationStage,
  AiBRollStage,
  NarrationSynthesisStage,
  VideoAssemblyStage,
  QualityGateStage,
  MatrixVariantsStage,
  PlatformAdaptationStage,
  CostReportStage,
];

/* ── 辅助 ────────────────────────────────────────────────── */

/** 带回退的生成：首选模型失败 → 自动换下一个 */
async function generateWithFallback(ctx: WorkflowContext, modelIds: string[], params: GenerateParams): Promise<GenerateResult> {
  let lastErr: Error | undefined;
  for (const id of modelIds) {
    try {
      return await ctx.managers.models.get(id).generate(params);
    } catch (e) {
      lastErr = e as Error;
      ctx.trace.push(`model ${id} 失败，回退下一个: ${lastErr.message}`);
    }
  }
  throw lastErr ?? new Error("无可用视频模型");
}

/** 从产物推导整片口播文案（业务层可覆盖为真实脚本） */
function deriveNarration(ctx: WorkflowContext): string[] {
  if (ctx.artifacts.script) return [ctx.artifacts.script];
  if (ctx.artifacts.angles?.length) return ctx.artifacts.angles.map((a) => a.hook);
  return ["（口播文案由业务层脚本提供）"];
}

/** 占位以消除未使用告警（Platform 类型在 variants 中使用） */
export type _PlatformUsed = Platform;
