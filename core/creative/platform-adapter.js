/**
 * ============================================================
 * PlatformAdapter — 平台适配引擎
 * VIDEO-FACTORY 多平台分发核心
 * ============================================================
 * 职责:
 *   - 5 大平台预设管理（抖音 / 快手 / 视频号 / 小红书 / TikTok）
 *   - 脚本级适配（钩子调性 / 节奏 / CTA / 字幕 / 音乐 / 发布时间）
 *   - 画面级适配（比例 / 风格 / 光线 / 时长上限）
 *   - 合规级适配（违禁开场 / 平台禁忌词 / 行业分区）
 *
 * 设计原则:
 *   - 适配是"转译"而非"重写"：保留核心卖点，只调表达方式
 *   - 每个平台有独立 preset，可热更新
 * ============================================================
 */

class PlatformAdapter {
  constructor() {
    // 五大平台完整预设
    this.presets = {
      // ── 抖音 ──────────────────────────────
      douyin: {
        name: '抖音', code: 'douyin',
        aspect: '9:16', maxDuration: 60, recommendDuration: [15, 30, 60],
        resolution: '1080x1920', fps: 30,
        hookStyle: '强钩子/冲突开场/反认知/数字冲击',
        pace: '快', paceWpm: 280,              // 每分钟字数参考
        cta: '点击小黄车/评论区见/主页橱窗',
        subtitleStyle: '关键信息上大字/关键词高亮/动态出现',
        musicStyle: '热门BGM/卡点/变装音效',
        bannedOpeners: ['大家好今天推荐', 'Hey guys', '家人们今天给你们', '今天给大家分享'],
        bannedWords: ['最', '第一', '国家级', '根治', '100%有效', '绝对'],
        visualStyle: '高饱和/强对比/信息密度高/竖屏满画幅',
        optimalTimes: ['12:00-13:00', '18:00-20:00', '21:00-23:00'],
        bestDays: ['周二', '周四', '周六'],
        features: { shoppingCart: true, liveRoom: true, miniStore: true },
        contentTags: ['反转', '教程', '测评', '挑战']
      },

      // ── 快手 ──────────────────────────────
      kuaishou: {
        name: '快手', code: 'kuaishou',
        aspect: '9:16', maxDuration: 60, recommendDuration: [15, 30, 60],
        resolution: '1080x1920', fps: 30,
        hookStyle: '接地气/老铁文化/真实信任/生活场景',
        pace: '中快', paceWpm: 250,
        cta: '小黄车下单/直播间等你/私信我',
        subtitleStyle: '口语化大字/接地气表达/情感词强调',
        musicStyle: '接地气BGM/情感音乐/口播原声',
        bannedOpeners: ['大家好今天推荐', '各位观众朋友'],
        bannedWords: ['最', '第一', '根治', '100%'],
        visualStyle: '真实感/生活化/自然光线/不过度修图',
        optimalTimes: ['12:00-13:00', '18:00-21:00', '21:00-22:00'],
        bestDays: ['周三', '周五', '周日'],
        features: { shoppingCart: true, liveRoom: true, privateDomain: true },
        contentTags: ['真实测评', '源头好货', '老铁推荐']
      },

      // ── 视频号 ────────────────────────────
      shipinhao: {
        name: '视频号', code: 'shipinhao',
        aspect: '9:16', maxDuration: 60, recommendDuration: [15, 30],
        resolution: '1080x1920', fps: 30,
        hookStyle: '标题党/社交货币/知识获得感/转发价值',
        pace: '中', paceWpm: 230,
        cta: '关注公众号/加微信/私域社群/点赞转发',
        subtitleStyle: '信息密度高/知识型排版/重点标色',
        musicStyle: '轻音乐/知识感BGM/原声口播',
        bannedOpeners: ['大家好', '各位好'],
        bannedWords: ['最', '第一', '根治', '100%'],
        visualStyle: '清新/信任感/知识感/中高端调性',
        optimalTimes: ['07:00-09:00', '12:00-13:00', '21:00-22:00'],
        bestDays: ['周一', '周三', '周五'],
        features: { shoppingCart: true, officialAccount: true, privateDomain: true },
        contentTags: ['知识科普', '生活技巧', '好物分享']
      },

      // ── 小红书 ────────────────────────────
      xiaohongshu: {
        name: '小红书', code: 'xiaohongshu',
        aspect: '4:3', maxDuration: 60, recommendDuration: [15, 30],
        resolution: '1080x1440', fps: 30,
        hookStyle: '高颜值/生活美学/种草感/真实体验',
        pace: '中', paceWpm: 220,
        cta: '评论区问链接/主页店铺/标记位置',
        subtitleStyle: '文艺感/emoji点缀/分段清晰/关键词高亮',
        musicStyle: '清新治愈/日系/轻音乐/环境音',
        bannedOpeners: ['大家好今天推荐', '今天给大家推荐'],
        bannedWords: ['最', '第一', '根治', '100%', '智商税'],
        visualStyle: 'ins风/柔和滤镜/高颜值/精致生活感',
        optimalTimes: ['08:00-09:00', '12:00-13:00', '20:00-22:00'],
        bestDays: ['周四', '周五', '周六', '周日'],
        features: { shoppingCart: true, notes: true, liveRoom: true },
        contentTags: ['种草', '测评', '教程', 'Vlog']
      },

      // ── TikTok ────────────────────────────
      tiktok: {
        name: 'TikTok', code: 'tiktok',
        aspect: '9:16', maxDuration: 60, recommendDuration: [15, 30, 60],
        resolution: '1080x1920', fps: 30,
        hookStyle: '视觉冲击/卡点/潮流/UGC感/话题挑战',
        pace: '快', paceWpm: 260,
        cta: 'Shop Now/Link in Bio/Comment below',
        subtitleStyle: '多语言字幕/关键词跳动/大字幕',
        musicStyle: 'Trending/卡点/Remix/原声',
        bannedOpeners: ['Hey guys welcome back', 'In this video', 'Today I will'],
        bannedWords: ['guarantee', 'cure', '100%', 'best ever', '#ad 未标'],
        visualStyle: '高饱和/潮流感/强视觉/年轻化',
        optimalTimes: ['12:00-13:00', '19:00-21:00', '21:00-23:00'],
        bestDays: ['周二', '周四', '周五'],
        features: { shoppingCart: true, liveRoom: true, creativityFund: true },
        contentTags: ['Trend', 'Challenge', 'Haul', 'Review']
      }
    };

    // 平台间内容迁移的转换权重（用于估算改编成本）
    this.migrateCost = {
      douyin_kuaishou: 0.2, douyin_shipinhao: 0.4, douyin_xiaohongshu: 0.6, douyin_tiktok: 0.3,
      kuaishou_douyin: 0.2, kuaishou_shipinhao: 0.5, kuaishou_xiaohongshu: 0.7, kuaishou_tiktok: 0.5,
      shipinhao_douyin: 0.4, shipinhao_kuaishou: 0.5, shipinhao_xiaohongshu: 0.5, shipinhao_tiktok: 0.6,
      xiaohongshu_douyin: 0.6, xiaohongshu_kuaishou: 0.7, xiaohongshu_shipinhao: 0.5, xiaohongshu_tiktok: 0.6,
      tiktok_douyin: 0.3, tiktok_kuaishou: 0.5, tiktok_shipinhao: 0.6, tiktok_xiaohongshu: 0.6
    };
  }

  // ─────────────────────────────────────────────
  // 1. 主适配入口 — 脚本级全维度适配
  // ─────────────────────────────────────────────
  /**
   * 将通用脚本适配到指定平台
   * @param {Object} script   - 原始脚本 {hook, body, cta, sellingPoints}
   * @param {string} platform - 平台代码（douyin/kuaishou/shipinhao/xiaohongshu/tiktok）
   * @returns {Object} 适配后的完整脚本 + 平台元信息
   */
  adapt(script, platform) {
    const preset = this.presets[platform];
    if (!preset) {
      throw new Error(`[PlatformAdapter] 不支持的平台 "${platform}"。支持: ${this.listPlatforms().join(', ')}`);
    }

    return {
      ...script,
      _platform: {
        name: preset.name,
        code: preset.code,
        aspect: preset.aspect,
        maxDuration: preset.maxDuration,
        adaptedAt: new Date().toISOString()
      },
      hook: this.adaptHook(script.hook || '', preset),
      body: this.adaptBody(script.body || '', preset),
      cta: this.adaptCTA(script.cta || '', preset),
      pacing: preset.pace,
      paceWpm: preset.paceWpm,
      subtitleStyle: preset.subtitleStyle,
      musicStyle: preset.musicStyle,
      visualStyle: preset.visualStyle,
      publishStrategy: {
        optimalTimes: preset.optimalTimes,
        bestDays: preset.bestDays,
        recommendDuration: this.pickDuration(script.duration, preset.recommendDuration)
      },
      compliance: {
        bannedWords: preset.bannedWords,
        checkRequired: true
      }
    };
  }

  // ─────────────────────────────────────────────
  // 2. 子维度适配方法
  // ─────────────────────────────────────────────

  /** 钩子适配：根据平台调性改写前3秒 */
  adaptHook(hook, preset) {
    // 检测并替换违禁开场
    let adapted = hook;
    for (const banned of preset.bannedOpeners) {
      if (adapted.startsWith(banned)) {
        adapted = adapted.slice(banned.length).trim();
      }
    }

    // 平台特异性调性注入
    const styleInjections = {
      douyin:      () => adapted,                                       // 抖音保持原样（强钩子已适配）
      kuaishou:    () => adapted.replace(/您/g, '你').replace(/亲爱的/g, '老铁'), // 快手接地气
      shipinhao:   () => adapted,                                       // 视频号标题党由策略层处理
      xiaohongshu: () => adapted.replace(/你/g, '姐妹').replace(/!/g, '！✨'), // 小红书闺蜜感
      tiktok:      () => adapted                                        // TikTok 英文由翻译层处理
    };

    return styleInjections[preset.code] ? styleInjections[preset.code]() : adapted;
  }

  /** 正文适配：调整语气与信息密度 */
  adaptBody(body, preset) {
    const densityMap = { fast: 1.3, medium: 1.0, slow: 0.8 };
    const density = densityMap[preset.pace] || 1.0;
    return {
      text: body,
      targetWpm: preset.paceWpm,
      densityMultiplier: density,
      note: `按${preset.name} ${preset.pace}节奏调整，目标${preset.paceWpm}字/分钟`
    };
  }

  /** CTA 适配：替换为平台特定行动号召 */
  adaptCTA(cta, preset) {
    if (!cta || cta.trim() === '') return preset.cta;
    // 保留原 CTA 意图，替换为平台惯用表达
    return `${preset.cta}（原意：${cta}）`;
  }

  // ─────────────────────────────────────────────
  // 3. 画面级适配
  // ─────────────────────────────────────────────
  /**
   * 获取画面技术参数（喂给视频生成 prompt）
   * @param {string} platform - 平台代码
   * @returns {Object} 画面技术规格
   */
  getVideoSpec(platform) {
    const p = this.presets[platform];
    if (!p) return null;
    return {
      aspectRatio: p.aspect,
      resolution: p.resolution,
      fps: p.fps,
      maxDuration: p.maxDuration,
      visualStyle: p.visualStyle,
      platform: p.name
    };
  }

  // ─────────────────────────────────────────────
  // 4. 合规级适配
  // ─────────────────────────────────────────────
  /**
   * 合规扫描：检测脚本中是否含平台违禁词
   * @param {string} text     - 待检文本
   * @param {string} platform - 平台代码
   * @returns {Object} 扫描结果 {passed, violations[]}
   */
  complianceScan(text, platform) {
    const preset = this.presets[platform];
    if (!preset) return { passed: false, error: '未知平台' };

    const violations = [];
    for (const word of preset.bannedWords) {
      if (text.includes(word)) {
        violations.push({ word, position: text.indexOf(word) });
      }
    }

    return {
      passed: violations.length === 0,
      platform: preset.name,
      violations,
      severity: violations.length === 0 ? 'pass' : violations.length <= 2 ? 'warning' : 'block'
    };
  }

  // ─────────────────────────────────────────────
  // 5. 策略工具方法
  // ─────────────────────────────────────────────

  /** 选择最佳时长档位 */
  pickDuration(requested, options) {
    if (!requested) return options[0];
    return options.reduce((prev, curr) =>
      Math.abs(curr - requested) < Math.abs(prev - requested) ? curr : prev
    );
  }

  /** 获取迁移成本（估算从 A 平台改编到 B 平台的工作量） */
  getMigrateCost(from, to) {
    if (from === to) return 0;
    const key = `${from}_${to}`;
    return this.migrateCost[key] ?? 0.5; // 默认 0.5 中等成本
  }

  /** 列出所有支持的平台 */
  listPlatforms() {
    return Object.keys(this.presets);
  }

  /** 获取平台完整预设（用于调试/前端展示） */
  getPreset(platform) {
    return this.presets[platform] ? { ...this.presets[platform] } : null;
  }

  /** 获取所有平台简要信息 */
  listPlatformBriefs() {
    return Object.entries(this.presets).map(([code, p]) => ({
      code, name: p.name, aspect: p.aspect, maxDuration: p.maxDuration,
      hookStyle: p.hookStyle, pace: p.pace
    }));
  }

  // ─────────────────────────────────────────────
  // 6. 批量适配 — 矩阵分发场景
  // ─────────────────────────────────────────────
  /**
   * 一键适配多平台（矩阵分发）
   * @param {Object} script    - 原始脚本
   * @param {string[]} targets - 目标平台代码数组
   * @returns {Object} 各平台适配结果映射
   */
  adaptMulti(script, targets) {
    const results = {};
    for (const platform of targets) {
      try {
        results[platform] = this.adapt(script, platform);
      } catch (err) {
        results[platform] = { _error: err.message };
      }
    }
    return results;
  }
}

// ─────────────────────────────────────────────────
// 导出（兼容 CommonJS 与 ES Module）
// ─────────────────────────────────────────────────
if (typeof module !== 'undefined' && module.exports) {
  module.exports = { PlatformAdapter };
}
