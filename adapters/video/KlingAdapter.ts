/**
 * KlingAdapter.ts — 可灵 Kling 视频模型适配器（样板·真实实现）
 *
 * 可灵性价比高（~$0.07/s），支持图生视频/视频生视频，时长可到 2 分钟。
 * 访问方式：可灵开放平台 或 fal.ai / Replicate 托管。本适配器用「可配置端点 + Bearer Key」
 * 的通用 REST 模式实现；缺 Key 时进入 demo 模式（返回占位结果，便于离线跑通工作流）。
 *
 * 接入真实环境：传入 { apiKey, baseUrl }，并按你所用平台核对 请求体/响应字段（见 // VERIFY）。
 */
import { BaseVideoModel } from "../../core/VideoModel";
import { GenerateParams, GenerateResult, ModelCapabilities, ModelSpecificPrompt, TaskStatus } from "../../core/types";

export interface KlingConfig {
  apiKey?: string;
  /** 任务创建端点。VERIFY：按你所用平台（可灵开放平台/fal/replicate）填写 */
  baseUrl?: string;
}

export class KlingAdapter extends BaseVideoModel {
  readonly modelId = "kling";
  readonly modelName = "可灵 Kling";
  readonly capabilities: ModelCapabilities = {
    generateModes: ["text2video", "image2video", "video2video"],
    maxDuration: 120,
    minDuration: 5,
    durationStep: 1,
    resolutions: ["720p", "1080p"],
    aspectRatios: ["9:16", "16:9", "1:1"],
    referenceImages: { min: 0, max: 1, supportedRoles: ["subject", "style"] },
    audioSupport: false,
    consistencyControl: { supported: true, min: 0, max: 1, default: 0.5 },
    qualityTiers: ["standard", "pro"],
    costPerSecond: 0.07,
    costTierMultiplier: { standard: 1, pro: 1.6 },
    supportedLanguages: ["zh", "en"],
    deploymentType: "cloud-api",
    features: ["camera-control", "character-consistency", "video2video"],
  };

  constructor(private cfg: KlingConfig = {}) {
    super();
  }

  protected async doGenerate(p: ModelSpecificPrompt, params: GenerateParams): Promise<GenerateResult> {
    if (!this.cfg.apiKey || !this.cfg.baseUrl) {
      // demo 模式：无 Key 时返回占位，保证工作流可离线跑通
      return { taskId: `kling-demo-${Date.now()}`, state: "succeeded", videoUrl: "demo://kling/hook.mp4", hasNativeAudio: false, modelId: this.modelId };
    }
    // VERIFY：以下请求体字段名按你所用平台核对
    const resp = await fetch(this.cfg.baseUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${this.cfg.apiKey}` },
      body: JSON.stringify({
        prompt: p.prompt,
        negative_prompt: (p.params.negative_prompt as string) ?? "",
        duration: params.durationSec,
        aspect_ratio: params.aspectRatio,
        mode: params.mode,
        image_url: params.referenceImages?.find((r) => r.role === "subject")?.url,
        cfg_scale: params.consistency ?? this.capabilities.consistencyControl.default,
      }),
    });
    if (!resp.ok) throw new Error(`Kling API ${resp.status}: ${await resp.text()}`);
    const data = (await resp.json()) as { task_id?: string; id?: string; video_url?: string };
    return {
      taskId: data.task_id ?? data.id ?? "unknown",
      state: data.video_url ? "succeeded" : "queued",
      videoUrl: data.video_url,
      hasNativeAudio: false,
      modelId: this.modelId,
      raw: data,
    };
  }

  protected async doGetStatus(taskId: string): Promise<TaskStatus> {
    if (!this.cfg.apiKey || !this.cfg.baseUrl) return { taskId, state: "succeeded", videoUrl: "demo://kling/hook.mp4" };
    // VERIFY：状态查询端点
    const resp = await fetch(`${this.cfg.baseUrl}/${taskId}`, { headers: { Authorization: `Bearer ${this.cfg.apiKey}` } });
    const data = (await resp.json()) as { status?: string; video_url?: string; progress?: number };
    const map: Record<string, TaskStatus["state"]> = { processing: "running", succeed: "succeeded", failed: "failed" };
    return { taskId, state: map[data.status ?? ""] ?? "running", videoUrl: data.video_url, progress: data.progress };
  }

  async healthCheck(): Promise<boolean> {
    return !!this.cfg.apiKey && !!this.cfg.baseUrl;
  }
}
