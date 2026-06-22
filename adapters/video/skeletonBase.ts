/**
 * skeletonBase.ts — 视频骨架适配器基类
 * P1/P2 适配器继承它：能力画像已填（可用于选型/成本/健康检查），doGenerate 留 TODO。
 * demoMode=true 时返回占位结果以便离线跑通工作流。
 */
import { BaseVideoModel } from "../../core/VideoModel";
import { GenerateParams, GenerateResult, ModelCapabilities, ModelSpecificPrompt, TaskStatus, UVGError } from "../../core/types";

export type Tier = "P0" | "P1" | "P2";

export class SkeletonVideoModel extends BaseVideoModel {
  constructor(
    readonly modelId: string,
    readonly modelName: string,
    readonly capabilities: ModelCapabilities,
    protected tier: Tier,
    protected demoMode = false,
  ) {
    super();
  }
  protected async doGenerate(_p: ModelSpecificPrompt, _params: GenerateParams): Promise<GenerateResult> {
    if (this.demoMode)
      return { taskId: `${this.modelId}-demo-${Date.now()}`, state: "succeeded", videoUrl: `demo://${this.modelId}/clip.mp4`, hasNativeAudio: this.capabilities.audioSupport, modelId: this.modelId };
    throw new UVGError(`${this.modelId} 适配器未接入（${this.tier}）：实现 doGenerate 即可启用`, "ADAPTER_NOT_IMPLEMENTED");
  }
  protected async doGetStatus(taskId: string): Promise<TaskStatus> {
    if (this.demoMode) return { taskId, state: "succeeded", videoUrl: `demo://${this.modelId}/clip.mp4` };
    throw new UVGError(`${this.modelId} 适配器未接入（${this.tier}）`, "ADAPTER_NOT_IMPLEMENTED");
  }
  async healthCheck(): Promise<boolean> {
    return this.demoMode;
  }
  enableDemo(): this {
    this.demoMode = true;
    return this;
  }
}

/** 一份保守的云端默认能力画像（待调研工具用，接入时按实际改） */
export function baseCloudCaps(modes: Array<"text2video" | "image2video" | "video2video">, costPerSecond: number): ModelCapabilities {
  return {
    generateModes: modes,
    maxDuration: 10,
    minDuration: 2,
    durationStep: 1,
    resolutions: ["720p", "1080p"],
    aspectRatios: ["9:16", "16:9", "1:1"],
    referenceImages: { min: 0, max: 1, supportedRoles: ["subject"] },
    audioSupport: false,
    consistencyControl: { supported: false },
    qualityTiers: ["standard"],
    costPerSecond,
    supportedLanguages: ["zh", "en"],
    deploymentType: "cloud-api",
    features: [],
  };
}
