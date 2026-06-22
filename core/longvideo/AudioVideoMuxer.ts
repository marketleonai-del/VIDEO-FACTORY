/**
 * AudioVideoMuxer.ts — 把 TTS 语音（+可选 BGM）配到（静音）视频上（ffmpeg）。
 * 对齐时长（apad 补/ -shortest 裁 / atempo 变速贴合）、归一化采样率、保持音画同步、BGM 音量混音。
 * 缺 ffmpeg 或远程/占位地址 → 优雅降级为输出命令计划。ffmpeg 标志依据见 references/TTS-SELECTION.md。
 */
import { spawnSync } from "node:child_process";
import { logger } from "../runtime";

export interface MuxOptions {
  /** 人声音量(1=原) */
  voiceVol?: number;
  /** 背景音乐路径（可选） */
  bgmPath?: string;
  /** BGM 音量(默认 0.2，压在人声下) */
  bgmVol?: number;
  /** 采样率 */
  sampleRate?: number;
  /** 时长贴合：pad=补静音 / trim=按短裁 / atempo=变速贴合视频 */
  fit?: "pad" | "trim" | "atempo";
  /** atempo 倍率（fit=atempo 时） */
  tempo?: number;
}
export interface MuxPlan {
  ffmpegAvailable: boolean;
  command: string[];
  notes: string[];
}
export interface MuxResult {
  muxed: boolean;
  outputPath: string;
  plan: MuxPlan;
}

export class AudioVideoMuxer {
  constructor(private ffmpegBin = process.env.FFMPEG_BIN || "ffmpeg") {}
  isAvailable(): boolean {
    try {
      return spawnSync(this.ffmpegBin, ["-version"], { encoding: "utf8" }).status === 0;
    } catch {
      return false;
    }
  }

  buildPlan(videoPath: string, voicePath: string, outputPath: string, opts: MuxOptions = {}): MuxPlan {
    const sr = opts.sampleRate ?? 44100;
    const vv = opts.voiceVol ?? 1;
    const fit = opts.fit ?? "pad";
    const notes: string[] = [];
    const cmd: string[] = ["-y", "-i", videoPath, "-i", voicePath];
    if (opts.bgmPath) cmd.push("-stream_loop", "-1", "-i", opts.bgmPath);

    // 人声链：音量 + 重采样 +（pad 补静音 / atempo 变速）
    let voiceChain = "[1:a]volume=" + vv + ",aresample=" + sr;
    if (fit === "pad") voiceChain += ",apad";
    if (fit === "atempo") voiceChain += ",atempo=" + (opts.tempo ?? 1);
    voiceChain += "[vv]";

    let filter: string;
    if (opts.bgmPath) {
      const bv = opts.bgmVol ?? 0.2;
      filter = voiceChain + ";[2:a]volume=" + bv + ",aresample=" + sr + ",apad[bg];[vv][bg]amix=inputs=2:duration=first[a]";
      notes.push("人声 + BGM 混音(amix, BGM 压低)");
    } else {
      filter = voiceChain.replace("[vv]", "[a]");
      notes.push("仅人声轨");
    }

    cmd.push("-filter_complex", filter, "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac");
    cmd.push("-shortest", outputPath); // 绑定到较短流，保 A/V 同步
    notes.push("时长贴合: " + fit + "；采样率 " + sr + "；-c:v copy 不重编码视频(无损失)");
    return { ffmpegAvailable: this.isAvailable(), command: cmd, notes };
  }

  render(videoPath: string, voicePath: string, outputPath: string, opts: MuxOptions = {}): MuxResult {
    const plan = this.buildPlan(videoPath, voicePath, outputPath, opts);
    const remote = [videoPath, voicePath, opts.bgmPath || ""].some((u) => /^https?:\/\/|^demo:\/\//.test(u));
    if (!plan.ffmpegAvailable || remote) {
      if (remote) plan.notes.push("含远程/占位地址：生产中先下载为本地文件再合成。");
      if (!plan.ffmpegAvailable) plan.notes.push("ffmpeg 不可用：输出命令计划，装 ffmpeg 后可真合成。");
      return { muxed: false, outputPath, plan };
    }
    const r = spawnSync(this.ffmpegBin, plan.command, { encoding: "utf8" });
    if (r.status !== 0) logger.warn("mux 失败", { err: (r.stderr || "").slice(0, 300) });
    return { muxed: r.status === 0, outputPath, plan };
  }
}
