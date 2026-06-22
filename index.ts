/**
 * index.ts — universal-video-generator 入口（barrel + 引擎装配）
 * 新增模型只需在 createEngine 多 register 一行（或在你代码里 register）——核心零改动。
 */
export * from "./core/types";
export * from "./core/VideoModel";
export * from "./core/TTSProvider";
export * from "./core/ModelRegistry";
export * from "./core/TTSRegistry";
export * from "./core/VoiceLockManager";
export * from "./core/CostEstimator";
export * from "./core/QualityGate";
export * from "./core/runtime";
export * from "./core/resilience";
export * from "./core/observability";
export * from "./core/errors";
export * from "./core/Config";
export * from "./core/JobStore";
export * from "./core/FileJobStore";
export * from "./core/Storage";
export * from "./core/Queue";
export * from "./core/Auth";
export * from "./core/Quota";
export * from "./core/Metering";
export * from "./core/FFmpegAssembler";
export * from "./core/BatchExecutor";
export * from "./core/longvideo/LongVideoPlanner";
export * from "./core/longvideo/LongFormAssembler";
export * from "./core/longvideo/LongVideoPipeline";
export * from "./core/longvideo/AudioVideoMuxer";
export * from "./adapters/tts/LocalTtsAdapter";
export * from "./core/evolution/SkillParams";
export * from "./core/evolution/Telemetry";
export * from "./core/evolution/QualitySignals";
export * from "./core/evolution/EvolutionEngine";
export * from "./core/evolution/Reporter";
export * from "./core/evolution/VersionChecker";
export * from "./workflows/WorkflowContext";
export * from "./workflows/WorkflowStage";
export * from "./workflows/Workflow";
export * from "./workflows/WorkflowBuilder";

import { CostEstimator } from "./core/CostEstimator";
import { ModelRegistry } from "./core/ModelRegistry";
import { QualityGate } from "./core/QualityGate";
import { TTSRegistry } from "./core/TTSRegistry";
import { VoiceLockManager } from "./core/VoiceLockManager";
import { loadConfigFromEnv, loadDotenvFile, LoadedConfig } from "./core/Config";
import { WorkflowManagers } from "./workflows/WorkflowContext";
import { WorkflowBuilder } from "./workflows/WorkflowBuilder";
import { KlingAdapter } from "./adapters/video/KlingAdapter";
import { RunwayAdapter } from "./adapters/video/RunwayAdapter";
import { SeedanceAdapter } from "./adapters/video/SeedanceAdapter";
import { VeoAdapter } from "./adapters/video/VeoAdapter";
import { VIDEO_SKELETONS } from "./adapters/video/skeletons";
import { ElevenLabsAdapter } from "./adapters/tts/ElevenLabsAdapter";
import { CosyVoiceAdapter } from "./adapters/tts/CosyVoiceAdapter";
import { VolcengineVoiceAdapter } from "./adapters/tts/VolcengineVoiceAdapter";
import { GptSoVITSAdapter } from "./adapters/tts/GptSoVITSAdapter";
import { TTS_SKELETONS } from "./adapters/tts/skeletons";
import { LocalTtsAdapter } from "./adapters/tts/LocalTtsAdapter";

export interface EngineConfig {
  klingApiKey?: string; klingBaseUrl?: string;
  runwayApiKey?: string; runwayBaseUrl?: string;
  seedanceApiKey?: string; seedanceBaseUrl?: string;
  veoApiKey?: string; veoBaseUrl?: string;
  elevenLabsApiKey?: string;
  volcengineToken?: string; volcengineAppId?: string;
  cosyVoiceBaseUrl?: string; cosyVoiceEnabled?: boolean;
  gptSoVitsBaseUrl?: string; gptSoVitsEnabled?: boolean;
  localTtsBaseUrl?: string; localTtsEnabled?: boolean; localTtsModel?: "moss" | "kokoro" | "piper";
  includeSkeletons?: boolean;
}
export interface Engine {
  managers: WorkflowManagers;
  builder: WorkflowBuilder;
}

/** 装配引擎：注册全部适配器（缺 Key 自动 demo 模式，可离线跑通） */
export function createEngine(cfg: EngineConfig = {}): Engine {
  const models = new ModelRegistry();
  const tts = new TTSRegistry();
  models.register(new KlingAdapter({ apiKey: cfg.klingApiKey, baseUrl: cfg.klingBaseUrl }));
  models.register(new RunwayAdapter({ apiKey: cfg.runwayApiKey, baseUrl: cfg.runwayBaseUrl }));
  models.register(new SeedanceAdapter({ apiKey: cfg.seedanceApiKey, baseUrl: cfg.seedanceBaseUrl }));
  models.register(new VeoAdapter({ apiKey: cfg.veoApiKey, baseUrl: cfg.veoBaseUrl }));
  tts.register(new ElevenLabsAdapter({ apiKey: cfg.elevenLabsApiKey }));
  tts.register(new CosyVoiceAdapter({ baseUrl: cfg.cosyVoiceBaseUrl, enabled: cfg.cosyVoiceEnabled }));
  tts.register(new VolcengineVoiceAdapter({ token: cfg.volcengineToken, appId: cfg.volcengineAppId }));
  tts.register(new GptSoVITSAdapter({ baseUrl: cfg.gptSoVitsBaseUrl, enabled: cfg.gptSoVitsEnabled }));
  // 本地开源 TTS（无云 API，零成本，CPU 可跑）——默认主选，引擎"省钱优先"会优先选它而非云 TTS
  tts.register(new LocalTtsAdapter({ baseUrl: cfg.localTtsBaseUrl, enabled: cfg.localTtsEnabled, model: cfg.localTtsModel ?? "moss" }));
  if (cfg.includeSkeletons !== false) {
    VIDEO_SKELETONS.forEach((m) => models.register(m));
    TTS_SKELETONS.forEach((p) => tts.register(p));
  }
  const voiceLock = new VoiceLockManager(tts);
  return {
    managers: { models, tts, voiceLock, cost: new CostEstimator(models, tts), quality: new QualityGate() },
    builder: new WorkflowBuilder(),
  };
}

/** 从环境变量/.env 装配引擎（生产推荐） */
export function createEngineFromEnv(): Engine {
  loadDotenvFile();
  const c: LoadedConfig = loadConfigFromEnv();
  return createEngine({ ...c });
}
