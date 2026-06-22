/**
 * matrix-workflow.ts — 矩阵示例：1 个赢家 → 20 条变体，统一声纹，逐条过精品闸
 * 运行：npx ts-node examples/matrix-workflow.ts
 */
import { createEngine } from "../index";
import { createContext } from "../workflows/WorkflowContext";

async function main(): Promise<void> {
  const { managers, builder } = createEngine({
    // 有真人口播样本 → 克隆为 @voice1（这里给个示意路径；demo 下不真连）
    cosyVoiceEnabled: false,
    includeSkeletons: false,
  });

  const ctx = createContext(
    {
      mode: "from-winner",
      winner: { hook: "去污前后对比冲击", structure: "痛点→产品→效果→CTA", coreSellingPoint: "强力去污+温和不伤手" },
      hasRealPersonAudio: true,
      realPersonAudioSample: "file://sample-voice.wav",
      hasOwnMaterials: true,
      materials: [
        { id: "R1", type: "真人口播", url: "file://r1.mp4", trustValue: "high" },
        { id: "R2", type: "真实效果对比", url: "file://r2.mp4", trustValue: "high" },
      ],
      budgetTier: "standard",
      platform: "douyin",
      matrixCount: 20,
      language: "zh",
      durationSec: 30,
      aspectRatio: "9:16",
    },
    managers,
  );

  const result = await builder.build(ctx).run(ctx);

  console.log("矩阵变体（钩子轮换 + 人设差异化）:");
  result.artifacts.variants?.forEach((v) => console.log(`  #${v.index} [${v.hookFamily}] ${v.persona} @${v.platform}`));
  console.log("\n@voice1（全矩阵统一音色）:", result.managers.voiceLock.getVoice("voice1"));
  console.log("\n矩阵成本（克隆一次性摊薄）:", JSON.stringify(result.artifacts.costReport, null, 2));
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
