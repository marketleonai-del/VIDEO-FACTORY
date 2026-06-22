/**
 * Config.ts — 配置与密钥加载 + 输入校验（零依赖）
 * 从环境变量/.env 读取各家 API Key 与端点；并提供 WorkflowInput 校验。
 */
import * as fs from "node:fs";
import { WorkflowInput } from "../workflows/WorkflowContext";

/** 引擎装配所需配置（与 index.ts 的 EngineConfig 对齐的超集） */
export interface LoadedConfig {
  klingApiKey?: string;
  klingBaseUrl?: string;
  runwayApiKey?: string;
  runwayBaseUrl?: string;
  seedanceApiKey?: string;
  seedanceBaseUrl?: string;
  veoApiKey?: string;
  veoBaseUrl?: string;
  elevenLabsApiKey?: string;
  volcengineToken?: string;
  cosyVoiceBaseUrl?: string;
  cosyVoiceEnabled?: boolean;
  gptSoVitsBaseUrl?: string;
  gptSoVitsEnabled?: boolean;
  localTtsBaseUrl?: string;
  localTtsEnabled?: boolean;
  localTtsModel?: "moss" | "kokoro" | "piper";
  concurrency?: number;
  includeSkeletons?: boolean;
}

/** 极简 .env 解析（KEY=VALUE，# 注释；不依赖 dotenv） */
export function parseDotenv(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith("#")) continue;
    const i = line.indexOf("=");
    if (i < 0) continue;
    const k = line.slice(0, i).trim();
    let v = line.slice(i + 1).trim();
    if ((v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'"))) v = v.slice(1, -1);
    out[k] = v;
  }
  return out;
}

/** 把 .env 文件（若存在）合并进 process.env（已存在的不覆盖） */
export function loadDotenvFile(path = ".env"): void {
  try {
    if (fs.existsSync(path)) {
      const vars = parseDotenv(fs.readFileSync(path, "utf8"));
      for (const [k, v] of Object.entries(vars)) if (process.env[k] === undefined) process.env[k] = v;
    }
  } catch {
    /* 忽略 .env 读取错误 */
  }
}

/** 从环境变量构建配置 */
export function loadConfigFromEnv(env: NodeJS.ProcessEnv = process.env): LoadedConfig {
  const num = (s?: string): number | undefined => (s && !Number.isNaN(+s) ? +s : undefined);
  const bool = (s?: string): boolean | undefined => (s === undefined ? undefined : /^(1|true|yes|on)$/i.test(s));
  return {
    klingApiKey: env.KLING_API_KEY,
    klingBaseUrl: env.KLING_BASE_URL,
    runwayApiKey: env.RUNWAY_API_KEY,
    runwayBaseUrl: env.RUNWAY_BASE_URL,
    seedanceApiKey: env.SEEDANCE_API_KEY,
    seedanceBaseUrl: env.SEEDANCE_BASE_URL,
    veoApiKey: env.VEO_API_KEY,
    veoBaseUrl: env.VEO_BASE_URL,
    elevenLabsApiKey: env.ELEVENLABS_API_KEY,
    volcengineToken: env.VOLCENGINE_TOKEN,
    cosyVoiceBaseUrl: env.COSYVOICE_BASE_URL,
    cosyVoiceEnabled: bool(env.COSYVOICE_ENABLED),
    gptSoVitsBaseUrl: env.GPTSOVITS_BASE_URL,
    gptSoVitsEnabled: bool(env.GPTSOVITS_ENABLED),
    localTtsBaseUrl: env.LOCAL_TTS_BASE_URL,
    localTtsEnabled: bool(env.LOCAL_TTS_ENABLED),
    localTtsModel: env.LOCAL_TTS_MODEL as "moss" | "kokoro" | "piper" | undefined,
    concurrency: num(env.UVG_CONCURRENCY),
    includeSkeletons: bool(env.UVG_INCLUDE_SKELETONS),
  };
}

/** WorkflowInput 校验：返回错误列表（空=通过） */
export function validateWorkflowInput(input: Partial<WorkflowInput>): string[] {
  const errs: string[] = [];
  const modes = ["from-scratch", "from-materials", "from-winner"];
  if (!input.mode || !modes.includes(input.mode)) errs.push(`mode 必须是 ${modes.join("/")}`);
  const tiers = ["minimal", "standard", "premium"];
  if (!input.budgetTier || !tiers.includes(input.budgetTier)) errs.push(`budgetTier 必须是 ${tiers.join("/")}`);
  const platforms = ["douyin", "tiktok", "xiaohongshu", "videohao"];
  if (!input.platform || !platforms.includes(input.platform)) errs.push(`platform 必须是 ${platforms.join("/")}`);
  if (typeof input.matrixCount !== "number" || input.matrixCount < 1) errs.push("matrixCount 必须 ≥ 1");
  if (typeof input.durationSec !== "number" || input.durationSec <= 0) errs.push("durationSec 必须 > 0");
  if (!input.aspectRatio) errs.push("aspectRatio 必填");
  if (input.mode === "from-materials" && !input.hasOwnMaterials) errs.push("from-materials 模式需 hasOwnMaterials=true 并提供 materials");
  if (input.hasRealPersonAudio && !input.realPersonAudioSample) errs.push("hasRealPersonAudio=true 时需提供 realPersonAudioSample");
  return errs;
}
