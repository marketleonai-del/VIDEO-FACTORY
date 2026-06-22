/**
 * basic-usage.ts — 最小可运行示例（demo 模式，无需任何 API Key）
 * 运行：npx ts-node examples/basic-usage.ts
 */
import { createEngine } from "../index";
import { createContext } from "../workflows/WorkflowContext";

async function main(): Promise<void> {
  // 缺 Key → 样板适配器自动 demo 模式，可离线跑通整条流水线
  const { managers, builder } = createEngine({ includeSkeletons: false });

  const ctx = createContext(
    {
      mode: "from-scratch",
      product: "便携榨汁杯",
      hasRealPersonAudio: false, // 无真人 → @voice1 用内置音色
      hasOwnMaterials: false,
      budgetTier: "standard",
      platform: "douyin",
      matrixCount: 1,
      language: "zh",
      durationSec: 30,
      aspectRatio: "9:16",
    },
    managers,
  );

  const workflow = builder.build(ctx);
  console.log("动态组装的 Stage:", workflow.getStages().map((s) => s.id).join(" → "));

  const result = await workflow.run(ctx);
  console.log("\n执行轨迹:");
  result.trace.forEach((t) => console.log("  " + t));
  console.log("\n选中视频模型:", result.artifacts.selectedVideoModelIds);
  console.log("声纹:", result.managers.voiceLock.getVoice("voice1"));
  console.log("质量:", result.artifacts.qualityResults?.[0]);
  console.log("成本:", result.artifacts.costReport);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
