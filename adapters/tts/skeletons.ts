/**
 * skeletons.ts — P1 TTS 骨架适配器（能力画像完整，调用待接入）
 */
import { SkeletonTTSProvider, baseCloudTTSCaps } from "./skeletonBase";

export const GeminiTTSAdapter = new SkeletonTTSProvider("gemini-tts", "Gemini TTS（Google）", baseCloudTTSCaps(["en", "zh", "ja"], 0.015, false), "P1");
export const OpenAITTSAdapter = new SkeletonTTSProvider("openai-tts", "OpenAI TTS", baseCloudTTSCaps(["en", "zh"], 0.015, false), "P1");

export const TTS_SKELETONS = [GeminiTTSAdapter, OpenAITTSAdapter];
