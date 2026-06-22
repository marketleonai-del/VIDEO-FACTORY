/**
 * long-video.ts — 长视频示例：60s = 多段(续帧一致)+拼接（demo，无需 Key）
 * 运行：npx ts-node examples/long-video.ts
 */
import { createEngine, LongVideoPipeline } from "../index";

async function main(): Promise<void> {
  const { managers } = createEngine({ includeSkeletons: false });
  const pipe = new LongVideoPipeline(managers.models);
  const res = await pipe.generate(60, {
    aspectRatio: "9:16",
    budgetTier: "standard",
    subjectAnchor: "同一位女主播 + 同款产品",
    maxSegSec: 10,
    reanchorEvery: 3,
    transition: "xfade",
    anchorImageUrl: "file://product.png",
    outputPath: "./long-demo.mp4",
  });
  console.log("分段规划：\n  " + res.plan.notes.join("\n  "));
  console.log("段数:", res.plan.segments.length, "（续帧+主体恒定+每3段再锚定）");
  console.log("拼接:", res.assembly.rendered ? "已渲染" : "命令计划", "\n  " + res.assembly.plan.notes.join("\n  "));
}
main().catch((e) => { console.error(e); process.exit(1); });
