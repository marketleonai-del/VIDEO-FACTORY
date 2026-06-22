/**
 * batch.ts — 大规模批量示例：1 基准 → N 条并发生成 + 任务追踪 + 成本/质检汇总
 * 运行：npx ts-node examples/batch.ts   （demo 模式，无需 Key）
 */
import { createEngine } from "../index";
import { BatchExecutor } from "../core/BatchExecutor";
import { MemoryJobStore } from "../core/JobStore";
import { logger } from "../core/runtime";
import { WorkflowInput } from "../workflows/WorkflowContext";

async function main(): Promise<void> {
  logger.setLevel("warn"); // 安静点，只看结果
  const { managers, builder } = createEngine({ includeSkeletons: false });

  const base: WorkflowInput = {
    mode: "from-winner",
    winner: { hook: "去污前后对比", structure: "痛点→产品→效果→CTA", coreSellingPoint: "强力去污+温和" },
    hasRealPersonAudio: true,
    realPersonAudioSample: "file://voice.wav",
    hasOwnMaterials: true,
    materials: [{ id: "R1", type: "真人口播", url: "file://r1.mp4", trustValue: "high" }],
    budgetTier: "standard",
    platform: "douyin",
    matrixCount: 1,
    language: "zh",
    durationSec: 30,
    aspectRatio: "9:16",
  };

  const store = new MemoryJobStore<WorkflowInput, unknown>();
  const be = new BatchExecutor(managers.models, managers.tts, builder);
  const summary = await be.run(base, 20, { concurrency: 5, jobStore: store as never });

  console.log(`矩阵 20 条：成功 ${summary.succeeded}/${summary.total}，全部过质检=${summary.allQualityPass}，总成本≈$${summary.totalCostUsd}`);
  console.log("前3条质检分:", summary.results.slice(0, 3).map((r) => r.qualityScore));
  console.log("JobStore 记录数:", store.list().length);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
