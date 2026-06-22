/**
 * LongFormAssembler.ts — 长视频拼接（归一化分辨率/帧率 + concat 或 xfade），基于 ffmpeg。
 * 缺 ffmpeg 或含远程/占位地址时优雅降级为输出命令计划。
 */
import { spawnSync } from "node:child_process";

export interface LongFormOptions {
  fps?: number;
  width?: number;
  height?: number;
  transition?: "hard" | "xfade";
  xfadeDur?: number;
  audioTrack?: string; // 单一 @voice1 音轨
}
export interface LongFormPlan {
  ffmpegAvailable: boolean;
  inputs: string[];
  command: string[];
  notes: string[];
}

export class LongFormAssembler {
  constructor(private ffmpegBin = process.env.FFMPEG_BIN || "ffmpeg") {}
  isAvailable(): boolean {
    try {
      return spawnSync(this.ffmpegBin, ["-version"], { encoding: "utf8" }).status === 0;
    } catch {
      return false;
    }
  }

  /** 构建归一化 + 拼接命令。clips 为有序片段（本地路径）；durations 仅 xfade 需要 */
  buildPlan(clips: string[], outputPath: string, opts: LongFormOptions = {}, durations: number[] = []): LongFormPlan {
    const fps = opts.fps ?? 30;
    const w = opts.width ?? 720;
    const h = opts.height ?? 1280;
    const notes: string[] = [];
    const cmd: string[] = ["-y"];
    clips.forEach((c) => cmd.push("-i", c));
    if (opts.audioTrack) cmd.push("-i", opts.audioTrack);

    // 每段归一化：scale+pad 到统一尺寸、统一 fps 与 SAR
    const norm = clips.map((_, i) => `[${i}:v]scale=${w}:${h}:force_original_aspect_ratio=decrease,pad=${w}:${h}:-1:-1,fps=${fps},setsar=1[v${i}]`);
    let filter = norm.join(";");
    const n = clips.length;

    if (opts.transition === "xfade" && n >= 2) {
      const d = opts.xfadeDur ?? 0.5;
      // 链式 xfade：offset = 累计时长 - 累计淡入
      let prev = `[v0]`;
      let offset = (durations[0] ?? opts.fps ?? 3) - d;
      for (let i = 1; i < n; i++) {
        const out = i === n - 1 ? "[outv]" : `[x${i}]`;
        filter += `;${prev}[v${i}]xfade=transition=fade:duration=${d}:offset=${offset.toFixed(2)}${out}`;
        prev = out;
        offset += (durations[i] ?? 3) - d;
      }
      notes.push("xfade 链式交叉淡变");
    } else {
      filter += ";" + clips.map((_, i) => `[v${i}]`).join("") + `concat=n=${n}:v=1:a=0[outv]`;
      notes.push("concat 硬拼（已统一分辨率/帧率/SAR）");
    }
    cmd.push("-filter_complex", filter, "-map", "[outv]");
    if (opts.audioTrack) cmd.push("-map", `${n}:a`, "-shortest");
    cmd.push("-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", String(fps), outputPath);

    notes.push(`归一化到 ${w}x${h}@${fps}fps；段数 ${n}`);
    return { ffmpegAvailable: this.isAvailable(), inputs: clips, command: cmd, notes };
  }

  render(clips: string[], outputPath: string, opts: LongFormOptions = {}, durations: number[] = []): { rendered: boolean; outputPath: string; plan: LongFormPlan } {
    const plan = this.buildPlan(clips, outputPath, opts, durations);
    const hasRemote = clips.some((c) => /^https?:\/\/|^demo:\/\//.test(c));
    if (!plan.ffmpegAvailable || hasRemote || clips.length === 0) {
      if (hasRemote) plan.notes.push("含远程/占位地址：生产中先下载为本地文件再渲染。");
      if (!plan.ffmpegAvailable) plan.notes.push("ffmpeg 不可用：已输出命令计划，装 ffmpeg 后可真渲染。");
      return { rendered: false, outputPath, plan };
    }
    const r = spawnSync(this.ffmpegBin, plan.command, { encoding: "utf8" });
    return { rendered: r.status === 0, outputPath, plan };
  }
}
