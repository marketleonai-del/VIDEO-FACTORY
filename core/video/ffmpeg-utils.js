/**
 * FFmpegUtils - 视频处理工具集
 * 纯Node调用ffmpeg，零依赖，支持竖屏/横屏视频处理
 *
 * @author VIDEO-FACTORY
 * @description 提供视频归一化、裁剪、拼接、转场、音频处理等核心能力
 */
const { execSync } = require('child_process');
const fs = require('fs');
const path = require('path');

class FFmpegUtils {
  constructor(ffmpegPath = 'ffmpeg', ffprobePath = 'ffprobe') {
    this.ffmpeg = ffmpegPath;
    this.ffprobe = ffprobePath;
  }

  /** 检查ffmpeg/ffprobe是否可用 */
  check() {
    try { execSync(`${this.ffmpeg} -version`, { stdio: 'ignore' }); return true; }
    catch { return false; }
  }

  /** 使用ffprobe获取视频详细信息 */
  probe(filePath) {
    if (!fs.existsSync(filePath)) throw new Error(`[FFmpegUtils] 文件不存在: ${filePath}`);
    try {
      const cmd = `${this.ffprobe} -v quiet -print_format json -show_format -show_streams "${filePath}"`;
      return JSON.parse(execSync(cmd, { encoding: 'utf8', timeout: 30000 }));
    } catch (e) { throw new Error(`[FFmpegUtils] probe失败: ${e.message}`); }
  }

  /**
   * 归一化视频 - 统一分辨率/帧率/编码，多视频拼接前的标准化
   * @param {object} options - {width=720, height=1280, fps=24, codec='libx264', preset='fast', crf=23}
   */
  normalize(input, output, options = {}) {
    const { width = 720, height = 1280, fps = 24, codec = 'libx264', preset = 'fast', crf = 23 } = options;
    const vf = `scale=${width}:${height}:force_original_aspect_ratio=increase,crop=${width}:${height},fps=${fps},format=yuv420p`;
    const cmd = `${this.ffmpeg} -y -i "${input}" -vf "${vf}" -c:v ${codec} -preset ${preset} -crf ${crf} -an -movflags +faststart "${output}"`;
    try { execSync(cmd, { timeout: 120000, stdio: 'pipe' }); return output; }
    catch (e) { throw new Error(`[FFmpegUtils] 归一化失败: ${e.message}`); }
  }

  /** 裁剪视频时间段（流式拷贝，极速） */
  trim(input, output, start, duration) {
    const s = typeof start === 'number' ? start.toFixed(3) : start;
    const d = typeof duration === 'number' ? duration.toFixed(3) : duration;
    const cmd = `${this.ffmpeg} -y -ss ${s} -t ${d} -i "${input}" -c copy -avoid_negative_ts make_zero "${output}"`;
    try { execSync(cmd, { timeout: 60000, stdio: 'pipe' }); return output; }
    catch (e) { throw new Error(`[FFmpegUtils] 裁剪失败: ${e.message}`); }
  }

  /** 精确裁剪（重新编码，关键帧精确） */
  trimPrecise(input, output, start, duration) {
    const s = typeof start === 'number' ? start.toFixed(3) : start;
    const d = typeof duration === 'number' ? duration.toFixed(3) : duration;
    const cmd = `${this.ffmpeg} -y -i "${input}" -ss ${s} -t ${d} -c:v libx264 -preset fast -crf 23 -c:a aac -movflags +faststart "${output}"`;
    try { execSync(cmd, { timeout: 120000, stdio: 'pipe' }); return output; }
    catch (e) { throw new Error(`[FFmpegUtils] 精确裁剪失败: ${e.message}`); }
  }

  /** concat协议拼接多视频（编码须一致，建议先normalize） */
  concat(inputs, output) {
    if (!inputs || inputs.length === 0) throw new Error('[FFmpegUtils] concat输入为空');
    if (inputs.length === 1) { fs.copyFileSync(inputs[0], output); return output; }
    const listFile = path.join(path.dirname(output), `concat_${Date.now()}.txt`);
    fs.writeFileSync(listFile, inputs.map(f => `file '${path.resolve(f)}'`).join('\n'));
    try {
      const cmd = `${this.ffmpeg} -y -f concat -safe 0 -i "${listFile}" -c copy -movflags +faststart "${output}"`;
      execSync(cmd, { timeout: 180000, stdio: 'pipe' }); return output;
    } catch (e) { throw new Error(`[FFmpegUtils] concat拼接失败: ${e.message}`); }
    finally { if (fs.existsSync(listFile)) fs.unlinkSync(listFile); }
  }

  /**
   * xfade转场拼接多视频（平滑过渡）
   * @param {string[]} inputs    - 输入视频路径数组（至少2个）
   * @param {string} transition  - 转场类型: fade/dissolve/wipeleft/slideleft 等
   * @param {number} duration    - 转场持续时间(秒)
   * @param {object} options     - {width=720, height=1280, fps=24}
   */
  concatWithTransition(inputs, output, transition = 'fade', duration = 0.5, options = {}) {
    if (!inputs || inputs.length < 2) throw new Error('[FFmpegUtils] xfade至少需要2个视频');
    const { width = 720, height = 1280, fps = 24 } = options;

    // 归一化所有视频
    const normalized = inputs.map((input, idx) => {
      const tmp = path.join(path.dirname(output), `norm_${Date.now()}_${idx}.mp4`);
      this.normalize(input, tmp, { width, height, fps }); return tmp;
    });

    try {
      const durations = normalized.map(f => this.getDuration(f));
      let filterComplex = '', prevLabel = '0:v';

      for (let i = 1; i < normalized.length; i++) {
        const isLast = i === normalized.length - 1;
        const outLabel = isLast ? 'out' : `tmp${i}`;
        const offset = durations.slice(0, i).reduce((a, b) => a + b, 0) - duration * i;
        filterComplex += `[${prevLabel}][${i}:v]xfade=transition=${transition}:duration=${duration}:offset=${Math.max(0.1, offset).toFixed(3)}[${outLabel}];`;
        prevLabel = outLabel;
      }

      const inputArgs = normalized.map(f => `-i "${f}"`).join(' ');
      const cmd = `${this.ffmpeg} -y ${inputArgs} -filter_complex "${filterComplex}" -map [out] -c:v libx264 -preset fast -crf 23 -movflags +faststart "${output}"`;
      execSync(cmd, { timeout: 300000, stdio: 'pipe' }); return output;
    } catch (e) { throw new Error(`[FFmpegUtils] xfade转场失败: ${e.message}`); }
    finally { normalized.forEach(f => { if (fs.existsSync(f)) fs.unlinkSync(f); }); }
  }

  /** 叠加音频到视频（替换原音频） */
  addAudio(video, audio, output) {
    const cmd = `${this.ffmpeg} -y -i "${video}" -i "${audio}" -c:v copy -c:a aac -b:a 192k -map 0:v:0 -map 1:a:0 -shortest "${output}"`;
    try { execSync(cmd, { timeout: 60000, stdio: 'pipe' }); return output; }
    catch (e) { throw new Error(`[FFmpegUtils] 音频叠加失败: ${e.message}`); }
  }

  /** 混合音频到视频（保留原音频+叠加新音频） */
  mixAudio(video, audio, output, audioVolume = 1.0) {
    const cmd = `${this.ffmpeg} -y -i "${video}" -i "${audio}" -filter_complex "[0:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo[main];[1:a]aformat=sample_fmts=fltp:sample_rates=44100:channel_layouts=stereo,volume=${audioVolume}[over];[main][over]amix=inputs=2:duration=first[aout]" -map 0:v -map [aout] -c:v copy -c:a aac -b:a 192k -shortest "${output}"`;
    try { execSync(cmd, { timeout: 60000, stdio: 'pipe' }); return output; }
    catch (e) { throw new Error(`[FFmpegUtils] 音频混合失败: ${e.message}`); }
  }

  /** 移除视频音频轨道 */
  mute(input, output) {
    const cmd = `${this.ffmpeg} -y -i "${input}" -c:v copy -an -movflags +faststart "${output}"`;
    try { execSync(cmd, { timeout: 60000, stdio: 'pipe' }); return output; }
    catch (e) { throw new Error(`[FFmpegUtils] 静音处理失败: ${e.message}`); }
  }

  /** 提取音频为AAC格式 */
  extractAudio(input, output) {
    const cmd = `${this.ffmpeg} -y -i "${input}" -vn -c:a aac -b:a 192k "${output}"`;
    try { execSync(cmd, { timeout: 60000, stdio: 'pipe' }); return output; }
    catch (e) { throw new Error(`[FFmpegUtils] 音频提取失败: ${e.message}`); }
  }

  /** 生成视频缩略图JPG */
  thumbnail(input, output, time = '00:00:01', resolution = null) {
    const vf = resolution ? `-vf "scale=${resolution}"` : '';
    const cmd = `${this.ffmpeg} -y -ss ${time} -i "${input}" -vframes 1 ${vf} -q:v 2 -f image2 "${output}"`;
    try { execSync(cmd, { timeout: 30000, stdio: 'pipe' }); return output; }
    catch (e) { throw new Error(`[FFmpegUtils] 缩略图生成失败: ${e.message}`); }
  }

  /** 获取视频时长（秒） */
  getDuration(filePath) {
    const info = this.probe(filePath);
    const d = parseFloat(info.format.duration);
    return isNaN(d) ? 0 : d;
  }

  /** 获取视频分辨率等信息 */
  getResolution(filePath) {
    const info = this.probe(filePath);
    const vs = info.streams.find(s => s.codec_type === 'video');
    if (!vs) throw new Error('未找到视频流');
    return { width: vs.width, height: vs.height, fps: eval(vs.r_frame_rate) || 0, codec: vs.codec_name };
  }

  /** 将秒数格式化为 HH:MM:SS */
  formatTime(seconds) {
    const h = Math.floor(seconds / 3600);
    const m = Math.floor((seconds % 3600) / 60);
    const s = Math.floor(seconds % 60);
    return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  }

  /** 验证视频文件是否有效 */
  validate(filePath) {
    try {
      if (!fs.existsSync(filePath)) return false;
      const info = this.probe(filePath);
      return info.streams.some(s => s.codec_type === 'video') && parseFloat(info.format.duration) > 0;
    } catch { return false; }
  }
}

module.exports = FFmpegUtils;
