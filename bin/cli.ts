#!/usr/bin/env node
/**
 * cli.ts — 命令行入口（零依赖）
 * 用法：
 *   uvg generate --product "便携榨汁杯" --count 20 --platform douyin --budget standard
 *   uvg long --duration 60 --aspect 9:16 --subject "同一主播+同款产品" --out long.mp4
 *   uvg evolve [--file telemetry.json]      # 跑一轮自进化（验证门控）
 *   uvg health
 */
import {
  createEngineFromEnv, createContext, validateWorkflowInput, BatchExecutor, FFmpegAssembler,
  LongVideoPipeline, EvolutionEngine, Telemetry, computeQuality, WorkflowInput, TelemetryEvent, logger,
} from "../index";
import * as fs from "node:fs";

type Flags = Record<string, string | boolean>;
function parseArgs(argv: string[]): { cmd: string; flags: Flags } {
  const cmd = argv[0] && !argv[0].startsWith("--") ? argv[0] : "help";
  const flags: Flags = {};
  for (let i = 1; i < argv.length; i++) {
    const a = argv[i];
    if (a.startsWith("--")) {
      const key = a.slice(2);
      const next = argv[i + 1];
      if (next && !next.startsWith("--")) { flags[key] = next; i++; } else flags[key] = true;
    }
  }
  return { cmd, flags };
}

const HELP = `universal-video-generator CLI
  generate  生成（单条/矩阵）：--product --mode --count --platform --budget --duration --aspect --lang --real-audio --concurrency --render <out>
  long      长视频：--duration <秒> --aspect 9:16 --budget --subject "<恒定主体>" --maxseg 10 --transition xfade --out <file>
  evolve    自进化一轮：--file <telemetry.json>（缺省用合成 demo 数据），输出验证门控结果与参数变化
  health    列出已注册模型/TTS 与可用性
  help      显示帮助`;

async function cmdGenerate(flags: Flags): Promise<void> {
  const engine = createEngineFromEnv();
  const count = Number(flags.count ?? 1);
  const input: WorkflowInput = {
    mode: (flags.mode as WorkflowInput["mode"]) ?? "from-scratch",
    product: typeof flags.product === "string" ? flags.product : undefined,
    hasRealPersonAudio: !!flags["real-audio"],
    realPersonAudioSample: typeof flags["real-audio"] === "string" ? (flags["real-audio"] as string) : undefined,
    hasOwnMaterials: (flags.mode as string) === "from-materials",
    budgetTier: (flags.budget as WorkflowInput["budgetTier"]) ?? "standard",
    platform: (flags.platform as WorkflowInput["platform"]) ?? "douyin",
    matrixCount: count,
    language: (flags.lang as string) ?? "zh",
    durationSec: Number(flags.duration ?? 30),
    aspectRatio: (flags.aspect as string) ?? "9:16",
  };
  const errs = validateWorkflowInput(input);
  if (errs.length) { logger.error("输入校验失败：\n - " + errs.join("\n - ")); process.exit(1); }
  if (count > 1) {
    const be = new BatchExecutor(engine.managers.models, engine.managers.tts, engine.builder);
    const s = await be.run(input, count, { concurrency: Number(flags.concurrency ?? 4) });
    console.log(`✅ 矩阵：${s.succeeded}/${s.total} 成功，全部过质检=${s.allQualityPass}，总成本≈$${s.totalCostUsd}`);
  } else {
    const ctx = createContext(input, engine.managers);
    const res = await engine.builder.build(ctx).run(ctx);
    console.log("成本:", res.artifacts.costReport, "\n质检:", res.artifacts.qualityResults?.[0]);
    if (typeof flags.render === "string") {
      const out = new FFmpegAssembler().render(res.artifacts.assemblyManifest as never, flags.render);
      console.log("渲染:", out.rendered ? `已输出 ${out.outputPath}` : "未渲染（见计划）");
    }
  }
}

async function cmdLong(flags: Flags): Promise<void> {
  const engine = createEngineFromEnv();
  const pipe = new LongVideoPipeline(engine.managers.models);
  const res = await pipe.generate(Number(flags.duration ?? 60), {
    aspectRatio: (flags.aspect as string) ?? "9:16",
    budgetTier: (flags.budget as never) ?? "standard",
    subjectAnchor: typeof flags.subject === "string" ? flags.subject : undefined,
    maxSegSec: Number(flags.maxseg ?? 10),
    transition: (flags.transition as "hard" | "xfade") ?? "hard",
    outputPath: typeof flags.out === "string" ? flags.out : "./long-output.mp4",
  });
  console.log("分段规划：\n  " + res.plan.notes.join("\n  "));
  console.log("段数:", res.plan.segments.length);
  console.log("拼接:", res.assembly.rendered ? `已渲染 ${res.assembly.outputPath}` : "命令计划（装 ffmpeg + 本地素材后可真渲染）");
}

async function cmdEvolve(flags: Flags): Promise<void> {
  let events: TelemetryEvent[];
  if (typeof flags.file === "string" && fs.existsSync(flags.file)) {
    const raw = JSON.parse(fs.readFileSync(flags.file, "utf8")) as { events?: TelemetryEvent[] } | TelemetryEvent[];
    events = (Array.isArray(raw) ? raw : raw.events ?? []).map((e) => Telemetry.sanitize(e as unknown as Record<string, unknown>));
  } else {
    events = [];
    for (let i = 0; i < 40; i++) events.push(Telemetry.sanitize({ type: "generate", scene: "痛点放大", success: true, qcPass: true, paramsVersion: 1, skillVersion: 1, anonId: "x", ts: Date.now() }));
    for (let i = 0; i < 40; i++) events.push(Telemetry.sanitize({ type: "generate", scene: "反常识", success: i % 5 !== 0, qcPass: false, paramsVersion: 1, skillVersion: 1, anonId: "x", ts: Date.now() }));
    console.log("(用合成 demo 遥测；真实用 --file telemetry.json)");
  }
  const eng = new EvolutionEngine();
  console.log("隐式质量:", computeQuality(events));
  const round = eng.evolveRound(events);
  console.log("本轮进化:", round.accepted ? "✅采纳" : "❌拒绝", "—", round.reason);
  console.log("参数:", JSON.stringify(eng.getParams().sceneWeights), "v" + eng.getParams().version);
}

async function cmdHealth(): Promise<void> {
  const { managers } = createEngineFromEnv();
  const v = await Promise.all(managers.models.list().map(async (m) => `${m.modelId}: ${(await m.healthCheck()) ? "ready" : "demo/未配置"}`));
  const t = await Promise.all(managers.tts.list().map(async (p) => `${p.providerId}: ${(await p.healthCheck()) ? "ready" : "demo/未配置"}`));
  console.log("视频模型:\n  " + v.join("\n  ") + "\nTTS:\n  " + t.join("\n  "));
}

async function main(): Promise<void> {
  const { cmd, flags } = parseArgs(process.argv.slice(2));
  if (cmd === "generate") await cmdGenerate(flags);
  else if (cmd === "long") await cmdLong(flags);
  else if (cmd === "evolve") await cmdEvolve(flags);
  else if (cmd === "health") await cmdHealth();
  else console.log(HELP);
}
main().catch((e) => { logger.error(String(e)); process.exit(1); });
