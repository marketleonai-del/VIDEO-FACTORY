/**
 * Workflow.ts — 工作流执行引擎
 *
 * 按顺序跑 Stage：先看 precondition，再 execute（带重试），再 postValidation，
 * 失败则 fallback；fallback 也失败则抛 WORKFLOW_ERROR。全程写 ctx.trace。
 */
import { UVGError } from "../core/types";
import { WorkflowContext } from "./WorkflowContext";
import { WorkflowStage } from "./WorkflowStage";

export class Workflow {
  private stages: WorkflowStage[] = [];

  addStage(stage: WorkflowStage): this {
    this.stages.push(stage);
    return this;
  }

  getStages(): WorkflowStage[] {
    return [...this.stages];
  }

  /** 执行整条工作流 */
  async run(ctx: WorkflowContext): Promise<WorkflowContext> {
    let cur = ctx;
    for (const stage of this.stages) {
      if (!stage.precondition(cur)) {
        cur.trace.push(`skip: ${stage.id}`);
        continue;
      }
      cur = await this.runStage(stage, cur);
    }
    return cur;
  }

  private async runStage(stage: WorkflowStage, ctx: WorkflowContext): Promise<WorkflowContext> {
    const attempts = stage.retry?.maxAttempts ?? 1;
    let lastErr: Error | undefined;

    for (let i = 1; i <= attempts; i++) {
      try {
        let next = await stage.execute(ctx);
        if (stage.postValidation && !stage.postValidation(next)) {
          throw new UVGError(`${stage.id} 后置验证未通过`, "WORKFLOW_ERROR");
        }
        next.trace.push(`ok: ${stage.id}${i > 1 ? ` (retry ${i})` : ""}`);
        return next;
      } catch (e) {
        lastErr = e as Error;
        ctx.trace.push(`fail: ${stage.id} attempt ${i} — ${lastErr.message}`);
        if (i < attempts && stage.retry) await delay(stage.retry.delayMs);
      }
    }

    // 重试用尽 → 回退
    if (stage.fallback && lastErr) {
      try {
        const fb = await stage.fallback(ctx, lastErr);
        fb.trace.push(`fallback-ok: ${stage.id}`);
        return fb;
      } catch (e) {
        throw new UVGError(`${stage.id} 回退也失败: ${(e as Error).message}`, "WORKFLOW_ERROR", e);
      }
    }
    throw new UVGError(`${stage.id} 失败且无回退: ${lastErr?.message}`, "WORKFLOW_ERROR", lastErr);
  }
}

function delay(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms));
}
