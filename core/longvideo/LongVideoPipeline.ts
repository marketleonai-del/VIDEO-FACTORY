/**
 * LongVideoPipeline.ts — 长视频端到端：规划 → 逐段生成(续帧+再锚定) → 拼接。
 * 复用 ModelRegistry（选型+回退）与 LongFormAssembler（归一化拼接）。模型无关。
 */
import { ModelRegistry } from "../ModelRegistry";
import { GenerateParams, GenerateResult, BudgetTier } from "../types";
import { logger } from "../runtime";
import { LongVideoPlanner, LongVideoPlan, LongPlanOptions } from "./LongVideoPlanner";
import { LongFormAssembler, LongFormOptions } from "./LongFormAssembler";

export interface LongVideoOptions extends LongPlanOptions {
  aspectRatio?: string;
  budgetTier?: BudgetTier;
  /** 再锚定用的产品/主体参考图（@image1） */
  anchorImageUrl?: string;
  language?: string;
  outputPath?: string;
  assemble?: LongFormOptions;
}
export interface LongVideoResult {
  plan: LongVideoPlan;
  clips: Array<{ index: number; source: "ai" | "user"; url?: string }>;
  assembly: ReturnType<LongFormAssembler["render"]>;
}

export class LongVideoPipeline {
  private planner = new LongVideoPlanner();
  private assembler = new LongFormAssembler();
  constructor(private models: ModelRegistry) {}

  async generate(targetSec: number, opts: LongVideoOptions = {}): Promise<LongVideoResult> {
    const aspectRatio = opts.aspectRatio ?? "9:16";
    // 选型：短段、可图生视频（续帧需要）
    const candidates = this.models.select({
      mode: "image2video",
      aspectRatio,
      durationSec: Math.min(opts.maxSegSec ?? 10, 10),
      language: opts.language,
      budgetTier: opts.budgetTier ?? "standard",
      preferLocal: opts.budgetTier === "minimal",
    });
    const maxSeg = Math.min(candidates[0]?.capabilities.maxDuration ?? 10, opts.maxSegSec ?? 10);
    const plan = this.planner.plan(targetSec, { ...opts, maxSegSec: maxSeg });

    const clips: LongVideoResult["clips"] = [];
    let prevUrl: string | undefined;
    for (const seg of plan.segments) {
      if (seg.source === "user") {
        clips.push({ index: seg.index, source: "user", url: seg.userClipUrl });
        prevUrl = seg.userClipUrl;
        continue;
      }
      // AI 段：续帧（image2video，参考上一段末帧）+ 必要时再锚定产品图
      const refs: GenerateParams["referenceImages"] = [];
      if (seg.continueFromIndex !== undefined && prevUrl) refs.push({ role: "subject", url: prevUrl });
      if (seg.reanchor && opts.anchorImageUrl) refs.push({ role: "style", url: opts.anchorImageUrl });
      const params: GenerateParams = {
        mode: refs.length ? "image2video" : "text2video",
        prompt: {
          description: [seg.prompt, seg.subjectAnchor ? `主体恒定：${seg.subjectAnchor}` : ""].filter(Boolean).join(" | "),
          suffix: "连续运镜, 与上一段同风格同人物, no text",
          negative: "scene cut, identity change, morphing, text",
        },
        durationSec: seg.durationSec,
        resolution: opts.budgetTier === "premium" ? "1080p" : "720p",
        aspectRatio,
        referenceImages: refs.length ? refs : undefined,
        wantNativeAudio: false,
        language: opts.language,
      };
      const res = await this.generateWithFallback(candidates.map((m) => m.modelId), params);
      // 真实实现：用 ffmpeg 抽取本段末帧作为下一段 subject 锚；demo 下用 videoUrl 占位
      prevUrl = res.videoUrl;
      clips.push({ index: seg.index, source: "ai", url: res.videoUrl });
    }

    const urls = clips.map((c) => c.url ?? "");
    const durations = plan.segments.map((s) => s.durationSec);
    const assembly = this.assembler.render(urls, opts.outputPath ?? "./long-output.mp4", { transition: plan.transition, ...opts.assemble }, durations);
    return { plan, clips, assembly };
  }

  private async generateWithFallback(ids: string[], params: GenerateParams): Promise<GenerateResult> {
    let lastErr: Error | undefined;
    for (const id of ids) {
      try {
        return await this.models.get(id).generate(params);
      } catch (e) {
        lastErr = e as Error;
        logger.warn(`长视频段 ${id} 失败，回退`, { err: lastErr.message });
      }
    }
    throw lastErr ?? new Error("无可用视频模型");
  }
}
