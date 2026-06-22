/**
 * self-evolve.ts — 自进化示例：合成隐式遥测 → 进化一轮（验证门控）→ 看参数变化（无需 Key）
 * 运行：npx ts-node examples/self-evolve.ts
 */
import { EvolutionEngine, Telemetry, computeQuality, TelemetryEvent } from "../index";

function synth(): TelemetryEvent[] {
  const evs: TelemetryEvent[] = [];
  // 痛点放大：高质量；反常识：低质量（用户隐式信号，无需打分）
  for (let i = 0; i < 40; i++) evs.push(Telemetry.sanitize({ type: "generate", scene: "痛点放大", success: true, qcPass: true, paramsVersion: 1, skillVersion: 1, anonId: "x", ts: Date.now() }));
  for (let i = 0; i < 40; i++) evs.push(Telemetry.sanitize({ type: "generate", scene: "反常识", success: i % 5 !== 0, qcPass: false, paramsVersion: 1, skillVersion: 1, anonId: "x", ts: Date.now() }));
  return evs;
}

async function main(): Promise<void> {
  const eng = new EvolutionEngine();
  const events = synth();
  console.log("隐式质量:", computeQuality(events));
  console.log("进化前 痛点放大=" + eng.getParams().sceneWeights["痛点放大"], " 反常识=" + eng.getParams().sceneWeights["反常识"]);
  const round = eng.evolveRound(events);
  console.log("本轮:", round.accepted ? "✅采纳" : "❌拒绝", "—", round.reason);
  console.log("进化后 痛点放大=" + eng.getParams().sceneWeights["痛点放大"], " 反常识=" + eng.getParams().sceneWeights["反常识"], " v" + eng.getParams().version);
}
main().catch((e) => { console.error(e); process.exit(1); });
