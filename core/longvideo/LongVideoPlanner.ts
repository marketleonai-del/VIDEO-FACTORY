/**
 * LongVideoPlanner.ts — 长视频分段规划（超过单模型上限→多段+拼接）
 * 一致性策略（2026 实践）：每段"续帧"自上一段末帧（image2video，Veo/Seedance Extend 思路）；
 * 主体描述跨段恒定（subjectAnchor）防漂移；每 N 段用产品图(@image1)再锚定一次，抑制累积漂移。
 * 支持混合：用户素材片段放指定位置，AI 只生成缺口。
 */
export interface LongSegment {
  index: number;
  source: "ai" | "user";
  durationSec: number;
  /** AI 段：续上一段末帧（实现连续性） */
  continueFromIndex?: number;
  /** AI 段：跨段恒定的主体描述（防漂移） */
  subjectAnchor?: string;
  /** AI 段：是否在本段用产品图再锚定（每 N 段一次） */
  reanchor?: boolean;
  prompt?: string;
  /** 用户段：素材地址 */
  userClipUrl?: string;
}
export interface LongVideoPlan {
  targetSec: number;
  maxSegSec: number;
  transition: "hard" | "xfade";
  segments: LongSegment[];
  notes: string[];
}
export interface LongPlanOptions {
  maxSegSec?: number; // 单模型上限
  subjectAnchor?: string; // 跨段恒定主体
  reanchorEvery?: number; // 每 N 段再锚定
  transition?: "hard" | "xfade";
  /** 混合模式：用户素材片段（按出现顺序），其余由 AI 续生成 */
  userClips?: Array<{ atSec: number; durationSec: number; url: string }>;
  perSegmentPrompt?: (i: number, total: number) => string;
}

export class LongVideoPlanner {
  plan(targetSec: number, opts: LongPlanOptions = {}): LongVideoPlan {
    const maxSegSec = Math.max(1, opts.maxSegSec ?? 10);
    const reanchorEvery = opts.reanchorEvery ?? 3;
    const transition = opts.transition ?? "hard";
    const notes: string[] = [];
    const segments: LongSegment[] = [];

    // 1) 放置用户素材片段（混合模式）
    const userClips = (opts.userClips ?? []).slice().sort((a, b) => a.atSec - b.atSec);
    let cursor = 0;
    let idx = 0;
    const pushAi = (dur: number): void => {
      const seg: LongSegment = {
        index: idx,
        source: "ai",
        durationSec: dur,
        continueFromIndex: idx > 0 ? idx - 1 : undefined,
        subjectAnchor: opts.subjectAnchor,
        reanchor: idx % reanchorEvery === 0,
        prompt: opts.perSegmentPrompt ? opts.perSegmentPrompt(idx, 0) : `第${idx + 1}段（续上一段末帧，主体恒定）`,
      };
      segments.push(seg);
      idx++;
    };

    for (const uc of userClips) {
      // 用 AI 段填补 cursor→uc.atSec 的缺口（每段≤maxSegSec）
      let gap = uc.atSec - cursor;
      while (gap > 0.01) {
        const d = Math.min(maxSegSec, gap);
        pushAi(d);
        gap -= d;
      }
      segments.push({ index: idx, source: "user", durationSec: uc.durationSec, userClipUrl: uc.url });
      idx++;
      cursor = uc.atSec + uc.durationSec;
    }
    // 2) 剩余时长用 AI 段补齐
    let remaining = targetSec - cursor;
    while (remaining > 0.01) {
      const d = Math.min(maxSegSec, remaining);
      pushAi(d);
      remaining -= d;
    }

    const aiCount = segments.filter((s) => s.source === "ai").length;
    notes.push(`目标 ${targetSec}s → ${segments.length} 段（AI ${aiCount} / 用户 ${segments.length - aiCount}），单段≤${maxSegSec}s`);
    notes.push(`一致性：续帧连续 + 主体恒定 + 每${reanchorEvery}段再锚定（防累积漂移）`);
    if (transition === "xfade") notes.push("段间 xfade 交叉淡入淡出");
    return { targetSec, maxSegSec, transition, segments, notes };
  }
}
