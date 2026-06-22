/**
 * WorkflowStage.ts — Stage 契约
 *
 * 每个 Stage 是工作流的一个步骤：有前置条件、执行逻辑、后置验证、失败回退、重试。
 * 调度器据此决定是否执行、是否回退。Stage 之间通过 WorkflowContext 传递产物。
 */
import { WorkflowContext } from "./WorkflowContext";

export interface WorkflowStage {
  id: string;
  name: string;
  /** 前置条件：返回 true 才执行该 Stage */
  precondition: (ctx: WorkflowContext) => boolean;
  /** 执行逻辑：返回更新后的 ctx */
  execute: (ctx: WorkflowContext) => Promise<WorkflowContext>;
  /** 后置验证：执行后必须满足，否则触发 fallback/报错 */
  postValidation?: (ctx: WorkflowContext) => boolean;
  /** 失败回退 */
  fallback?: (ctx: WorkflowContext, error: Error) => Promise<WorkflowContext>;
  /** 重试策略 */
  retry?: { maxAttempts: number; delayMs: number };
}
