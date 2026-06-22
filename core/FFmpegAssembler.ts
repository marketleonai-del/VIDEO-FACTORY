/**
 * FFmpegAssembler.ts — 真实视频组装（ffmpeg）
 * 把"组装清单"（AI钩子 + 真素材，全部静音）+ 单一 @voice1 音轨 → 合成最终 mp4。
 * ffmpeg 是系统二进制（非 npm 依赖）；未安装时优雅降级为"输出命令计划 + 说明"。
 */
import { spawnSync } from "node:child_process";
import { logger } from "./runtime";

export interface AssemblyClip {
  kind: "ai-hook" | "ai-broll" | "real";
  /** 视频地址/本地路径（real 用 materialId 时由上层映射为路径） */
  url?: string;
  materialId?: string;
  muted: boolean;
}
export interface AssemblyManifest {
  order: AssemblyClip[];
  /** 单一 @voice1 音轨（一条或分句，按序拼） */
  audioTrack?: string[];
  note?: string;
}
export interface RenderPlan {
  ffmpegAvailable: boolean;
  inputs: string[];
  /** 可直接执行的 ffmpeg 命令（参数数组） */
  command: string[];
  notes: string[];
}
export interface RenderResult {
  rendered: boolean;
  outputPath: string;
  plan: RenderPlan;
}

export class FFmpegAssembler {
  constructor(private ffmpegBin = process.env.FFMPEG_BIN || "ffmpeg") {}

  /** 检查 ffmpeg 是否可用 */
  isAvailable(): boolean {
    try {
      const r = spawnSync(this.ffmpegBin, ["-version"], { encoding: "utf8" });
      return r.status === 0;
    } catch {
      return false;
    }
  }

  /** 仅构建渲染计划（不执行）——便于审阅/在别处执行 */
  buildPlan(manifest: AssemblyManifest, outputPath: string): RenderPlan {
    const notes: string[] = [];
    const inputs: string[] = [];
    manifest.order.forEach((c) => {
      const src = c.url ?? c.materialId ?? "";
      if (src) inputs.push(src);
      if (!c.muted) notes.push(`警告：片段 ${src} 未标记静音；混剪应全部静音，音轨统一用 @voice1`);
    });
    const audio = manifest.audioTrack && manifest.audioTrack.length ? manifest.audioTrack[0] : undefined;

    // 计划：所有视频片段去音 → concat → 叠加单一 @voice1 音轨
    const command = ["-y"];
    inputs.forEach((i) => command.push("-i", i));
    if (audio) command.push("-i", audio);
    const n = inputs.length;
    const vConcat = inputs.map((_, i) => `[${i}:v]`).join("") + `concat=n=${n}:v=1:a=0[outv]`;
    command.push("-filter_complex", vConcat);
    command.push("-map", "[outv]");
    if (audio) command.push("-map", `${n}:a`, "-shortest");
    command.push("-c:v", "libx264", "-pix_fmt", "yuv420p", outputPath);

    notes.push("所有视频片段静音后拼接；最终只保留 @voice1 单一音轨 → 全片音色一致");
    if (!audio) notes.push("未提供音轨：将输出无声成片，请补 @voice1 旁白音频");
    return { ffmpegAvailable: this.isAvailable(), inputs, command, notes };
  }

  /** 真实渲染：ffmpeg 可用且输入就绪则执行；否则返回计划（降级） */
  render(manifest: AssemblyManifest, outputPath: string): RenderResult {
    const plan = this.buildPlan(manifest, outputPath);
    if (!plan.ffmpegAvailable) {
      logger.warn("ffmpeg 不可用，降级为输出渲染计划。安装 ffmpeg 后即可真实合成。");
      return { rendered: false, outputPath, plan };
    }
    const hasRemote = plan.inputs.some((i) => /^https?:\/\/|^demo:\/\//.test(i));
    if (hasRemote || plan.inputs.length === 0) {
      plan.notes.push("输入含远程/占位地址：请先下载为本地文件再渲染（生产中由下载步骤完成）。");
      return { rendered: false, outputPath, plan };
    }
    const r = spawnSync(this.ffmpegBin, plan.command, { encoding: "utf8" });
    if (r.status !== 0) {
      logger.error("ffmpeg 渲染失败：", r.stderr?.slice(0, 400));
      return { rendered: false, outputPath, plan };
    }
    logger.info("已渲染：", outputPath);
    return { rendered: true, outputPath, plan };
  }
}
