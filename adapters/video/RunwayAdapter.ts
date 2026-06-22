/**
 * RunwayAdapter.ts — Runway Gen-4 视频模型适配器（样板·真实实现）
 *
 * API 友好，~$0.05/s（Turbo）。擅长图生视频。
 * 通用 REST 模式 + Bearer Key；缺 Key 进入 demo 模式。
 * 真实接入按 Runway 官方 API 核对端点/字段（// VERIFY）。
 */
import { BaseVideoModel } from "../../core/VideoModel";
import { GenerateParams, GenerateResult, ModelCapabilities, ModelSpecificPrompt, TaskStatus } from "../../core/types";

export interface RunwayConfig {
  apiKey?: string;
  baseUrl?: string; // 默认官方 API；VERIFY
}

export class RunwayAdapter extends BaseVideoModel {
  readonly modelId = "runway";
  readonly modelName = "Runway Gen-4";
  readonly capabilities: ModelCapabilities = {
    generateModes: ["text2video", "image2video"],
    maxDuration: 16,
    minDuration: 2,
    durationStep: 1,
    resolutions: ["720p", "1080p"],
    aspectRatios: ["9:16", "16:9", "1:1", "4:3"],
    referenceImages: { min: 0, max: 1, supportedRoles: ["subject"] },
    audioSupport: false,
    consistencyControl: { supported: false },
    qualityTiers: ["turbo", "standard"],
    costPerSecond: 0.05,
    costTierMultiplier: { turbo: 1, standard: 2 },
    supportedLanguages: ["en", "zh"],
    deploymentType: "cloud-api",
    features: ["image2video", "camera-control"],
  };

  constructor(private cfg: RunwayConfig = {}) {
    super();
  }

  protected async doGenerate(p: ModelSpecificPrompt, params: GenerateParams): Promise<GenerateResult> {
    if (!this.cfg.apiKey || !this.cfg.baseUrl) {
      return { taskId: `runway-demo-${Date.now()}`, state: "succeeded", videoUrl: "demo://runway/hook.mp4", hasNativeAudio: false, modelId: this.modelId };
    }
    const resp = await fetch(this.cfg.baseUrl, {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${this.cfg.apiKey}` },
      body: JSON.stringify({
        promptText: p.prompt,
        duration: params.durationSec,
        ratio: params.aspectRatio,
        promptImage: params.referenceImages?.find((r) => r.role === "subject")?.url, // 图生视频
      }),
    });
    if (!resp.ok) throw new Error(`Runway API ${resp.status}: ${await resp.text()}`);
    const data = (await resp.json()) as { id?: string; output?: string[] };
    return { taskId: data.id ?? "unknown", state: data.output?.length ? "succeeded" : "queued", videoUrl: data.output?.[0], hasNativeAudio: false, modelId: this.modelId, raw: data };
  }

  protected async doGetStatus(taskId: string): Promise<TaskStatus> {
    if (!this.cfg.apiKey || !this.cfg.baseUrl) return { taskId, state: "succeeded", videoUrl: "demo://runway/hook.mp4" };
    const resp = await fetch(`${this.cfg.baseUrl}/${taskId}`, { headers: { Authorization: `Bearer ${this.cfg.apiKey}` } });
    const data = (await resp.json()) as { status?: string; output?: string[] };
    const map: Record<string, TaskStatus["state"]> = { PENDING: "queued", RUNNING: "running", SUCCEEDED: "succeeded", FAILED: "failed" };
    return { taskId, state: map[data.status ?? ""] ?? "running", videoUrl: data.output?.[0] };
  }

  async healthCheck(): Promise<boolean> {
    return !!this.cfg.apiKey && !!this.cfg.baseUrl;
  }
}
