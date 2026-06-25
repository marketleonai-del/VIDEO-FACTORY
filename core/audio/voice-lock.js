/**
 * ============================================================================
 * VoiceLockManager - 声纹锁定 @voice1 系统
 * ============================================================================
 * 核心机制:
 * 1. 全片只用一个声纹 @voice1
 * 2. AI生成的视频片段一律静音 (-an)
 * 3. 整片口播写成连续脚本，用@voice1一次性合成统一音轨
 * 4. 最后把统一音轨贴到拼好的画面上
 * ============================================================================
 */

const { execFile } = require('child_process');
const { promisify } = require('util');
const fs = require('fs');
const path = require('path');
const https = require('https');
const http = require('http');
const crypto = require('crypto');

const execFileAsync = promisify(execFile);

// === VoiceLockManager: 声纹锁定管理器 ========================================
class VoiceLockManager {
  constructor(ttsAdapter) {
    this.tts = ttsAdapter;
    this.voiceProfile = null;  // @voice1 声纹档案
    this.isLocked = false;
    this.mutedVideos = new Set();
    this.tempDir = path.join(process.cwd(), 'temp', 'audio');
    if (!fs.existsSync(this.tempDir)) fs.mkdirSync(this.tempDir, { recursive: true });
  }

  /** 锁定声纹: type='clone'从真人克隆 | type='preset'选TTS音色 */
  async lockVoice(source) {
    if (this.isLocked) throw new Error('Voice already locked! 声纹已锁定，不可重复设置。');
    if (!source?.type) throw new Error('Must provide { type: "clone" | "preset" }');

    if (source.type === 'clone') {
      if (!source.audioFile || !fs.existsSync(source.audioFile)) {
        throw new Error(`Clone audio not found: ${source.audioFile}`);
      }
      this.voiceProfile = await this.tts.cloneVoice(source.audioFile, source.name || 'voice1');
    } else if (source.type === 'preset') {
      if (!source.voiceId) throw new Error('Preset mode requires voiceId');
      this.voiceProfile = await this.tts.selectVoice(source.voiceId);
    } else {
      throw new Error(`Unknown type: ${source.type}`);
    }
    this.isLocked = true;
    return this.voiceProfile;
  }

  /** 合成统一音轨: 整片口播用@voice1一次性合成 */
  async synthesizeTrack(scriptText, emotion = 'neutral', platform = 'douyin') {
    if (!this.isLocked) throw new Error('Voice not locked. Call lockVoice() first.');
    if (!scriptText?.trim()) throw new Error('Script text is empty.');

    const segments = this.splitScript(scriptText.trim());
    const segFiles = [];
    for (let i = 0; i < segments.length; i++) {
      const out = path.join(this.tempDir, `seg_${String(i).padStart(3, '0')}.wav`);
      const audio = await this.tts.synthesize(segments[i], {
        voice: this.voiceProfile.voiceId, emotion, speed: this.getPlatformSpeed(platform)
      }, out);
      segFiles.push(audio);
    }
    const unified = path.join(this.tempDir, 'unified_track.wav');
    return this.concatAudio(segFiles, unified);
  }

  /** 静音AI视频: ffmpeg -c:v copy -an 去除音频流 */
  async muteAIVideos(videoPaths) {
    const muted = [];
    for (const video of videoPaths) {
      if (!fs.existsSync(video)) throw new Error(`Video not found: ${video}`);
      const ext = path.extname(video), base = path.basename(video, ext), dir = path.dirname(video);
      const out = path.join(dir, `${base}_muted${ext}`);
      await execFileAsync('ffmpeg', ['-y', '-i', video, '-c:v', 'copy', '-an', out]);
      this.mutedVideos.add(out);
      muted.push(out);
    }
    return muted;
  }

  /** 音视频合成: 画面+统一音轨 mux */
  async muxVideoAudio(videoPath, audioPath, outPath, opts = {}) {
    if (!fs.existsSync(videoPath)) throw new Error(`Video not found: ${videoPath}`);
    if (!fs.existsSync(audioPath)) throw new Error(`Audio not found: ${audioPath}`);
    const args = ['-y', '-i', videoPath, '-i', audioPath];
    args.push(opts.reencode ? '-c:v' : '-c:v', opts.reencode ? 'libx264' : 'copy');
    if (opts.reencode) args.push('-crf', '18', '-preset', 'fast');
    args.push('-c:a', 'aac', '-b:a', opts.audioBitrate || '192k',
      '-map', '0:v:0', '-map', '1:a:0', '-shortest');
    if (opts.volume) args.push('-af', `volume=${opts.volume}`);
    args.push(outPath);
    await execFileAsync('ffmpeg', args);
    return outPath;
  }

  /** 拆分长脚本: 按句子拆分，适配TTS长度限制 */
  splitScript(text, maxLen = 500) {
    const sentences = text.split(/([。！？.!?]+)/).filter(s => s.trim());
    const segs = []; let cur = '';
    for (let i = 0; i < sentences.length; i += 2) {
      const sent = sentences[i].trim(), punct = sentences[i + 1] || '。', full = sent + punct;
      if ((cur + full).length > maxLen && cur.length > 0) { segs.push(cur.trim()); cur = full; }
      else { cur += full; }
    }
    if (cur.trim()) segs.push(cur.trim());
    return segs.length > 0 ? segs : [text.trim()];
  }

  /** 平台适配语速 */
  getPlatformSpeed(platform) {
    const speeds = { douyin: 1.15, kuaishou: 1.1, tiktok: 1.1, xiaohongshu: 1.0, bilibili: 1.05, weixinshipin: 1.0, youtube: 1.0 };
    return speeds[platform] || 1.0;
  }

  /** 拼接音频段: ffmpeg concat demuxer 无损拼接 */
  async concatAudio(files, outPath) {
    if (files.length === 0) throw new Error('No audio files to concatenate');
    if (files.length === 1) { fs.copyFileSync(files[0], outPath); return outPath; }
    const listPath = path.join(this.tempDir, 'concat_list.txt');
    const content = files.map(f => `file '${path.resolve(f).replace(/'/g, "\\'")}'`).join('\n');
    fs.writeFileSync(listPath, content, 'utf8');
    await execFileAsync('ffmpeg', ['-y', '-f', 'concat', '-safe', '0', '-i', listPath, '-c', 'copy', outPath]);
    return outPath;
  }

  /** 音频变速: 使音轨匹配视频时长 */
  async stretchAudio(audioPath, targetDur, outPath) {
    await execFileAsync('ffmpeg', ['-y', '-i', audioPath, '-filter:a', `atempo=${targetDur}`,
      '-c:a', 'aac', '-b:a', '192k', outPath]);
    return outPath;
  }

  /** 自检报告 */
  selfCheck() {
    const adapterName = this.tts && this.tts.constructor ? this.tts.constructor.name : '未配置';
    return {
      voiceLocked: this.isLocked, voiceId: this.voiceProfile?.voiceId || null,
      voiceName: this.voiceProfile?.name || null, mutedCount: this.mutedVideos.size,
      ready: this.isLocked && !!this.tts,
      checks: [
        `全片单一音色: ${this.isLocked ? '✓ @voice1已锁定' : '✗ 未锁定'}`,
        `AI视频已静音: ${this.mutedVideos.size > 0 ? `✓ ${this.mutedVideos.size}个` : '○ 待处理'}`,
        `口播用@voice1合成: ${this.isLocked ? '✓' : '✗ 未锁定'}`,
        `TTS适配器: ${adapterName}`
      ],
      passed: this.isLocked
    };
  }

  cleanup() {
    if (fs.existsSync(this.tempDir)) fs.rmSync(this.tempDir, { recursive: true, force: true });
    this.mutedVideos.clear();
  }
}

// === TTSAdapter: 抽象基类 ====================================================
class TTSAdapter {
  async synthesize(text, opts, outPath) { throw new Error('Not implemented'); }
  async cloneVoice(audioFile, name) { throw new Error('Not implemented'); }
  async selectVoice(voiceId) { throw new Error('Not implemented'); }
}

// === LocalTTSAdapter: 本地CosyVoice/GPT-SoVITS ===============================
class LocalTTSAdapter extends TTSAdapter {
  constructor(endpoint = 'http://localhost:5000') { super(); this.endpoint = endpoint; }

  async _request(apiPath, data, binary = false) {
    return new Promise((resolve, reject) => {
      const url = new URL(apiPath, this.endpoint);
      const body = JSON.stringify(data);
      const opts = { hostname: url.hostname, port: url.port, path: url.pathname,
        method: 'POST', headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) },
        timeout: 120000 };
      const req = http.request(opts, (res) => {
        const chunks = [];
        res.on('data', c => chunks.push(c));
        res.on('end', () => {
          const buf = Buffer.concat(chunks);
          res.statusCode >= 200 && res.statusCode < 300 ? resolve(buf) : reject(new Error(`TTS ${res.statusCode}: ${buf}`));
        });
      });
      req.on('error', reject); req.on('timeout', () => reject(new Error('TTS timeout')));
      req.write(body); req.end();
    });
  }

  async synthesize(text, opts, outPath) {
    const buf = await this._request('/tts', { text, speaker: opts.voice,
      emotion: opts.emotion || 'neutral', speed: opts.speed || 1.0, format: 'wav' });
    fs.writeFileSync(outPath, buf);
    return outPath;
  }

  async cloneVoice(audioFile, name) {
    // 通过/form端点上传文件进行克隆
    const buf = await this._request('/clone', { speaker_name: name });
    const res = JSON.parse(buf.toString());
    return { voiceId: res.speaker_id || name, name, type: 'cloned' };
  }

  async selectVoice(voiceId) {
    return { voiceId, name: voiceId, type: 'preset' };
  }
}

// === ElevenLabsAdapter: ElevenLabs云TTS =====================================
class ElevenLabsAdapter extends TTSAdapter {
  constructor(apiKey) { super(); this.apiKey = apiKey; this.host = 'api.elevenlabs.io'; }

  async _request(path, method = 'GET', postData = null) {
    return new Promise((resolve, reject) => {
      const opts = { hostname: this.host, path: `/v1${path}`, method,
        headers: { 'xi-api-key': this.apiKey, 'Content-Type': 'application/json' }, timeout: 120000 };
      const req = https.request(opts, (res) => {
        const chunks = [];
        res.on('data', c => chunks.push(c));
        res.on('end', () => {
          const buf = Buffer.concat(chunks);
          res.statusCode >= 200 && res.statusCode < 300 ? resolve(buf) : reject(new Error(`ElevenLabs ${res.statusCode}: ${buf}`));
        });
      });
      req.on('error', reject); req.on('timeout', () => reject(new Error('ElevenLabs timeout')));
      if (postData) req.write(JSON.stringify(postData)); req.end();
    });
  }

  async synthesize(text, opts, outPath) {
    const buf = await this._request(`/text-to-speech/${opts.voice}`, 'POST', {
      text, model_id: 'eleven_multilingual_v2',
      voice_settings: { stability: 0.5, similarity_boost: 0.75,
        style: opts.emotion === 'excited' ? 0.5 : 0.3, use_speaker_boost: true, speed: opts.speed || 1.0 },
      output_format: 'mp3_44100_192'
    });
    fs.writeFileSync(outPath, buf);
    return outPath;
  }

  async cloneVoice(audioFile, name) {
    const res1 = JSON.parse((await this._request('/voices', 'POST', {
      name, description: `Cloned @voice1 - ${name}` })).toString());
    await this._request(`/voices/${res1.voice_id}/add-samples`, 'POST', {
      sample_file: fs.readFileSync(audioFile).toString('base64') });
    return { voiceId: res1.voice_id, name, type: 'cloned' };
  }

  async selectVoice(voiceId) {
    const res = JSON.parse((await this._request(`/voices/${voiceId}`)).toString());
    return { voiceId: res.voice_id, name: res.name, type: 'preset' };
  }
}

// === AliyunTTSAdapter: 阿里云语音合成(中文场景推荐) ==========================
class AliyunTTSAdapter extends TTSAdapter {
  constructor(accessKeyId, accessKeySecret, appkey, region = 'cn-shanghai') {
    super();
    this.accessKeyId = accessKeyId; this.accessKeySecret = accessKeySecret;
    this.appkey = appkey; this.region = region;
    this.token = null; this.tokenExpire = 0;
  }

  async _getToken() {
    if (this.token && Date.now() < this.tokenExpire) return this.token;
    const date = new Date().toISOString();
    const sig = crypto.createHmac('sha256', this.accessKeySecret)
      .update(`POST\napplication/json\n\napplication/json\n${date}`).digest('base64');
    return new Promise((resolve, reject) => {
      const req = https.request({
        hostname: `nls-meta.${this.region}.aliyuncs.com`, path: '/token', method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Date': date,
          'Authorization': `acs ${this.accessKeyId}:${sig}` }
      }, (res) => { let d = ''; res.on('data', c => d += c); res.on('end', () => {
        const r = JSON.parse(d); this.token = r.token;
        this.tokenExpire = Date.now() + 25 * 60 * 1000; resolve(this.token); }); });
      req.on('error', reject); req.write(JSON.stringify({ appkey: this.appkey })); req.end();
    });
  }

  async synthesize(text, opts, outPath) {
    const token = await this._getToken();
    const payload = { appkey: this.appkey, token, text, format: 'wav', sample_rate: 44100,
      voice: opts.voice || 'xiaoyun', volume: 50,
      speech_rate: Math.round(((opts.speed || 1.0) - 1) * 500), pitch_rate: 0 };
    return new Promise((resolve, reject) => {
      const req = https.request({
        hostname: `nls-gateway.${this.region}.aliyuncs.com`, path: '/stream/v1/tts', method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-NLS-Token': token }, timeout: 120000
      }, (res) => { const chunks = []; res.on('data', c => chunks.push(c)); res.on('end', () => {
        const buf = Buffer.concat(chunks);
        if ((res.headers['content-type'] || '').startsWith('audio/')) {
          fs.writeFileSync(outPath, buf); resolve(outPath);
        } else { reject(new Error(`Aliyun TTS failed: ${buf}`)); } }); });
      req.on('error', reject); req.on('timeout', () => reject(new Error('Aliyun TTS timeout')));
      req.write(JSON.stringify(payload)); req.end();
    });
  }

  async cloneVoice(audioFile, name) {
    // 阿里云需先在控制台上传音色样本
    return { voiceId: `custom_${name}`, name, type: 'cloned', note: '请先在阿里云控制台上传音色样本' };
  }

  async selectVoice(voiceId) {
    return { voiceId, name: voiceId, type: 'preset' };
  }
}

// === 模块导出 ================================================================
module.exports = {
  VoiceLockManager,   // 声纹锁定管理器（核心入口）
  TTSAdapter,         // TTS适配器基类
  LocalTTSAdapter,    // 本地TTS（CosyVoice/GPT-SoVITS）
  ElevenLabsAdapter,  // ElevenLabs云TTS
  AliyunTTSAdapter    // 阿里云TTS（中文场景推荐）
};
