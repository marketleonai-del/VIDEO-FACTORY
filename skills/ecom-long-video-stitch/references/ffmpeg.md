# FFmpeg 拼接实操（后期层）

顺序固定：探测 → 统一转码 → 拼接 → 音频 → QC。跳过统一转码直接 concat 是新手最常见的"技术性拼接感"来源（帧率/时间基不一致导致接缝处卡一下或音画漂移）。

优先用 `scripts/stitch.py` 自动完成前四步；本文件是它背后的原理和手动命令，用于自定义或排障。

## 0. 探测各段参数

```bash
for f in clips/*.mp4; do
  echo "== $f"; ffprobe -v error -select_streams v:0 \
    -show_entries stream=width,height,r_frame_rate,pix_fmt,codec_name \
    -show_entries format=duration -of default=noprint_wrappers=1 "$f"
done
```
关注：分辨率、帧率（30/1 与 30000/1001 不是一回事）、pix_fmt、时长。任何一项不一致都必须先转码统一。

## 1. 统一转码（标准化）

统一到同一分辨率/帧率/像素格式/音频规格（AI 生成段常常没有音轨或规格不一，统一补齐静音轨）：

```bash
mkdir -p norm
for f in clips/*.mp4; do
  ffmpeg -y -i "$f" -f lavfi -i anullsrc=r=48000:cl=stereo -shortest \
    -map 0:v:0 -map "0:a:0?" -map 1:a:0? \
    -vf "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2,fps=30,format=yuv420p" \
    -c:v libx264 -preset medium -crf 17 -c:a aac -ar 48000 -b:a 192k \
    "norm/$(basename "$f")"
done
```
（竖版 1080×1920/30fps 为例；横版换 1920×1080。crf 17 接近无损，中间工序不要用高压缩。）

**掐掉结尾冻结帧**：AI 段结尾常有 0.3-1 秒冻结。转码时加 `-t <可用时长>` 截掉，或先用
`ffmpeg -sseof -1.5 -i seg.mp4 -vf "select=gt(scene\,0.001)" ...` 观察运动停止点，人工确定各段 trim 时长。动作匹配的重叠区也在这一步裁：A 段掐掉重叠后半，B 段掐掉重叠前半，切点选运动模糊最大帧。

```bash
# 精确裁剪：保留 segA 的 0 到 9.2 秒，丢弃后面
ffmpeg -y -i norm/segA.mp4 -t 9.2 -c copy cut/segA.mp4
# B 段从 0.8 秒开始保留（丢弃重叠前半）
ffmpeg -y -i norm/segB.mp4 -ss 0.8 -c:v libx264 -crf 17 -c:a aac cut/segB.mp4
```
注意：`-ss` 放在 `-i` 之后并重编码，保证帧精确；`-c copy` 只能按关键帧切，用于掐尾巴没问题，掐头必须重编码。

## 2a. 硬切拼接（默认，最快）

设计层做好了（景别阶梯+转场手法），硬切就是最干净的拼法：

```bash
printf "file '%s'\n" "$(pwd)"/cut/*.mp4 > list.txt
ffmpeg -y -f concat -safe 0 -i list.txt -c copy joined.mp4
```
所有段已统一参数时 `-c copy` 无损且零缝隙。若 concat 后接缝处卡顿，说明标准化没做干净，回到步骤 1。

## 2b. 微溶解拼接（xfade，掩盖残余光色差）

接缝处 0.12-0.2 秒（4-6 帧 @30fps）的交叉溶解，低于肉眼"转场感"阈值，但能揉掉轻微光色跳变。**超过 0.25 秒就会看出叠影，变成廉价转场效果，切忌。**

两段示例（多段用 stitch.py 自动生成滤镜链）：
```bash
# A 段时长 9.2s，溶解 0.15s → offset = 9.2 - 0.15 = 9.05
ffmpeg -y -i cut/segA.mp4 -i cut/segB.mp4 -filter_complex \
"[0:v][1:v]xfade=transition=fade:duration=0.15:offset=9.05[v];\
 [0:a][1:a]acrossfade=d=0.15[a]" \
-map "[v]" -map "[a]" -c:v libx264 -crf 17 -c:a aac out.mp4
```

**模拟甩镜**（生成端甩镜失败时的补救）：`xfade=transition=slideleft:duration=0.15`（方向与两段甩动方向一致：向右甩用 slideright）。加一点模糊更像：先对 A 末 B 首各 3 帧加 `boxblur`，再 slideleft。

**遮挡切补救**（AI 没给足全遮帧）：接缝处插 2 帧黑场，比露出半遮挡帧更自然：
```bash
ffmpeg -y -f lavfi -i color=black:s=1080x1920:r=30:d=0.066 -f lavfi -i anullsrc=r=48000:cl=stereo -shortest -c:v libx264 -crf 17 -c:a aac -pix_fmt yuv420p black2f.mp4
# 然后把 black2f.mp4 插入 list.txt 对应位置硬切拼接
```

## 3. 音频（J/L-cut 与整轨 BGM）

原则：**成片的声音不用各段自带音频拼**。口播、BGM、音效单独成轨整条铺，画面切点和声音事件错开，这就是 J/L-cut 的后期实现。

```bash
# 整轨替换：joined.mp4 的画面 + 单独录制/合成的整条口播 + BGM
ffmpeg -y -i joined.mp4 -i voiceover.mp3 -i bgm.mp3 -filter_complex \
"[1:a]adelay=300|300,loudnorm=I=-16:TP=-1.5[vo];\
 [2:a]volume=0.18,afade=t=in:d=0.5,afade=t=out:st=57:d=3[bg];\
 [vo][bg]amix=inputs=2:duration=first:dropout_transition=0[a]" \
-map 0:v -map "[a]" -c:v copy -c:a aac -b:a 192k final.mp4
```
- `adelay=300`：口播比画面晚 0.3 秒进（J-cut 感）；跨段句的口播天然实现 L-cut；
- BGM 音量 0.15-0.25，`afade` 首尾淡入淡出；
- 需要环境音连续时，再铺一条低音量 room tone / 环境底噪（-30dB 左右），接缝的"空气感"就连上了；
- 各段自带音效（ASMR、开盖声）值得保留时，用 `amix` 把该段音轨低权重混入，不要独占。

**卡点（节奏切）**：确定 BGM 的 BPM 后，把步骤 1 的各段 trim 时长调整为拍长整数倍（BPM 120 → 拍长 0.5s → 段长 9.0/9.5/10.0…），拼出来的切点自动落拍。

## 4. 接缝 QC（交付前必做）

抽每个拼接点前后各 0.2 秒的帧，拼图对比：

```bash
# 接缝在 9.05s：抽 8.85-9.25s 的 12 帧
ffmpeg -y -ss 8.85 -t 0.4 -i final.mp4 -vf "fps=30,scale=270:-1" qc/seam1_%02d.jpg
ffmpeg -y -pattern_type glob -i 'qc/seam1_*.jpg' -filter_complex "tile=6x2" -frames:v 1 -update 1 qc/seam1_grid.jpg
```
逐张检查：人脸/服装/产品是否一致、光色是否连续、动作是否顺滑、有没有闪帧。全部接缝过检后再看一遍全片（1 倍速 + 1.5 倍速各一遍，倍速下跳变更显眼）。

## 5. 常见症状排查

| 症状 | 原因 | 解法 |
|---|---|---|
| 接缝处卡一下/顿帧 | 帧率或时间基不一致 | 回步骤 1 统一 fps 和 `-video_track_timescale` |
| 接缝处闪一帧黑/绿 | 某段末尾有坏帧 | trim 时多掐 1-2 帧 |
| 音画越到后面越不同步 | 各段音频采样率不一致被 concat | 步骤 1 统一 48000Hz；或整轨替换音频 |
| 色调忽冷忽暖 | 生成端光线锁没锁住 | 对跳变段做色彩匹配：`colorbalance`/`curves` 或统一过一个 LUT |
| 溶解处有叠影 | xfade 时长过长 | 降到 0.15s 以下，或改硬切 |
| 画面比例跳 | 有段是 9:16 有段是 3:4 | 步骤 1 的 scale+pad 统一画布 |
