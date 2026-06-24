#!/usr/bin/env node
/**
 * live-server.js — 视频工厂「实时后端」v2（纯 Node 零依赖 + 本机 ffmpeg）
 *  - 文生视频：接快子·丽帧（Seedance 2.0）真实接口
 *  - 免描述：prompt 为空时，用网关 LLM(gpt-5.5) 看产品名/图自动写带货分镜脚本
 *  - 长视频：30/50/60s 自动分段(每段≤15s)→ 并发生成 → ffmpeg 拼接成一条
 *  - 矩阵：count>1 生成多条
 * 跑法：node live-server.js   （默认 :8088，读 .env 的 KUAIZI_API_KEY / IMAGE_API_KEY）
 */
const http = require("node:http");
const https = require("node:https");
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const { spawnSync } = require("node:child_process");

(function () { try { const p = path.join(__dirname, ".env"); if (fs.existsSync(p)) fs.readFileSync(p, "utf8").split(/\r?\n/).forEach((l) => { const m = l.match(/^\s*([A-Za-z0-9_]+)\s*=\s*(.*)\s*$/); if (m && !process.env[m[1]]) process.env[m[1]] = m[2].replace(/^["']|["']$/g, ""); }); } catch (e) { /* */ } })();

const KEY = process.env.KUAIZI_API_KEY || "";
const KZ_BASE = process.env.KUAIZI_BASE_URL || "https://aiopenapi.kuaizi.cn";
const GW_KEY = process.env.IMAGE_API_KEY || "";
const GW_BASE = process.env.IMAGE_BASE_URL || "https://www.hfsyapi.cn";
const LLM_MODEL = process.env.LLM_MODEL || "gpt-5.5";
const AGNES_KEY = process.env.AGNES_API_KEY || "";
const AGNES_BASE = process.env.AGNES_BASE_URL || "https://apihub.agnes-ai.com/v1";
const AGNES_VIDEO_MODEL = process.env.AGNES_VIDEO_MODEL || "agnes-video-v2.0";
const PORT = Number(process.env.WEB_PORT || 8088);
const PUBLIC = path.join(__dirname, "web", "public");
const OUT = path.join(__dirname, ".uvg-out");
const FFMPEG = process.env.FFMPEG_BIN || "ffmpeg";
try { fs.mkdirSync(OUT, { recursive: true }); } catch (e) { /* */ }

function reqJSON(base, pathname, headers, body) {
  return new Promise((resolve, reject) => {
    const data = Buffer.from(JSON.stringify(body));
    const u = new URL(base + pathname);
    const r = https.request(u, { method: "POST", headers: Object.assign({ "Content-Type": "application/json", "Content-Length": data.length }, headers) },
      (rr) => { let b = ""; rr.on("data", (c) => (b += c)); rr.on("end", () => { try { resolve({ status: rr.statusCode, json: JSON.parse(b) }); } catch { resolve({ status: rr.statusCode, json: null, raw: b }); } }); });
    r.on("error", reject); r.write(data); r.end();
  });
}
const kz = (p, body, key) => reqJSON(KZ_BASE, p, { "ApiKey": key || KEY }, body);
const gw = (p, body, key) => reqJSON(GW_BASE, p, { "Authorization": "Bearer " + (key || GW_KEY) }, body);

// 网关文生图 → 返回公网 URL（供丽帧作参考图，锁人物/产品一致）
async function genImage(prompt, gKey, refs) {
  if (!(gKey || GW_KEY)) return "";
  try {
    const body = { model: process.env.IMAGE_MODEL_IMG || "gpt-image-2pro", prompt, size: "720x1280", n: 1, response_format: "b64_json" };
    if (refs && refs.length) body.reference_images = refs.slice(0, 6); // 图生图：引用产品/人物参考图锁一致
    const r = await gw("/v1/images/generations", body, gKey);
    const it = (r.json && r.json.data && r.json.data[0]) || {};
    return it.url || "";
  } catch (e) { return ""; }
}

// 作品持久化（关掉页面后也找得到）
const WORKS = path.join(OUT, "works.json");
function loadWorks() { try { if (fs.existsSync(WORKS)) return JSON.parse(fs.readFileSync(WORKS, "utf8")); } catch (e) { /* */ } return []; }
function saveWork(w) { try { const all = loadWorks(); all.unshift(w); fs.writeFileSync(WORKS, JSON.stringify(all.slice(0, 500))); } catch (e) { /* */ } }

// ===== Agnes AI 视频（OpenAI 兼容，apihub.agnes-ai.com/v1，并发上限=1）=====
function av(method, pathname, body, key) {
  return new Promise((resolve, reject) => {
    const data = body ? Buffer.from(JSON.stringify(body)) : null;
    const u = new URL(AGNES_BASE + pathname);
    const h = { "Authorization": "Bearer " + (key || AGNES_KEY) }; if (data) { h["Content-Type"] = "application/json"; h["Content-Length"] = data.length; }
    const r = https.request(u, { method, headers: h }, (x) => { let s = ""; x.on("data", (c) => (s += c)); x.on("end", () => { try { resolve({ status: x.statusCode, json: JSON.parse(s) }); } catch { resolve({ status: x.statusCode, json: null, raw: s }); } }); });
    r.on("error", reject); if (data) r.write(data); r.end();
  });
}
async function agnesCreate(scene, dur, key) {
  try {
    const r = await av("POST", "/videos", { model: AGNES_VIDEO_MODEL, prompt: scene, seconds: String(Math.max(4, Math.min(15, dur || 5))), size: "720x1280" }, key);
    if (r.status && r.status < 300 && r.json && (r.json.id || r.json.task_id)) return { taskId: r.json.id || r.json.task_id };
    const msg = (r.json && (r.json.message || (r.json.error && r.json.error.message))) || (r.raw || "").slice(0, 120) || ("HTTP " + r.status);
    return { busy: /busy|503|rate|too many/i.test(msg + " " + r.status), error: msg };
  } catch (e) { return { error: String((e && e.message) || e) }; }
}
async function agnesPoll(taskId, key) {
  try {
    const r = await av("GET", "/videos/" + taskId, null, key);
    const j = r.json || {};
    if (j.status === "completed") return { done: true, url: j.remixed_from_video_id || j.url || j.video_url || "" };
    if (j.status === "failed") return { done: true, failed: true, error: (j.error && (j.error.message || j.error)) || "failed" };
    return { done: false };
  } catch (e) { return { done: false }; }
}
// 多段 → 下载 + ffmpeg 拼接为一条；返回 /out 相对路径或 ""
// 下载各段 → (拼接) → 裁成 9:16 竖屏(720x1280) → /out 相对路径
async function finalizeVideo(jobId, vi, urls) {
  try {
    const dir = path.join(OUT, jobId + "_" + vi); fs.mkdirSync(dir, { recursive: true });
    const files = [];
    for (let i = 0; i < urls.length; i++) { const fp = path.join(dir, "seg" + i + ".mp4"); await download(urls[i], fp); files.push(fp); }
    const outFile = path.join(OUT, jobId + "_" + vi + ".mp4");
    const vf = "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,setsar=1"; // 填满后居中裁成竖屏，无黑边
    let rr;
    if (files.length === 1) {
      rr = spawnSync(FFMPEG, ["-y", "-i", files[0], "-vf", vf, "-r", "24", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", outFile], { encoding: "utf8" });
    } else {
      const listFile = path.join(dir, "list.txt");
      fs.writeFileSync(listFile, files.map((f) => "file '" + f.replace(/\\/g, "/") + "'").join("\n"));
      rr = spawnSync(FFMPEG, ["-y", "-f", "concat", "-safe", "0", "-i", listFile, "-vf", vf, "-r", "24", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", outFile], { encoding: "utf8" });
    }
    if (fs.existsSync(outFile)) return "/out/" + jobId + "_" + vi + ".mp4";
  } catch (e) { /* */ }
  return "";
}

function download(url, file) {
  return new Promise((resolve, reject) => { const f = fs.createWriteStream(file);
    https.get(url, (r) => { if (r.statusCode >= 300 && r.statusCode < 400 && r.headers.location) { f.close(); return download(r.headers.location, file).then(resolve, reject); } r.pipe(f); f.on("finish", () => f.close(() => resolve(file))); }).on("error", reject); });
}

// 时长 → 分段（每段 4~15s，和≈总时长）
function splitDurations(total) {
  total = Math.max(4, Math.min(120, Math.round(total)));
  if (total <= 15) return [total];
  const n = Math.ceil(total / 15);
  const base = Math.floor(total / n); let rem = total - base * n;
  const arr = []; for (let i = 0; i < n; i++) { let d = base + (rem > 0 ? 1 : 0); if (rem > 0) rem--; arr.push(Math.max(4, Math.min(15, d))); }
  return arr;
}

// 网关 LLM：为产品生成 n 个带货分镜（中文画面脚本）。失败则回退。
async function llmScenes(topic, n, totalSec, imageB64, gKey) {
  const sys = "你是短视频带货导演。只输出一个 JSON 数组（" + n + " 个中文字符串），每个元素是一个分镜的画面脚本（可直接喂给文生视频模型），递进体现：钩子→产品核心卖点→使用/效果→促单。不要解释，不要多余文字。";
  const userText = "产品/主题：" + topic + "。总时长约 " + totalSec + " 秒，分 " + n + " 段。每段一句，画面感强、适合竖屏带货。";
  const content = imageB64 ? [{ type: "text", text: userText }, { type: "image_url", image_url: { url: imageB64 } }] : userText;
  try {
    const r = await gw("/v1/chat/completions", { model: LLM_MODEL, messages: [{ role: "system", content: sys }, { role: "user", content }], max_tokens: 600, temperature: 0.8 }, gKey);
    let txt = r.json && r.json.choices && r.json.choices[0] && r.json.choices[0].message && r.json.choices[0].message.content || "";
    txt = String(txt).replace(/```json|```/g, "").trim();
    const m = txt.match(/\[[\s\S]*\]/);
    const arr = m ? JSON.parse(m[0]) : null;
    if (Array.isArray(arr) && arr.length) { const out = arr.map((x) => String(x)).filter(Boolean); while (out.length < n) out.push(out[out.length - 1]); return out.slice(0, n); }
  } catch (e) { /* fallback below */ }
  const fb = []; for (let i = 0; i < n; i++) fb.push(topic + "，竖屏带货短视频，第" + (i + 1) + "/" + n + "镜，电影感运镜，明亮高级，突出卖点");
  return fb;
}

const jobs = {}; // id -> { state, videos:[{segs:[{taskId,state,url,dur}], out, state}], error }

function createJob(input) {
  const id = "job_" + crypto.randomBytes(6).toString("hex");
  const count = Math.max(1, Math.min(20, Number(input.count) || 1));
  const durations = splitDurations(Number(input.durationSec) || 5);
  const N = durations.length;
  const ratio = "9:16";
  const topic = (input.prompt && input.prompt.trim()) || (input.product && input.product.trim()) || "产品";
  const job = { id, state: "running", videos: [], error: undefined, topic: topic, built: false, phase: "准备中…", storyboard: [], kKey: (input.kuaiziKey || "").trim(), gKey: (input.imageKey || "").trim() };
  jobs[id] = job;
  void buildJob(job, input, count, durations, N, ratio, topic); // 后台跑：秒回 jobId
  return job;
}

async function buildJob(job, input, count, durations, N, ratio, topic) {
  // ① 写分镜故事板脚本（按产品+时长自动匹配镜数）
  job.phase = "① 写分镜故事板脚本";
  let scenes;
  if (N === 1 && input.prompt && input.prompt.trim()) scenes = [input.prompt.trim()];
  else scenes = await llmScenes(topic, N, Number(input.durationSec) || 5, input.imageB64, job.gKey);
  job.storyboard = scenes;
  // 一致性锁：每个分镜视频提示词统一前置，死锁同一产品 + 同一主播，贯穿全片
  const lock = "【一致性锁·全程严格保持不变】同一个" + topic + "：同颜色、同形状、同logo、同包装；同一位主播：同一张脸、同发型、同服装、同妆容；统一画面风格与光线。";

  // ② 生成产品/人物参考图（锁一致），两张并发
  job.phase = "② 生成参考图（锁产品/人物）";
  let pRef = "", cRef = "";
  try {
    const rr = await Promise.all([
      genImage("电商产品参考图：同一件【" + topic + "】，正面清晰展示，纯白背景，统一光照，高清产品摄影，无文字无水印", job.gKey),
      genImage("带货主播人物参考图：同一张脸、同一发型、同一套服装，自然微笑，竖屏写真，纯色背景，高清，无文字", job.gKey),
    ]);
    pRef = rr[0] || ""; cRef = rr[1] || "";
  } catch (e) { /* 无参考图则退回纯文生视频 */ }
  job.refs = { product: pRef, character: cRef };
  const refOnly = [];
  if (pRef) refOnly.push({ url: pRef, role: "reference_image" });
  if (cRef) refOnly.push({ url: cRef, role: "reference_image" });
  const refUrls = [pRef, cRef].filter(Boolean);

  // ③ 用 gpt-image-2 把每个分镜画成"分镜图"（引用产品+人物参考图保持一致），并发
  job.phase = "③ 用 gpt-image-2 画分镜图";
  job.sheets = new Array(N).fill("");
  const sheetUrls = await Promise.all(scenes.map(function (sc, i) {
    return genImage("竖屏9:16分镜关键帧（第" + (i + 1) + "/" + N + "镜，电影感带货）：" + sc + "。务必保持与参考图完全相同的产品外观与人物（同脸/同发型/同服装），写实风格，无文字水印", job.gKey, refUrls)
      .then(function (u) { job.sheets[i] = u || ""; return u || ""; })
      .catch(function () { job.sheets[i] = ""; return ""; });
  }));

  // ④ 逐镜出片：Agnes 视频（并发上限=1 → 串行：每镜 create → 轮询完成 → 下一镜）；分镜图作故事板展示
  job.phase = "④ 逐镜生成视频（Agnes）";
  for (let v = 0; v < count; v++) {
    job.videos.push({ segs: scenes.map((sc, k) => ({ state: "pending", dur: durations[k], scene: sc || topic, sheet: sheetUrls[k] || "" })), out: undefined, state: "running" });
  }
  for (let vi = 0; vi < job.videos.length; vi++) {
    const vd = job.videos[vi];
    for (const seg of vd.segs) {
      let created = false;
      for (let a = 0; a < 12 && !created; a++) {
        const c = await agnesCreate(lock + " 本镜画面：" + seg.scene, seg.dur, job.kKey);
        if (c.taskId) { seg.taskId = c.taskId; seg.state = "running"; created = true; }
        else if (c.busy) { await new Promise((r) => setTimeout(r, 12000)); }
        else { seg.state = "failed"; seg.error = c.error; created = true; }
      }
      if (seg.state !== "running") { if (seg.state === "pending") { seg.state = "failed"; seg.error = "视频服务繁忙(并发占满)"; } continue; }
      for (let p = 0; p < 90; p++) {
        await new Promise((r) => setTimeout(r, 8000));
        const st = await agnesPoll(seg.taskId, job.kKey);
        if (st.done) { if (st.failed) { seg.state = "failed"; seg.error = st.error; } else { seg.state = "succeeded"; seg.url = st.url; } break; }
      }
      if (seg.state === "running") { seg.state = "failed"; seg.error = "轮询超时"; }
    }
    const okUrls = vd.segs.filter((s) => s.state === "succeeded" && s.url).map((s) => s.url);
    if (!okUrls.length) { vd.state = "failed"; vd.error = "全部分镜失败"; }
    else { job.phase = "⑤ 拼接 + 竖屏成片"; vd.out = (await finalizeVideo(job.id, vi, okUrls)) || okUrls[0]; vd.state = "succeeded"; }
  }
  job.state = job.videos.some((vd) => vd.state === "succeeded") ? "succeeded" : "failed";
  job.phase = job.state === "succeeded" ? "✅ 完成" : "❌ 失败";
  if (job.state === "succeeded" && !job.saved) { job.saved = true; saveWork({ id: job.id, ts: Date.now(), topic: job.topic, refs: job.refs, urls: job.videos.filter((vd) => vd.out).map((vd) => vd.out) }); }
  job.built = true;
}

async function refreshJob(job) {
  return; // Agnes 并发=1：生成已由 buildJob 串行驱动，/api/jobs 直接读 job 即可，无需轮询
  for (let vi = 0; vi < job.videos.length; vi++) {
    const video = job.videos[vi];
    if (video.state === "succeeded" || video.state === "failed") continue;
    // 轮询各段
    for (const s of video.segs) {
      if (s.state === "running" && s.taskId) {
        try { const r = await kz("/ai-open-platform-api/v1/lz/video/task/status", { task_id: s.taskId }, job.kKey); const d = (r.json && r.json.data) || {};
          if (d.status === "succeeded") { s.state = "succeeded"; s.url = d.video_url; }
          else if (d.status === "failed") { s.state = "failed"; s.error = d.error || "failed"; }
        } catch (e) { /* retry next */ }
      }
    }
    const allDone = video.segs.every((s) => s.state !== "running");
    if (allDone && video.state === "running") {
      const okUrls = video.segs.filter((s) => s.state === "succeeded").map((s) => s.url);
      if (!okUrls.length) { video.state = "failed"; video.error = "全部分段失败"; continue; }
      if (okUrls.length === 1) { video.out = okUrls[0]; video.state = "succeeded"; continue; }
      // 多段 → 下载 + ffmpeg 拼接
      video.state = "stitching"; job.phase = "④ 拼接成片";
      try {
        const dir = path.join(OUT, job.id + "_" + vi); fs.mkdirSync(dir, { recursive: true });
        const files = [];
        for (let i = 0; i < okUrls.length; i++) { const fp = path.join(dir, "seg" + i + ".mp4"); await download(okUrls[i], fp); files.push(fp); }
        const listFile = path.join(dir, "list.txt");
        fs.writeFileSync(listFile, files.map((f) => "file '" + f.replace(/\\/g, "/") + "'").join("\n"));
        const outFile = path.join(OUT, job.id + "_" + vi + ".mp4");
        // 先尝试无损 concat；失败再重编码归一化
        let rr = spawnSync(FFMPEG, ["-y", "-f", "concat", "-safe", "0", "-i", listFile, "-c", "copy", outFile], { encoding: "utf8" });
        if (rr.status !== 0 || !fs.existsSync(outFile)) {
          rr = spawnSync(FFMPEG, ["-y", "-f", "concat", "-safe", "0", "-i", listFile, "-vf", "scale=720:1280:force_original_aspect_ratio=decrease,pad=720:1280:(ow-iw)/2:(oh-ih)/2,setsar=1", "-r", "24", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", outFile], { encoding: "utf8" });
        }
        if (fs.existsSync(outFile)) { video.out = "/out/" + job.id + "_" + vi + ".mp4"; video.state = "succeeded"; }
        else { video.out = okUrls[0]; video.state = "succeeded"; video.note = "拼接失败，返回首段"; }
      } catch (e) { video.out = okUrls[0]; video.state = "succeeded"; video.note = "拼接异常：" + String((e && e.message) || e); }
    }
  }
  const allVideosDone = job.videos.every((vd) => vd.state === "succeeded" || vd.state === "failed");
  if (allVideosDone) {
    job.state = job.videos.some((vd) => vd.state === "succeeded") ? "succeeded" : "failed";
    job.phase = job.state === "succeeded" ? "✅ 完成" : "❌ 失败";
    if (job.state === "succeeded" && !job.saved) { job.saved = true; saveWork({ id: job.id, ts: Date.now(), topic: job.topic, refs: job.refs, urls: job.videos.filter((vd) => vd.out).map((vd) => vd.out) }); }
  }
}

const server = http.createServer(async (req, res) => {
  const send = (code, obj) => { res.writeHead(code, { "Content-Type": "application/json; charset=utf-8", "Access-Control-Allow-Origin": "*" }); res.end(JSON.stringify(obj)); };
  try {
    const u = new URL(req.url || "/", "http://localhost");
    const p = u.pathname;
    if (req.method === "GET" && p === "/api/config") return send(200, { backend: true, live: true, hasKey: !!KEY, autoScript: !!GW_KEY, serverHasKey: !!KEY, needUserKey: !KEY, github: process.env.UVG_GITHUB || "https://github.com/marketleonai-del/VIDEO-FACTORY" });
    if (req.method === "POST" && p === "/api/generate") {
      let body = ""; req.on("data", (c) => (body += c)); await new Promise((r) => req.on("end", r));
      const input = JSON.parse(body || "{}");
      const hasTopic = (input.prompt && input.prompt.trim()) || (input.product && input.product.trim()) || input.imageB64;
      if (!hasTopic) return send(400, { error: { message: "请输入描述，或上传一张产品图（可免描述自动写脚本）" } });
      if (!((input.kuaiziKey && input.kuaiziKey.trim()) || KEY)) return send(400, { error: { message: "请先在页面右上角填写你自己的丽帧 API Key（kz-…）" } });
      const job = createJob(input);
      return send(202, { jobId: job.id });
    }
    if (req.method === "GET" && p === "/api/works") {
      const host = req.headers.host || ("localhost:" + PORT);
      return send(200, loadWorks().map((w) => ({ id: w.id, ts: w.ts, topic: w.topic,
        refs: { product: w.refs && w.refs.product, character: w.refs && w.refs.character },
        urls: (w.urls || []).map((u) => /^https?:/.test(u) ? u : ("http://" + host + u)) })));
    }
    if (req.method === "GET" && p.startsWith("/api/jobs/")) {
      const job = jobs[p.slice("/api/jobs/".length)];
      if (!job) return send(404, { error: "not found" });
      await refreshJob(job);
      const segments = []; let gi = 0;
      job.videos.forEach((vd, vidx) => vd.segs.forEach((s) => segments.push({ index: gi++, video: vidx, state: s.state === "succeeded" ? "succeeded" : (s.state === "failed" ? "failed" : "running"), scene: s.scene || "", sheet: s.sheet || "" })));
      const urls = job.videos.filter((vd) => vd.state === "succeeded" && vd.out).map((vd) => /^https?:/.test(vd.out) ? vd.out : ("http://" + (req.headers.host || ("localhost:" + PORT)) + vd.out));
      return send(200, { state: job.state, phase: job.phase, storyboard: job.storyboard || [], segments, stitch: job.state === "succeeded" ? "succeeded" : "running", videoUrl: urls[0], videoUrls: urls, refs: job.refs, error: job.error });
    }
    // 技能包下载
    if (p === "/skill.zip") {
      const zip = path.join(__dirname, "..", "..", "video-factory-skill.zip");
      if (fs.existsSync(zip)) { res.writeHead(200, { "Content-Type": "application/zip", "Content-Disposition": "attachment; filename=video-factory-skill.zip", "Access-Control-Allow-Origin": "*" }); return fs.createReadStream(zip).pipe(res); }
      return send(404, { error: "技能包未生成（在项目目录先运行 _package.bat）" });
    }
    // 文档查看（白名单）
    if (p === "/doc") {
      const f = decodeURIComponent(u.searchParams.get("f") || "");
      const allow = ["MASTER/视频工厂-总纲.md", "MASTER/提示词总库.md", "MASTER/功能全景.md", "使用说明.md", "README.md"];
      if (allow.indexOf(f) >= 0) { const fp = path.join(__dirname, f); if (fs.existsSync(fp)) { res.writeHead(200, { "Content-Type": "text/plain; charset=utf-8", "Access-Control-Allow-Origin": "*" }); return res.end(fs.readFileSync(fp)); } }
      return send(404, { error: "not found" });
    }
    // 拼接产物
    if (p.startsWith("/out/")) {
      const fp = path.join(OUT, path.normalize(p.slice("/out/".length)).replace(/^(\.\.[/\\])+/, ""));
      if (fp.startsWith(OUT) && fs.existsSync(fp)) { res.writeHead(200, { "Content-Type": "video/mp4" }); return fs.createReadStream(fp).pipe(res); }
      return send(404, { error: "not found" });
    }
    // 静态
    const file = p === "/" ? "/index.html" : p;
    const fp = path.join(PUBLIC, path.normalize(file).replace(/^(\.\.[/\\])+/, ""));
    if (fp.startsWith(PUBLIC) && fs.existsSync(fp) && fs.statSync(fp).isFile()) {
      const ext = path.extname(fp); const ct = ext === ".html" ? "text/html" : ext === ".js" ? "text/javascript" : ext === ".css" ? "text/css" : "application/octet-stream";
      res.writeHead(200, { "Content-Type": ct + "; charset=utf-8" }); return res.end(fs.readFileSync(fp));
    }
    send(404, { error: "not found" });
  } catch (e) { send(500, { error: { message: String((e && e.message) || e) } }); }
});

server.listen(PORT, () => {
  console.log("==============================================");
  console.log("  视频工厂 · 实时后端 v2（丽帧出片 + 免描述脚本 + 长视频拼接）");
  console.log("  打开:  http://localhost:" + PORT);
  console.log("  KUAIZI_API_KEY: " + (KEY ? "OK" : "缺失") + "   网关LLM(免描述): " + (GW_KEY ? "OK" : "缺失"));
  console.log("==============================================");
});
