/**
 * web/server.ts — 视频工厂 Web 服务（node:http，零依赖）
 * 双模式：本服务在线 = 后端 SaaS（统一管 Key、转发、拼接、统计）；前端检测不到本服务 = 纯前端模式。
 * 路由：GET / (前端页) · GET /api/config · POST /api/generate · GET /api/jobs/:id · GET /api/stats · POST /api/telemetry
 * 复用引擎：createEngineFromEnv + LongVideoPlanner + LongFormAssembler，逐段更新进度。
 */
import * as http from "node:http";
import * as fs from "node:fs";
import * as path from "node:path";
import { createEngineFromEnv, Engine } from "../index";
import { LongVideoPlanner } from "../core/longvideo/LongVideoPlanner";
import { LongFormAssembler } from "../core/longvideo/LongFormAssembler";
import { AudioVideoMuxer } from "../core/longvideo/AudioVideoMuxer";
import { FileJobStore } from "../core/FileJobStore";
import { GenerateParams, GenerateResult } from "../core/types";
import { JsonLogger } from "../core/observability";

interface WebInput { prompt: string; mode: "ai" | "mix"; durationSec: number; userClipUrl?: string; voiceOn?: boolean; voiceText?: string; emotion?: string; voice?: string; speed?: number; count?: number; platform?: string; country?: string; language?: string }
interface SegState { index: number; source: string; state: "pending" | "running" | "succeeded" | "failed" }
interface WebResult { segments: SegState[]; stitch: "pending" | "running" | "succeeded" | "failed"; videoUrl?: string; voiceUrl?: string }

const log = new JsonLogger();
const engine: Engine = createEngineFromEnv();
const planner = new LongVideoPlanner();
const assembler = new LongFormAssembler();
const muxer = new AudioVideoMuxer();
const store = new FileJobStore<WebInput, WebResult>(process.env.UVG_WEB_JOBS || "./.uvg-web-jobs.json");
const PUBLIC = path.join(__dirname, "public");
const STATS_FILE = process.env.UVG_WEB_STATS || "./.uvg-web-stats.json";
const stats = loadStats();

function loadStats(): { total: number; success: number; byMode: Record<string, number> } {
  try { if (fs.existsSync(STATS_FILE)) return JSON.parse(fs.readFileSync(STATS_FILE, "utf8")); } catch { /* */ }
  return { total: 0, success: 0, byMode: {} };
}
function saveStats(): void { try { fs.writeFileSync(STATS_FILE, JSON.stringify(stats)); } catch { /* */ } }

function send(res: http.ServerResponse, code: number, body: unknown): void {
  res.writeHead(code, { "Content-Type": "application/json; charset=utf-8", "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "content-type", "Access-Control-Allow-Methods": "GET, POST, OPTIONS" });
  res.end(JSON.stringify(body, null, 2));
}
function readBody(req: http.IncomingMessage): Promise<string> {
  return new Promise((resolve, reject) => { let b = ""; req.on("data", (c) => { b += c; if (b.length > 2_000_000) reject(new Error("too large")); }); req.on("end", () => resolve(b)); req.on("error", reject); });
}
async function generateWithFallback(ids: string[], params: GenerateParams): Promise<GenerateResult> {
  let last: Error | undefined;
  for (const id of ids) { try { return await engine.managers.models.get(id).generate(params); } catch (e) { last = e as Error; } }
  throw last ?? new Error("无可用模型");
}

/** 异步处理：逐段生成（续帧一致）+ 拼接，每步更新 job 进度 */
async function processJob(jobId: string, input: WebInput): Promise<void> {
  store.update(jobId, { state: "running" });
  const plan = planner.plan(input.durationSec, { maxSegSec: 10, subjectAnchor: input.prompt.slice(0, 40), transition: "xfade" });
  const segments: SegState[] = plan.segments.map((s) => ({ index: s.index, source: s.source, state: "pending" }));
  store.update(jobId, { result: { segments, stitch: "pending" } });
  const ids = engine.managers.models.select({ mode: "text2video", aspectRatio: "9:16", durationSec: 10, budgetTier: "standard" }).map((m) => m.modelId);
  const clips: string[] = [];
  let prev: string | undefined;
  for (const seg of plan.segments) {
    const si = segments.find((x) => x.index === seg.index)!;
    si.state = "running"; store.update(jobId, { result: { segments, stitch: "pending" } });
    const params: GenerateParams = {
      mode: prev ? "image2video" : "text2video",
      prompt: { description: (seg.prompt || input.prompt) + (seg.subjectAnchor ? " | 主体恒定:" + seg.subjectAnchor : ""), suffix: "连续运镜,同风格,no text" },
      durationSec: seg.durationSec, resolution: "720p", aspectRatio: "9:16",
      referenceImages: prev ? [{ role: "subject", url: prev }] : undefined, wantNativeAudio: false,
    };
    try {
      const r = await generateWithFallback(ids, params);
      prev = r.videoUrl; clips.push(r.videoUrl || ""); si.state = "succeeded";
    } catch (e) { si.state = "failed"; store.update(jobId, { state: "failed", error: (e as Error).message, result: { segments, stitch: "pending" } }); return; }
    store.update(jobId, { result: { segments, stitch: "pending" } });
  }
  store.update(jobId, { result: { segments, stitch: "running" } });
  const asm = assembler.render(clips, "./.uvg-web-out/" + jobId + ".mp4", { transition: "xfade" }, plan.segments.map((s) => s.durationSec));
  const videoUrl = asm.rendered ? asm.outputPath : clips.find((c) => /^https?:/.test(c)) || undefined;
  // 本地 TTS 配音（无云 API）+ 音视频合成（方案 A）；缺 ffmpeg/远程地址时优雅跳过
  let voiceUrl: string | undefined;
  let finalUrl = videoUrl;
  if (input.voiceOn !== false) {
    try {
      const ttsId = engine.managers.tts.select({ needClone: false, preferLocal: true })[0]?.providerId;
      if (ttsId) {
        const audio = await engine.managers.tts.get(ttsId).synthesize({ text: input.voiceText || input.prompt, voiceId: input.voice || "default", emotion: input.emotion, speed: input.speed ?? 1, language: input.language || "zh" });
        voiceUrl = audio.audioUrl;
        if (asm.rendered && videoUrl && !/^demo:|^https?:/.test(voiceUrl)) {
          const mux = muxer.render(videoUrl, voiceUrl, "./.uvg-web-out/" + jobId + "-voiced.mp4", { fit: "pad" });
          if (mux.muxed) finalUrl = mux.outputPath;
        }
      }
    } catch (e) { log.warn("voice synth/mux failed", { err: (e as Error).message }); }
  }
  store.update(jobId, { state: "succeeded", progress: 100, result: { segments, stitch: "succeeded", videoUrl: finalUrl, voiceUrl } });
  stats.total++; stats.success++; stats.byMode[input.mode] = (stats.byMode[input.mode] || 0) + 1; saveStats();
}

const server = http.createServer(async (req, res) => {
  try {
    const url = new URL(req.url ?? "/", "http://localhost");
    const p = url.pathname;
    if (req.method === "OPTIONS") return send(res, 204, {});
    if (req.method === "GET" && p === "/api/config")
      return send(res, 200, { backend: true, github: process.env.UVG_GITHUB || "https://github.com/", features: ["ai", "mix", "long", "self-evolve", "local-tts"], emotions: ["neutral", "happy", "sad", "serious", "gentle", "excited", "calm"], voices: ["default", "female-1", "male-1"] });
    if (req.method === "GET" && p === "/api/stats") return send(res, 200, stats);
    if (req.method === "POST" && p === "/api/telemetry") return send(res, 200, { ok: true });
    if (req.method === "POST" && p === "/api/generate") {
      const body = JSON.parse((await readBody(req)) || "{}") as WebInput;
      if (!body.prompt) return send(res, 400, { error: { message: "缺少 prompt" } });
      const job = store.create({ prompt: body.prompt, mode: body.mode || "ai", durationSec: Number(body.durationSec) || 10, voiceOn: body.voiceOn, voiceText: body.voiceText, emotion: body.emotion, voice: body.voice, speed: body.speed, count: Number(body.count) || 1, platform: body.platform, country: body.country, language: body.language });
      void processJob(job.id, job.input);
      return send(res, 202, { jobId: job.id });
    }
    if (req.method === "GET" && p.startsWith("/api/jobs/")) {
      const job = store.get(p.slice("/api/jobs/".length));
      if (!job) return send(res, 404, { error: "not found" });
      const r = job.result;
      return send(res, 200, { state: job.state, segments: r?.segments, stitch: r?.stitch, videoUrl: r?.videoUrl, voiceUrl: r?.voiceUrl, error: job.error });
    }
    const file = p === "/" ? "/index.html" : p;
    const fp = path.join(PUBLIC, path.normalize(file).replace(/^(\.\.[/\\])+/, ""));
    if (fp.startsWith(PUBLIC) && fs.existsSync(fp) && fs.statSync(fp).isFile()) {
      const ext = path.extname(fp);
      const ct = ext === ".html" ? "text/html" : ext === ".js" ? "text/javascript" : ext === ".css" ? "text/css" : "application/octet-stream";
      res.writeHead(200, { "Content-Type": ct + "; charset=utf-8" });
      return res.end(fs.readFileSync(fp));
    }
    send(res, 404, { error: "not found" });
  } catch (e) { send(res, 500, { error: { message: (e as Error).message } }); }
});

const PORT = Number(process.env.WEB_PORT ?? 8080);
server.listen(PORT, () => log.info("video-factory web started", { port: PORT, url: "http://localhost:" + PORT }));
export { server };
