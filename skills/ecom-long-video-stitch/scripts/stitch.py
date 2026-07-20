#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""带货长视频拼接器：探测 -> 统一转码 -> 硬切/微溶解拼接 -> BGM -> 接缝QC。

用法：
  python stitch.py clips/ -o final.mp4                          # 硬切
  python stitch.py a.mp4 b.mp4 c.mp4 -o final.mp4 \
      --transition dissolve --dissolve 0.15                     # 微溶解
  python stitch.py clips/ -o final.mp4 --bgm bgm.mp3 --bgm-volume 0.18
  python stitch.py clips/ -o final.mp4 --trims trims.txt --qc   # 裁剪+QC拼图

trims.txt 每行：文件名,起始秒,结束秒   （用于掐结尾冻结帧/动作重叠区，留空项写 -）
  例：seg02.mp4,0.8,-      表示 seg02 从 0.8s 开始保留到结尾
"""
import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

def run(cmd, quiet=True):
    r = subprocess.run(cmd, capture_output=quiet, text=True)
    if r.returncode != 0:
        sys.exit(f"命令失败: {' '.join(map(str, cmd))}\n{(r.stderr or '')[-2000:]}")
    return r

def probe(path):
    r = run(["ffprobe", "-v", "error", "-print_format", "json",
             "-show_streams", "-show_format", str(path)])
    info = json.loads(r.stdout)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    return {"w": int(v["width"]), "h": int(v["height"]),
            "dur": float(info["format"]["duration"])}

def collect_clips(inputs):
    files = []
    for x in inputs:
        p = Path(x)
        if p.is_dir():
            files += sorted(q for q in p.iterdir() if q.suffix.lower() in
                            {".mp4", ".mov", ".webm", ".mkv"})
        else:
            files.append(p)
    if len(files) < 2:
        sys.exit("至少需要 2 段视频")
    return files

def load_trims(path):
    trims = {}
    if not path:
        return trims
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, ss, to = [t.strip() for t in line.split(",")]
        trims[name] = (None if ss in ("-", "") else float(ss),
                       None if to in ("-", "") else float(to))
    return trims

def normalize(files, workdir, size, fps, trims):
    """统一分辨率/帧率/像素格式/音频规格；按 trims 裁剪。返回 (路径, 时长) 列表。"""
    w, h = size
    vf = (f"[0:v]scale={w}:{h}:force_original_aspect_ratio=decrease,"
          f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,fps={fps},format=yuv420p[v]")
    out = []
    for i, f in enumerate(files):
        dst = workdir / f"n{i:02d}.mp4"
        ss, to = trims.get(f.name, (None, None))
        aud = has_audio(f)
        cmd = ["ffmpeg", "-y", "-v", "error"]
        if ss:
            cmd += ["-ss", str(ss)]
        cmd += ["-i", str(f)]
        if not aud:
            cmd += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
        if to:  # -ss 前置后时间戳归零，改用时长限制保证帧精确
            cmd += ["-t", str(round(to - (ss or 0.0), 3))]
        cmd += ["-filter_complex", vf, "-map", "[v]"]
        cmd += ["-map", "0:a:0"] if aud else ["-map", "1:a", "-shortest"]
        cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "17",
                "-video_track_timescale", "15360",
                "-c:a", "aac", "-ar", "48000", "-b:a", "192k", str(dst)]
        run(cmd)
        out.append((dst, probe(dst)["dur"]))
        print(f"  标准化 {f.name} -> {dst.name} ({out[-1][1]:.2f}s)")
    return out

def has_audio(path):
    r = run(["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path)])
    return bool(r.stdout.strip())

def concat_cut(norm, workdir, dst):
    lst = workdir / "list.txt"
    lst.write_text("".join(f"file '{p.resolve()}'\n" for p, _ in norm), encoding="utf-8")
    run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
         "-i", str(lst), "-c", "copy", str(dst)])

def concat_dissolve(norm, dst, d):
    """xfade + acrossfade 链。溶解时长 d 建议 0.12-0.2s，勿超 0.25s。"""
    inputs, fc = [], []
    for p, _ in norm:
        inputs += ["-i", str(p)]
    offsets, acc = [], 0.0
    for i, (_, dur) in enumerate(norm[:-1]):
        acc += dur - d
        offsets.append(round(acc, 3))
    vprev, aprev = "[0:v]", "[0:a]"
    for i in range(1, len(norm)):
        vout = f"[v{i}]" if i < len(norm) - 1 else "[vout]"
        aout = f"[a{i}]" if i < len(norm) - 1 else "[aout]"
        fc.append(f"{vprev}[{i}:v]xfade=transition=fade:duration={d}:offset={offsets[i-1]}{vout}")
        fc.append(f"{aprev}[{i}:a]acrossfade=d={d}{aout}")
        vprev, aprev = vout, aout
    run(["ffmpeg", "-y", "-v", "error", *inputs,
         "-filter_complex", ";".join(fc),
         "-map", "[vout]", "-map", "[aout]",
         "-c:v", "libx264", "-preset", "medium", "-crf", "17",
         "-c:a", "aac", "-b:a", "192k", str(dst)])
    return offsets

def add_bgm(src, dst, bgm, vol):
    run(["ffmpeg", "-y", "-v", "error", "-i", str(src), "-i", str(bgm),
         "-filter_complex",
         f"[1:a]volume={vol},afade=t=in:d=0.5[bg];"
         "[0:a][bg]amix=inputs=2:duration=first:dropout_transition=0[a]",
         "-map", "0:v", "-map", "[a]", "-c:v", "copy",
         "-c:a", "aac", "-b:a", "192k", str(dst)])

def qc_grids(final, seams, outdir, fps):
    outdir.mkdir(exist_ok=True)
    for k, t in enumerate(seams, 1):
        start = max(t - 0.2, 0)
        pat = outdir / f"seam{k}_%02d.jpg"
        run(["ffmpeg", "-y", "-v", "error", "-ss", f"{start:.3f}", "-t", "0.4",
             "-i", str(final), "-vf", f"fps={fps},scale=270:-1", str(pat)])
        frames = sorted(outdir.glob(f"seam{k}_*.jpg"))
        if frames:
            run(["ffmpeg", "-y", "-v", "error", "-pattern_type", "glob",
                 "-i", str(outdir / f"seam{k}_*.jpg"),
                 "-filter_complex", f"tile={min(len(frames),6)}x2",
                 "-frames:v", "1", "-update", "1", str(outdir / f"seam{k}_grid.jpg")])
        print(f"  QC 拼图: {outdir}/seam{k}_grid.jpg (接缝 {t:.2f}s)")

def main():
    ap = argparse.ArgumentParser(description="带货长视频拼接器")
    ap.add_argument("inputs", nargs="+", help="片段目录或文件列表（按文件名排序）")
    ap.add_argument("-o", "--output", default="final.mp4")
    ap.add_argument("--transition", choices=["cut", "dissolve"], default="cut")
    ap.add_argument("--dissolve", type=float, default=0.15, help="溶解时长秒，<=0.25")
    ap.add_argument("--size", default="1080x1920", help="目标分辨率，如 1080x1920")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--bgm", help="BGM 音频文件（整轨铺底）")
    ap.add_argument("--bgm-volume", type=float, default=0.18)
    ap.add_argument("--trims", help="裁剪表 trims.txt（掐冻结帧/动作重叠区）")
    ap.add_argument("--qc", action="store_true", help="输出接缝 QC 拼图")
    a = ap.parse_args()

    if a.dissolve > 0.25:
        print("警告：溶解超过 0.25s 会出现叠影转场感，已压到 0.2s")
        a.dissolve = 0.2
    files = collect_clips(a.inputs)
    w, h = map(int, a.size.lower().split("x"))
    out = Path(a.output)
    with tempfile.TemporaryDirectory() as td:
        wd = Path(td)
        print(f"[1/4] 标准化 {len(files)} 段到 {w}x{h}@{a.fps} ...")
        norm = normalize(files, wd, (w, h), a.fps, load_trims(a.trims))
        durs = [d for _, d in norm]
        print(f"[2/4] 拼接（{a.transition}）...")
        joined = wd / "joined.mp4"
        if a.transition == "cut":
            concat_cut(norm, wd, joined)
            seams, acc = [], 0.0
            for d in durs[:-1]:
                acc += d
                seams.append(acc)
        else:
            seams = concat_dissolve(norm, joined, a.dissolve)
        print(f"[3/4] 音频 ...")
        if a.bgm:
            add_bgm(joined, out, a.bgm, a.bgm_volume)
        else:
            run(["ffmpeg", "-y", "-v", "error", "-i", str(joined), "-c", "copy", str(out)])
        print(f"[4/4] 完成: {out}  总时长约 {sum(durs):.1f}s")
        if a.qc:
            qc_grids(out, seams, out.parent / "qc", a.fps)
            print("请逐张检查 QC 拼图：人脸/服装/产品一致、光色连续、动作顺滑、无闪帧")

if __name__ == "__main__":
    main()
