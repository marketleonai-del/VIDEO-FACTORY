/**
 * SeedanceAdapter.ts — Seedance 2.0（即梦/字节）适配器（P0·真实实现）
 * 国内强，带原生音频，~$0.20/s。⚠️ 原生音频在混剪时必须静音（音色统一交 @voice1）。
 * 通用 REST + Bearer/Token；缺 Key → demo 模式。VERIFY：按火山/即梦开放平台核对字段。
 */
import { BaseVideoModel } from "../../core/VideoModel";
import { withRetry, logger } from "../../core/runtime";
import { GenerateParams, GenerateResult, ModelCapabilities, ModelSpecificPrompt, TaskStatus } from "../../core/types";

export interface SeedanceConfig {
  apiKey?: string;
  baseUrl?: string;
}

export class SeedanceAdapter extends BaseVideoModel {
  readonly modelId = "seedance";
  readonly modelName = "Seedance 2.0（即梦）";
  readonly capabilities: ModelCapabilities = {
    generateModes: ["text2video", "image2video"],
    maxDuration: 12,
    minDuration: 4,
    durationStep: 1,
    resolutions: ["720p", "1080p"],
    aspectRatios: ["9:16", "16:9", "1:1"],
    referenceImages: { min: 0, max: 2, supportedRoles: ["subject", "style"] },
    audioSupport: true, // ★带原生音频——混剪时静音
    audioQuality: ["standard"],
    consistencyControl: { supported: true, min: 0, max: 1, default: 0.6 },
    qualityTiers: ["standard"],
    costPerSecond: 0.2,
    supportedLanguages: ["zh", "en"],
    deploymentType: "cloud-api",
    features: ["native-audio", "multi-shot", "character-consistency"],
  };

  constructor(private cfg: SeedanceConfig = {}) {
    super();
  }

  protected async doGenerate(p: ModelSpecificPrompt, params: GenerateParams): Promise<GenerateResult> {
    if (!this.cfg.apiKey || !this.cfg.baseUrl) {
      logger.debug("Seedance demo 模式（缺 Key/baseUrl）");
      return { taskId: `seedance-demo-${Date.now()}`, state: "succeeded", videoUrl: "demo://seedance/clip.mp4", hasNativeAudio: true, modelId: this.modelId };
    }
    return withRetry(async () => {
      // VERIFY：请求体字段名按即梦/火山开放平台核对
      const resp = await fetch(this.cfg.baseUrl!, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${this.cfg.apiKey}` },
        body: JSON.stringify({
          prompt: p.prompt,
          duration: params.durationSec,
          aspect_ratio: params.aspectRatio,
          resolution: params.resolution,
          image_url: params.referenceImages?.find((r) => r.role === "subject")?.url,
          with_audio: params.wantNativeAudio ?? false, // 混剪默认 false → 静音
        }),
      });
      if (!resp.ok) throw new Error(`Seedance ${resp.status}: ${await resp.text()}`);
      const data = (await resp.json()) as { task_id?: string; id?: string; video_url?: string };
      return {
        taskId: data.task_id ?? data.id ?? "unknown",
        state: data.video_url ? "succeeded" : "queued",
        videoUrl: data.video_url,
        hasNativeAudio: params.wantNativeAudio ?? false,
        modelId: this.modelId,
        raw: data,
      };
    });
  }

  protected async doGetStatus(taskId: string): Promise<TaskStatus> {
    if (!this.cfg.apiKey || !this.cfg.baseUrl) return { taskId, state: "succeeded", videoUrl: "demo://seedance/clip.mp4" };
    const resp = await fetch(`${this.cfg.baseUrl}/${taskId}`, { headers: { Authorization: `Bearer ${this.cfg.apiKey}` } });
    const data = (await resp.json()) as { status?: string; video_url?: string; progress?: number };
    const map: Record<string, TaskStatus["state"]> = { processing: "running", running: "running", success: "succeeded", succeeded: "succeeded", failed: "failed" };
    return { taskId, state: map[data.status ?? ""] ?? "running", videoUrl: data.video_url, progress: data.progress };
  }

  async healthCheck(): Promise<boolean> {
    return !!this.cfg.apiKey && !!this.cfg.baseUrl;
  }
}
