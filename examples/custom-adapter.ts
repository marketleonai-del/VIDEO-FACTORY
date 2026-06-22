/**
 * custom-adapter.ts — 插件式：新增一个视频模型只写 Adapter，不改核心
 * 运行：npx ts-node examples/custom-adapter.ts
 */
import { BaseVideoModel } from "../core/VideoModel";
import { GenerateParams, GenerateResult, ModelCapabilities, ModelSpecificPrompt, TaskStatus } from "../core/types";
import { ModelRegistry } from "../core/ModelRegistry";

/** 假设你接入了一个新模型 "MyModel" */
class MyModelAdapter extends BaseVideoModel {
  readonly modelId = "mymodel";
  readonly modelName = "My New Model";
  readonly capabilities: ModelCapabilities = {
    generateModes: ["text2video", "image2video"],
    maxDuration: 8,
    minDuration: 2,
    durationStep: 1,
    resolutions: ["720p", "1080p"],
    aspectRatios: ["9:16", "16:9"],
    referenceImages: { min: 0, max: 1, supportedRoles: ["subject"] },
    audioSupport: false,
    consistencyControl: { supported: true, default: 0.5 },
    qualityTiers: ["standard"],
    costPerSecond: 0.03,
    supportedLanguages: ["zh", "en"],
    deploymentType: "cloud-api",
    features: ["image2video"],
  };

  protected async doGenerate(p: ModelSpecificPrompt, params: GenerateParams): Promise<GenerateResult> {
    // 你的真实 API 调用
    return { taskId: "my-1", state: "succeeded", videoUrl: "https://example/out.mp4", modelId: this.modelId, raw: { p, params } };
  }
  protected async doGetStatus(taskId: string): Promise<TaskStatus> {
    return { taskId, state: "succeeded" };
  }
  async healthCheck(): Promise<boolean> {
    return true;
  }
}

const registry = new ModelRegistry();
registry.register(new MyModelAdapter());
// 选型会自动把它纳入候选（基于能力画像），无需改任何核心代码
const chosen = registry.select({ mode: "text2video", aspectRatio: "9:16", durationSec: 3, budgetTier: "minimal" });
console.log("候选选型结果:", chosen.map((m) => `${m.modelName}($${m.capabilities.costPerSecond}/s)`));
