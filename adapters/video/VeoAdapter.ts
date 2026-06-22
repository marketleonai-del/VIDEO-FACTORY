/**
 * VeoAdapter.ts — Veo 3.1（Google）适配器（P0·真实实现）
 * 多档位：Lite ~$0.05/s、Standard ~$0.40/s，带原生音频，高保真。
 * 通用 REST + Bearer；缺 Key → demo 模式。VERIFY：按 Google/Vertex 或托管平台核对。
 */
import { BaseVideoModel } from "../../core/VideoModel";
import { withRetry } from "../../core/runtime";
import { GenerateParams, GenerateResult, ModelCapabilities, ModelSpecificPrompt, TaskStatus } from "../../core/types";

export interface VeoConfig {
  apiKey?: string;
  baseUrl?: string;
}

export class VeoAdapter extends BaseVideoModel {
  readonly modelId = "veo";
  readonly modelName = "Veo 3.1（Google）";
  readonly capabilities: ModelCapabilities = {
    generateModes: ["text2video", "image2video"],
    maxDuration: 8,
    minDuration: 4,
    durationStep: 1,
    resolutions: ["720p", "1080p", "4K"],
    aspectRatios: ["9:16", "16:9", "1:1"],
    referenceImages: { min: 0, max: 1, supportedRoles: ["subject"] },
    audioSupport: true,
    consistencyControl: { supported: false },
    qualityTiers: ["lite", "standard"],
    costPerSecond: 0.1,
    costTierMultiplier: { lite: 0.5, standard: 4 }, // lite ~$0.05/s, standard ~$0.40/s
    supportedLanguages: ["en", "zh"],
    deploymentType: "cloud-api",
    features: ["native-audio", "high-fidelity"],
  };

  constructor(private cfg: VeoConfig = {}) {
    super();
  }

  protected async doGenerate(p: ModelSpecificPrompt, params: GenerateParams): Promise<GenerateResult> {
    if (!this.cfg.apiKey || !this.cfg.baseUrl) {
      return { taskId: `veo-demo-${Date.now()}`, state: "succeeded", videoUrl: "demo://veo/clip.mp4", hasNativeAudio: true, modelId: this.modelId };
    }
    return withRetry(async () => {
      const resp = await fetch(this.cfg.baseUrl!, {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${this.cfg.apiKey}` },
        body: JSON.stringify({
          prompt: p.prompt,
          durationSeconds: params.durationSec,
          aspectRatio: params.aspectRatio,
          resolution: params.resolution,
          tier: params.qualityTier ?? "lite",
          image: params.referenceImages?.find((r) => r.role === "subject")?.url,
        }),
      });
      if (!resp.ok) throw new Error(`Veo ${resp.status}: ${await resp.text()}`);
      const data = (await resp.json()) as { name?: string; operationId?: string; videoUri?: string };
      return { taskId: data.operationId ?? data.name ?? "unknown", state: data.videoUri ? "succeeded" : "queued", videoUrl: data.videoUri, hasNativeAudio: params.wantNativeAudio ?? false, modelId: this.modelId, raw: data };
    });
  }

  protected async doGetStatus(taskId: string): Promise<TaskStatus> {
    if (!this.cfg.apiKey || !this.cfg.baseUrl) return { taskId, state: "succeeded", videoUrl: "demo://veo/clip.mp4" };
    const resp = await fetch(`${this.cfg.baseUrl}/${taskId}`, { headers: { Authorization: `Bearer ${this.cfg.apiKey}` } });
    const data = (await resp.json()) as { done?: boolean; videoUri?: string; error?: string };
    return { taskId, state: data.error ? "failed" : data.done ? "succeeded" : "running", videoUrl: data.videoUri, error: data.error };
  }

  async healthCheck(): Promise<boolean> {
    return !!this.cfg.apiKey && !!this.cfg.baseUrl;
  }
}
