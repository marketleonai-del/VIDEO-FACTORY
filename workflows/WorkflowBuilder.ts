/**
 * WorkflowBuilder.ts — 动态工作流组装器
 *
 * 不写死流程：根据 Context（模式/预算/是否有素材/是否矩阵/平台）动态拼 Stage。
 * Stage 自身也有 precondition 兜底，因此 Builder 负责"放哪些"，引擎负责"跑不跑"。
 */
import { WorkflowContext } from "./WorkflowContext";
import { WorkflowStage } from "./WorkflowStage";
import { Workflow } from "./Workflow";
import { STANDARD_STAGES } from "./stages/standardStages";

export class WorkflowBuilder {
  private stages = new Map<string, WorkflowStage>();

  constructor(registerStandard = true) {
    if (registerStandard) STANDARD_STAGES.forEach((s) => this.registerStage(s));
  }

  /** 注册（或覆盖）一个 Stage——新增能力只加 Stage，不改引擎 */
  registerStage(stage: WorkflowStage): void {
    this.stages.set(stage.id, stage);
  }

  private use(wf: Workflow, id: string): void {
    const s = this.stages.get(id);
    if (s) wf.addStage(s);
  }

  /** 根据上下文动态组装工作流 */
  build(ctx: WorkflowContext): Workflow {
    const wf = new Workflow();
    const { input } = ctx;

    // 1. 始终：模型选型 + 声纹锁定（保证 @voice1 存在）
    this.use(wf, "model-selection");
    this.use(wf, "voice-lock");

    // 2. 从0开始：角度发现 + 脚本生成
    if (input.mode === "from-scratch") {
      this.use(wf, "angle-discovery");
      this.use(wf, "script-generation");
    }

    // 3. 需要分镜（非纯矩阵复用时）
    if (input.mode !== "from-winner") this.use(wf, "storyboard");

    // 4. 有自有素材：素材分析（真 vs AI）
    if (input.hasOwnMaterials) this.use(wf, "material-analysis");

    // 5. 预算决定 AI 用量：钩子始终做；标准/精品再补 B-roll
    this.use(wf, "ai-hook-generation");
    if (input.budgetTier !== "minimal") this.use(wf, "ai-b-roll");

    // 6. 始终：旁白合成（@voice1 统一音轨）+ 组装 + 质量闸
    this.use(wf, "narration-synthesis");
    this.use(wf, "video-assembly");
    this.use(wf, "quality-gate");

    // 7. 矩阵：变体 + 平台适配
    if (input.matrixCount > 1) {
      this.use(wf, "matrix-variants");
      this.use(wf, "platform-adaptation");
    }

    // 8. 始终：成本报告
    this.use(wf, "cost-report");

    return wf;
  }
}
