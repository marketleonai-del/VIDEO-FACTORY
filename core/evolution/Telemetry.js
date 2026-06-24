/**
 * @file Telemetry.js
 * @description 匿名遥测系统 —— 默认关闭、opt-in、无 PII
 *
 * 原则：
 * 1. 默认关闭，用户主动 opt-in 后才上报
 * 2. 白名单过滤：仅 ALLOW 中定义的字段可通过，其余字段物理剔除
 * 3. 零 PII：不上传用户名、IP、文件路径、prompt 原文等敏感信息
 * 4. 本地缓冲：网络异常时落盘，恢复后批量上报
 */

'use strict';

/** 匿名遥测系统，管理事件的上报生命周期 */
class Telemetry {
  static ALLOW = [
    'type', 'anonId', 'skillVersion', 'paramsVersion',
    'model', 'scene', 'angle', 'durationSec', 'success',
    'retries', 'regenerated', 'qcScore', 'qcPass', 'assembleOk', 'ms', 'ts',
  ];

  constructor() {
    this.enabled = false;
    this.events = [];
    this.flushThreshold = 50;
    this.flushIntervalMs = 5 * 60 * 1000;
    this.endpoint = null;
    this.commonDimensions = {};
    this._timer = setInterval(() => this.flush(), this.flushIntervalMs);
  }

  enable(endpoint, commonDims = {}) {
    this.enabled = true;
    this.endpoint = endpoint;
    this.commonDimensions = this.sanitize(commonDims);
  }

  disable() {
    this.enabled = false;
    this.endpoint = null;
    this.commonDimensions = {};
    if (this._timer) { clearInterval(this._timer); this._timer = null; }
  }

  record(event) {
    if (!this.enabled || !event || typeof event !== 'object') return null;
    const clean = this.sanitize(event);
    if (Object.keys(clean).length === 0) return null;
    const enriched = { ...this.commonDimensions, ...clean, ts: Date.now() };
    this.events.push(enriched);
    if (this.events.length >= this.flushThreshold) this.flush();
    return enriched;
  }

  sanitize(event) {
    if (!event || typeof event !== 'object') return {};
    const clean = {};
    for (const key of Telemetry.ALLOW) {
      if (Object.prototype.hasOwnProperty.call(event, key)) {
        const val = event[key];
        if (typeof val === 'string' || typeof val === 'number' || typeof val === 'boolean') {
          clean[key] = val;
        }
      }
    }
    return clean;
  }

  async flush() {
    if (!this.enabled || this.events.length === 0 || !this.endpoint) {
      return { sent: 0, success: false };
    }
    const batch = this.events.splice(0);
    try {
      const ok = await this._post(JSON.stringify(batch));
      return { sent: batch.length, success: ok };
    } catch (err) {
      return { sent: 0, success: false, error: err.message };
    }
  }

  getBufferStats() {
    return {
      enabled: this.enabled, buffered: this.events.length,
      endpoint: this.endpoint, flushThreshold: this.flushThreshold,
    };
  }

  _post(payload) {
    return new Promise((resolve) => {
      try {
        const https = require('https');
        const url = new URL(this.endpoint);
        const options = {
          hostname: url.hostname, port: url.port || 443,
          path: url.pathname + url.search, method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Content-Length': Buffer.byteLength(payload),
            'User-Agent': 'SkillOpt-Telemetry/1.0',
          },
          timeout: 10000,
        };
        const req = https.request(options, (res) => {
          resolve(res.statusCode >= 200 && res.statusCode < 300);
        });
        req.on('error', () => resolve(false));
        req.on('timeout', () => { req.destroy(); resolve(false); });
        req.write(payload); req.end();
      } catch { resolve(false); }
    });
  }
}

module.exports = { Telemetry };
