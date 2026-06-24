/**
 * VideoDownloader - 视频下载器
 * 支持丽帧/可灵/通用URL等多种来源的视频下载
 *
 * @author VIDEO-FACTORY
 * @description HTTP/HTTPS视频下载，支持重定向、批量下载、API轮询
 */
const http = require('http');
const https = require('https');
const fs = require('fs');
const path = require('path');
const { URL } = require('url');

class VideoDownloader {
  constructor(outputDir = './.uvg-out') {
    this.outputDir = outputDir;
    if (!fs.existsSync(outputDir)) fs.mkdirSync(outputDir, { recursive: true });
    this.defaultHeaders = { 'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.0' };
    this.maxRedirects = 5;
    this.downloadTimeout = 120000;
    this.pollInterval = 5000;
    this.maxPollCount = 60;
  }

  /** 从URL下载视频，支持重定向 */
  async download(url, filename, headers = {}) {
    const outputPath = path.join(this.outputDir, filename);
    return new Promise((resolve, reject) => {
      this._downloadWithRedirect(url, outputPath, headers, 0, resolve, reject);
    });
  }

  /** 内部方法：处理重定向下载 */
  _downloadWithRedirect(url, outputPath, headers, redirectCount, resolve, reject) {
    if (redirectCount > this.maxRedirects) {
      return reject(new Error(`[VideoDownloader] 重定向次数超过上限(${this.maxRedirects})`));
    }
    try {
      const parsedUrl = new URL(url);
      const client = parsedUrl.protocol === 'https:' ? https : http;
      const requestHeaders = { ...this.defaultHeaders, ...headers };

      const request = client.get(url, { headers: requestHeaders, timeout: this.downloadTimeout }, (response) => {
        // 处理重定向
        if (response.statusCode >= 301 && response.statusCode <= 308 && response.headers.location) {
          const redirectUrl = new URL(response.headers.location, url).toString();
          return this._downloadWithRedirect(redirectUrl, outputPath, headers, redirectCount + 1, resolve, reject);
        }
        if (response.statusCode !== 200) {
          return reject(new Error(`[VideoDownloader] HTTP ${response.statusCode}: ${url}`));
        }
        // 下载到文件
        const file = fs.createWriteStream(outputPath);
        let downloadedBytes = 0;
        const totalBytes = parseInt(response.headers['content-length'] || '0', 10);
        response.pipe(file);
        response.on('data', (chunk) => {
          downloadedBytes += chunk.length;
          if (totalBytes > 0) {
            const progress = ((downloadedBytes / totalBytes) * 100).toFixed(1);
            process.stdout.write(`\r[VideoDownloader] 下载进度: ${progress}%`);
          }
        });
        file.on('finish', () => {
          file.close();
          if (totalBytes > 0) process.stdout.write('\n');
          const stats = fs.statSync(outputPath);
          if (stats.size === 0) { fs.unlinkSync(outputPath); reject(new Error(`[VideoDownloader] 下载文件为空: ${url}`)); }
          else resolve(outputPath);
        });
        file.on('error', (err) => { fs.unlink(outputPath, () => {}); reject(new Error(`[VideoDownloader] 文件写入错误: ${err.message}`)); });
      });
      request.on('error', (err) => { fs.unlink(outputPath, () => {}); reject(new Error(`[VideoDownloader] 网络请求错误: ${err.message}`)); });
      request.on('timeout', () => { request.destroy(); fs.unlink(outputPath, () => {}); reject(new Error(`[VideoDownloader] 下载超时: ${url}`)); });
    } catch (e) { reject(new Error(`[VideoDownloader] URL解析失败: ${e.message}`)); }
  }

  /** 批量下载多个URL */
  async downloadBatch(urls, namePrefix = 'video', headers = {}) {
    const results = [];
    for (let i = 0; i < urls.length; i++) {
      try {
        const filepath = await this.download(urls[i], `${namePrefix}_${i}.mp4`, headers);
        results.push({ success: true, path: filepath, index: i });
      } catch (e) { results.push({ success: false, error: e.message, index: i }); }
    }
    return results;
  }

  /**
   * 下载丽帧(Kuaizi)平台视频 - 轮询task_id获取视频URL后下载
   * @param {string} taskId   - 丽帧任务ID
   * @param {string} apiKey   - 丽帧API密钥
   * @param {string} filename - 保存文件名
   * @param {string} apiBase  - API基础地址，默认'https://api.kuaizi.ai'
   */
  async downloadKuaizi(taskId, apiKey, filename = null, apiBase = 'https://api.kuaizi.ai') {
    const finalFilename = filename || `kuaizi_${taskId}.mp4`;
    const videoUrl = await this._pollKuaiziTask(taskId, apiKey, apiBase);
    if (!videoUrl) throw new Error(`[VideoDownloader] 丽帧任务${taskId}未返回视频URL`);
    return this.download(videoUrl, finalFilename, { 'Authorization': `Bearer ${apiKey}` });
  }

  /** 轮询丽帧任务状态 */
  async _pollKuaiziTask(taskId, apiKey, apiBase) {
    const statusUrl = `${apiBase}/v1/tasks/${taskId}`;
    for (let i = 0; i < this.maxPollCount; i++) {
      try {
        const status = await this._fetchJSON(statusUrl, { 'Authorization': `Bearer ${apiKey}` });
        if (status.data && status.data.status === 'completed') {
          return status.data.video_url || status.data.output_url || null;
        }
        if (status.data && status.data.status === 'failed') {
          throw new Error(`[VideoDownloader] 丽帧任务失败: ${status.data.error || '未知错误'}`);
        }
        await this._sleep(this.pollInterval);
      } catch (e) {
        if (e.message.includes('任务失败')) throw e;
        await this._sleep(this.pollInterval);
      }
    }
    throw new Error(`[VideoDownloader] 丽帧任务轮询超时: ${taskId}`);
  }

  /** 发送GET请求并解析JSON */
  _fetchJSON(url, headers = {}) {
    return new Promise((resolve, reject) => {
      try {
        const parsedUrl = new URL(url);
        const client = parsedUrl.protocol === 'https:' ? https : http;
        client.get(url, { headers: { ...this.defaultHeaders, ...headers }, timeout: 30000 }, (response) => {
          let data = '';
          response.on('data', chunk => { data += chunk; });
          response.on('end', () => { try { resolve(JSON.parse(data)); } catch { resolve({ raw: data }); } });
        }).on('error', reject);
      } catch (e) { reject(e); }
    });
  }

  /** 睡眠等待 */
  _sleep(ms) { return new Promise(resolve => setTimeout(resolve, ms)); }

  /** 设置下载配置 */
  setConfig(config) {
    if (config.downloadTimeout) this.downloadTimeout = config.downloadTimeout;
    if (config.maxRedirects) this.maxRedirects = config.maxRedirects;
    if (config.pollInterval) this.pollInterval = config.pollInterval;
    if (config.maxPollCount) this.maxPollCount = config.maxPollCount;
    if (config.defaultHeaders) this.defaultHeaders = { ...this.defaultHeaders, ...config.defaultHeaders };
  }
}

module.exports = VideoDownloader;
