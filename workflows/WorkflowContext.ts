/**
 * WorkflowContext.ts — 工作流执行上下文
 *
 * 贯穿整条流水线：装着用户输入、所选模型、各 Stage 产出的中间产物，以及核心管理器句柄。
 * 每个 Stage 读 ctx、写 ctx，最后由调度器返回完整 ctx。
 */
import { CostEstimator } from "../core/CostEstimator";
import { ModelRegistry } from "../core/ModelRegistry";
import { QualityGate, QualityResult } from "../core/QualityGate";
import { TTSRegistry } from "../core/TTSRegistry";
import { VoiceLockManager } from "../core/VoiceLockManager";
import { BudgetTier, CostEstimate, GenerateResult, Platform } from "../core/types";

/** 用户输入 */
export interface WorkflowInput {
  /** from-scratch=只有产品要从0；from-materials=有自有素材；from-winner=有赢家要矩阵 */
  mode: "from-scratch" | "from-materials" | "from-winner";
  product?: string;
  competitor?: string;
  /** 是否有真人口播素材（决定走克隆还是内置音色） */
  hasRealPersonAudio: boolean;
  realPersonAudioSample?: string;
  /** 是否有自有素材 */
  hasOwnMaterials: boolean;
  materials?: Array<{ id: string; type: string; url: string; durationSec?: number; trustValue?: "high" | "mid" | "low" }>;
  budgetTier: BudgetTier;
  platform: Platform;
  /** 要几条：>1 触发矩阵 */
  matrixCount: number;
  language: string;
  durationSec: number;
  aspectRatio: string;
  /** from-winner 模式下的赢家描述 */
  winner?: { hook: string; structure: string; coreSellingPoint: string };
}

/** 中间产物 */
export interface WorkflowArtifacts {
  selectedVideoModelIds?: string[];
  selectedTTSId?: string;
  lockedVoiceId?: string;
  angles?: Array<{ family: string; name: string; hook: string; winScore: number }>;
  script?: string;
  storyboard?: Array<{ shot: number; source: "real" | "ai" | "hybrid"; durationSec: number; desc: string }>;
  materialPlan?: Array<{ shot: number; source: "real" | "ai" | "hybrid"; materialId?: string }>;
  aiHookResults?: GenerateResult[];
  bRollResults?: GenerateResult[];
  narrationAudioUrls?: string[];
  assemblyManifest?: unknown;
  qualityResults?: QualityResult[];
  variants?: Array<{ index: number; hookFamily: string; persona: string; platform: Platform }>;
  costReport?: CostEstimate;
}

export interface WorkflowManagers {
  models: ModelRegistry;
  tts: TTSRegistry;
  voiceLock: VoiceLockManager;
  cost: CostEstimator;
  quality: QualityGate;
}

export interface WorkflowContext {
  input: WorkflowInput;
  artifacts: WorkflowArtifacts;
  managers: WorkflowManagers;
  /** 执行轨迹（哪些 Stage 跑了/跳过/回退） */
  trace: string[];
}

/** 便捷构造 */
export function createContext(input: WorkflowInput, managers: WorkflowManagers): WorkflowContext {
  return { input, artifacts: {}, managers, trace: [] };
}
