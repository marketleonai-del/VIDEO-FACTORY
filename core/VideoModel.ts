/**
 * VideoModel.ts — 视频模型统一接口（抽象层契约）
 *
 * 所有视频生成工具（Seedance/可灵/Veo/Runway/...）都实现这个接口。
 * 上层业务只调用 VideoModel，永远不知道底下是哪个模型。
 * 新增模型 = 新写一个实现了 VideoModel 的 Adapter，核心代码零改动。
 */
import {
  CostEstimate,
  GenerateParams,
  GenerateResult,
  ModelCapabilities,
  ModelSpecificPrompt,
  TaskStatus,
  UniversalPrompt,
  UVGError,
} from "./types";

/** 视频模型统一契约 */
export interface VideoModel {
  /** 模型唯一 id，如 "kling" */
  readonly modelId: string;
  /** 展示名 */
  readonly modelName: string;
  /** 能力画像（调度与成本的唯一依据） */
  readonly capabilities: ModelCapabilities;

  /** 核心：发起一次生成（异步，返回任务句柄或直接结果） */
  generate(params: GenerateParams): Promise<GenerateResult>;
  /** 轮询任务状态 */
  getTaskStatus(taskId: string): Promise<TaskStatus>;
  /** 成本估算（不发起真实调用） */
  estimateCost(params: GenerateParams): CostEstimate;
  /** 通用提示词 → 模型专属提示词 */
  adaptPrompt(prompt: UniversalPrompt): ModelSpecificPrompt;
  /** 健康检查（key/网络是否就绪） */
  healthCheck(): Promise<boolean>;
}

/**
 * 视频适配器基类：把公共逻辑（成本估算、提示词拼装、参数校验）收敛在这里，
 * 具体 Adapter 只需实现 doGenerate / doGetStatus / healthCheck 与填写 capabilities。
 */
export abstract class BaseVideoModel implements VideoModel {
  abstract readonly modelId: string;
  abstract readonly modelName: string;
  abstract readonly capabilities: ModelCapabilities;

  /** 子类实现真实的 HTTP 调用 */
  protected abstract doGenerate(p: ModelSpecificPrompt, params: GenerateParams): Promise<GenerateResult>;
  protected abstract doGetStatus(taskId: string): Promise<TaskStatus>;
  abstract healthCheck(): Promise<boolean>;

  async generate(params: GenerateParams): Promise<GenerateResult> {
    this.validate(params);
    const adapted = this.adaptPrompt(params.prompt);
    return this.doGenerate(adapted, params);
  }

  getTaskStatus(taskId: string): Promise<TaskStatus> {
    return this.doGetStatus(taskId);
  }

  /** 通用成本估算：秒数 × 单价 × 档位倍率 */
  estimateCost(params: GenerateParams): CostEstimate {
    const cap = this.capabilities;
    const tier = params.qualityTier ?? "standard";
    const mult = cap.costTierMultiplier?.[tier] ?? 1;
    const usd = +(params.durationSec * cap.costPerSecond * mult).toFixed(4);
    return {
      totalUsd: usd,
      currency: "USD",
      approximate: cap.deploymentType !== "cloud-api" ? true : false,
      breakdown: [
        {
          item: `${this.modelName} ${params.durationSec}s × $${cap.costPerSecond}/s × ${mult}(${tier})`,
          usd,
        },
      ],
    };
  }

  /** 默认提示词拼装：description + shot + 后缀 + 负面，锚定图放进 params */
  adaptPrompt(prompt: UniversalPrompt): ModelSpecificPrompt {
    const shot = prompt.shot
      ? Object.entries(prompt.shot)
          .filter(([, v]) => v)
          .map(([k, v]) => `${k}:${v}`)
          .join(", ")
      : "";
    const text = [prompt.description, shot, prompt.suffix].filter(Boolean).join(" | ");
    return {
      modelId: this.modelId,
      prompt: text,
      params: {
        negative_prompt: prompt.negative ?? "",
        anchors: prompt.anchors ?? {},
      },
    };
  }

  /** 参数与能力画像的一致性校验 */
  protected validate(params: GenerateParams): void {
    const cap = this.capabilities;
    if (!cap.generateModes.includes(params.mode)) {
      throw new UVGError(`${this.modelId} 不支持模式 ${params.mode}`, "CAPABILITY_UNSUPPORTED");
    }
    if (params.durationSec > cap.maxDuration || params.durationSec < cap.minDuration) {
      throw new UVGError(
        `${this.modelId} 时长 ${params.durationSec}s 超出 [${cap.minDuration}, ${cap.maxDuration}]`,
        "VALIDATION_ERROR",
      );
    }
    if (!cap.aspectRatios.includes(params.aspectRatio)) {
      throw new UVGError(`${this.modelId} 不支持画幅 ${params.aspectRatio}`, "CAPABILITY_UNSUPPORTED");
    }
  }
}
