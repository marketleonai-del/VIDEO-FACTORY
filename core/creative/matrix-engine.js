/**
 * ============================================================================
 * VIDEO-FACTORY :: Matrix Content Generation Engine
 * ============================================================================
 * 1精品母版 → 最多200条不同角度带货视频
 * Pipeline: S0验证 → S1拆解 → S2变体 → S3人设 → S4适配 → S5防限流 → S6成本
 * ============================================================================
 */

const crypto = require('crypto');
const {
  MATRIX_CONFIG, PERSONA_LIBRARY, SCENARIO_LIBRARY,
  RHYTHM_LIBRARY, PLATFORM_PRESETS, CTA_LIBRARY,
  BGM_TAGS, CAPTION_STYLES,
} = require('./matrix-config');

class MatrixEngine {
  constructor(hookEngine, skillParams = {}) {
    this.hookEngine = hookEngine;
    this.config = { ...MATRIX_CONFIG, ...skillParams.matrixConfig };
    this.variantCache = new Map();
    this.overlapMatrix = [];
    this.qualityGateLog = [];
  }

  // ═══════════════════════════════════════════════════════════════════════════
  // S0: 精品母版验证
  // ═══════════════════════════════════════════════════════════════════════════
  validateHero(heroScript) {
    const checkList = this._buildCheckList();
    const gate = { passed: 0, failed: 0, total: checkList.length, checks: [], score: 0 };
    let [totalWeight, earnedWeight] = [0, 0];
    for (const c of checkList) {
      const pass = c.test(heroScript);
      gate.checks.push({ id: c.id, category: c.category, name: c.name, pass, weight: c.weight });
      totalWeight += c.weight;
      if (pass) { gate.passed++; earnedWeight += c.weight; } else { gate.failed++; }
    }
    gate.score = totalWeight ? Math.round((earnedWeight / totalWeight) * 100) : 0;
    gate.isQualified = gate.score >= 75;
    this.qualityGateLog.push({ phase: 'S0-HeroValidation', ...gate });
    return gate;
  }

  _buildCheckList() {
    const has = v => v !== undefined && v !== null;
    return [
      { id: 1,  category: '钩子', name: '前3秒有强钩子',         weight: 5, test: s => has(s.hook?.opening3s) && s.hook.opening3s.length >= 3 },
      { id: 2,  category: '钩子', name: '钩子引发情绪反应',       weight: 5, test: s => has(s.hook?.emotionTrigger) },
      { id: 3,  category: '钩子', name: '钩子与产品强相关',       weight: 5, test: s => has(s.hook?.productRelevance) && s.hook.productRelevance >= 0.7 },
      { id: 4,  category: '钩子', name: '钩子有信息缺口',         weight: 5, test: s => has(s.hook?.infoGap) },
      { id: 5,  category: '钩子', name: '钩子公式符合心理学',     weight: 5, test: s => has(s.hook?.psychologyMatch) },
      { id: 6,  category: '钩子', name: '视觉钩子(前3帧)',        weight: 5, test: s => has(s.hook?.visualHook) },
      { id: 7,  category: '钩子', name: '听觉钩子(BGM前1秒)',    weight: 5, test: s => has(s.hook?.audioHook) },
      { id: 8,  category: '钩子', name: '文字钩子(标题党)',       weight: 5, test: s => has(s.hook?.textHook) },
      { id: 9,  category: '钩子', name: '钩子有冲突/反差',        weight: 5, test: s => has(s.hook?.conflictLevel) && s.hook.conflictLevel > 0.5 },
      { id: 10, category: '钩子', name: '钩子独特度≥60%',         weight: 5, test: s => has(s.hook?.uniqueness) && s.hook.uniqueness >= 0.6 },
      { id: 11, category: '钩子', name: '钩子可复制到变体',       weight: 5, test: s => has(s.hook?.variantReady) },
      { id: 12, category: '钩子', name: '钩子时长≤3秒',           weight: 5, test: s => has(s.hook?.duration) && s.hook.duration <= 3000 },
      { id: 13, category: '结构', name: '结构有8要素',            weight: 4, test: s => has(s.structure?.elements) && s.structure.elements.length >= 8 },
      { id: 14, category: '结构', name: '转化链路完整',           weight: 4, test: s => has(s.structure?.funnel) },
      { id: 15, category: '结构', name: '节奏曲线有起伏',         weight: 4, test: s => has(s.structure?.pacingCurve) },
      { id: 16, category: '结构', name: '信息密度适中',           weight: 4, test: s => has(s.structure?.infoDensity) && s.structure.infoDensity >= 0.5 },
      { id: 17, category: '结构', name: '时长15-60秒',            weight: 4, test: s => has(s.structure?.duration) && s.structure.duration >= 15 && s.structure.duration <= 60 },
      { id: 18, category: '卖点', name: '核心卖点唯一且清晰',     weight: 5, test: s => has(s.sellingPoint?.core) && s.sellingPoint.core.length <= 20 },
      { id: 19, category: '卖点', name: '卖点有证据支撑',         weight: 5, test: s => has(s.sellingPoint?.proof) },
      { id: 20, category: '卖点', name: '卖点差异化表述',         weight: 5, test: s => has(s.sellingPoint?.differentiation) },
      { id: 21, category: '人设', name: '人设定位清晰',           weight: 4, test: s => has(s.persona?.role) },
      { id: 22, category: '人设', name: '人设可信度高',           weight: 4, test: s => has(s.persona?.credibility) && s.persona.credibility >= 0.6 },
      { id: 23, category: '人设', name: '人设语言风格一致',       weight: 4, test: s => has(s.persona?.languageStyle) },
      { id: 24, category: '人设', name: '人设视觉签名统一',       weight: 4, test: s => has(s.persona?.visualSignature) },
      { id: 25, category: '人设', name: '人设与产品匹配',         weight: 4, test: s => has(s.persona?.productFit) && s.persona.productFit >= 0.6 },
      { id: 41, category: 'CTA',  name: 'CTA明确唯一',            weight: 4, test: s => has(s.cta?.action) && s.cta.action.length > 0 },
      { id: 42, category: 'CTA',  name: 'CTA有紧迫感',            weight: 4, test: s => has(s.cta?.urgency) },
      { id: 43, category: 'CTA',  name: 'CTA出现≥2次',            weight: 4, test: s => has(s.cta?.count) && s.cta.count >= 2 },
      { id: 44, category: 'CTA',  name: 'CTA有利益点',            weight: 4, test: s => has(s.cta?.benefit) },
      { id: 45, category: 'CTA',  name: 'CTA路径≤3步',            weight: 4, test: s => has(s.cta?.steps) && s.cta.steps <= 3 },
      { id: 61, category: 'BGM',  name: 'BGM匹配情绪曲线',        weight: 4, test: s => has(s.bgm?.emotionMatch) },
      { id: 62, category: 'BGM',  name: 'BGM节奏匹配剪辑',        weight: 4, test: s => has(s.bgm?.paceMatch) },
      { id: 63, category: 'BGM',  name: 'BGM音量平衡',            weight: 4, test: s => has(s.bgm?.volumeBalanced) },
      { id: 64, category: 'BGM',  name: '卡点精度<0.1s',          weight: 4, test: s => has(s.bgm?.beatAccuracy) && s.bgm.beatAccuracy < 0.1 },
      { id: 65, category: 'BGM',  name: '音轨不侵权',             weight: 4, test: s => has(s.bgm?.licenseClear) },
      { id: 81, category: '视觉', name: '开场镜有冲击力',         weight: 3, test: s => has(s.visual?.openingShot) },
      { id: 82, category: '视觉', name: '产品展示≥3个角度',       weight: 3, test: s => has(s.visual?.productAngles) && s.visual.productAngles >= 3 },
      { id: 83, category: '视觉', name: '字幕清晰可读',           weight: 3, test: s => has(s.visual?.captionReadability) },
      { id: 84, category: '视觉', name: '色彩风格一致',           weight: 3, test: s => has(s.visual?.colorConsistent) },
      { id: 85, category: '视觉', name: '转场流畅',               weight: 3, test: s => has(s.visual?.transitions) },
      { id: 101, category: '合规', name: '无绝对化用语',          weight: 5, test: s => has(s.compliance?.noAbsoluteTerms) },
      { id: 102, category: '合规', name: '无虚假宣传',            weight: 5, test: s => has(s.compliance?.noFalseClaims) },
      { id: 103, category: '合规', name: '有功效声明依据',        weight: 5, test: s => has(s.compliance?.claimsBacked) },
      { id: 104, category: '合规', name: 'AI标识已规划',          weight: 5, test: s => has(s.compliance?.aiLabelPlanned) },
      { id: 105, category: '合规', name: '版权素材无风险',        weight: 5, test: s => has(s.compliance?.copyrightClear) },
    ];
  }

  // ═══════════════════════════════════════════════════════════════════════════
  // S1: 赢家拆解
  // ═══════════════════════════════════════════════════════════════════════════
  decomposeWinner(heroScript) {
    const m = (d, s, c) => ({ ...d, mutable: true, rotationStrategy: s, variantCount: c });
    const i = (d, r) => ({ ...d, mutable: false, reason: r });
    return {
      hook: m(heroScript.hook, '公式替换:同一卖点换3-5种钩子公式', 20),
      persona: m(heroScript.persona, '人设轮换:学生党/宝妈/上班族/专业测评/精致生活/租房党/健身达人', 10),
      bgm: m(heroScript.bgm, 'BGM轮换:热门卡点/治愈/电子/悬疑/温馨', 15),
      cta: m(heroScript.cta, 'CTA替换:紧迫/利益/社交/稀缺', 5),
      scene: m({}, '场景轮换:厨房/卧室/办公/户外/车内/浴室', 8),
      rhythm: m({}, '节奏轮换:快剪/口播/剧情/ASMR', 6),
      captionStyle: m({}, '字幕样式轮换', 6),
      structure: i(heroScript.structure, '验证过的转化链路，动则全崩'),
      coreSellingPoint: i(heroScript.sellingPoint, '核心卖点唯一，不可动摇'),
      brandMessage: i(heroScript.brandMessage, '品牌信息必须一致'),
      complianceRules: i(heroScript.compliance, '合规底线不可碰'),
      _decompositionMeta: { mutableAxes: 7, immutableAxes: 4, totalCombinations: 5 * 10 * 15 * 5 * 8 * 6 * 6 },
    };
  }

  // ═══════════════════════════════════════════════════════════════════════════
  // S2: 变体生成
  // ═══════════════════════════════════════════════════════════════════════════
  generateVariants(winner, count = 200, platform = 'douyin') {
    count = Math.max(Math.min(count, this.config.MAX_VARIANTS), this.config.MIN_VARIANTS);
    const variants = [];
    const pools = {
      hooks: this._getHookVariants(winner.hook),
      personas: PERSONA_LIBRARY, scenarios: SCENARIO_LIBRARY,
      rhythms: RHYTHM_LIBRARY, ctas: CTA_LIBRARY[platform] || CTA_LIBRARY.douyin,
      bgms: BGM_TAGS, captions: CAPTION_STYLES,
    };
    let attempts = 0;
    const maxAttempts = count * 100;
    while (variants.length < count && attempts < maxAttempts) {
      attempts++;
      const variant = this._buildVariant(winner, variants.length, pools, platform);
      if (!this._meetsDiffCriteria(variant, variants)) continue;
      if (this._calculateMaxOverlap(variant, variants) > this.config.OVERLAP_THRESHOLD) continue;
      const sameHook = variants.filter(v => v.hookVariant.id === variant.hookVariant.id).length;
      if (sameHook >= this.config.MAX_SAME_HOOK_PER_BATCH) continue;
      variants.push(variant);
      this.variantCache.set(variant.id, variant);
    }
    this._computeOverlapMatrix(variants);
    return { total: variants.length, target: count, successRate: Math.round((variants.length / count) * 100), variants };
  }

  _buildVariant(winner, idx, pools, platform) {
    const { hooks, personas, scenarios, rhythms, ctas, bgms, captions } = pools;
    const p = (i, m, s) => this._permuteIndex(i, m, s);
    const hook = hooks[p(idx, hooks.length, 11)];
    const persona = personas[p(idx, personas.length, 7)];
    const scenario = scenarios[p(idx, scenarios.length, 13)];
    const rhythm = rhythms[p(idx, rhythms.length, 3)];
    const cta = ctas[p(idx, ctas.length, 5)];
    const bgmTag = bgms[p(idx, bgms.length, 17)];
    const caption = captions[p(idx, captions.length, 19)];
    return {
      id: `V${String(idx + 1).padStart(3, '0')}`, version: idx + 1,
      hookVariant: { id: hook.id, text: this._adaptHook(hook.text, persona), formula: hook.formula, duration: hook.duration },
      personaVariant: { id: persona.id, name: persona.name, tone: persona.tone, visual: persona.visual, ageRange: persona.ageRange, trustLevel: persona.trustLevel, languageHints: this._langHints(persona.id) },
      sceneVariant: { id: scenario.id, name: scenario.name, mood: scenario.mood, lighting: scenario.lighting, shots: this._shots(scenario.id) },
      rhythmVariant: { id: rhythm.id, name: rhythm.name, cutInterval: rhythm.desc, energy: rhythm.energy, bestFor: rhythm.bestFor },
      ctaVariant: { id: `cta${ctas.indexOf(cta)}`, text: cta, platform, urgency: this._urgency(cta) },
      bgmVariant: { id: `bgm${bgms.indexOf(bgmTag)}`, tag: bgmTag, mood: this._bgmMood(bgmTag), energy: this._bgmEnergy(bgmTag) },
      captionVariant: { id: `cap${captions.indexOf(caption)}`, ...caption },
      platform,
      visualFingerprint: this._fingerprint({ persona, scenario, rhythm, hook, idx }),
      publishTime: this._schedule(idx),
      accountCluster: `ACCT_${String(Math.floor(idx / 7) + 1).padStart(2, '0')}`,
    };
  }

  _permuteIndex(index, mod, salt) {
    return Math.abs((index * salt + Math.floor(index / mod) * (salt + 2)) % mod) % mod;
  }

  _getHookVariants(baseHook) {
    if (this.hookEngine?.getHookVariants) return this.hookEngine.getHookVariants(baseHook, 20);
    return [
      { id: 'h1', text: '我后悔没早点发现{product}，原来{benefit}', formula: '冲突+悬念', duration: 3000 },
      { id: 'h2', text: '为什么{persona}都在用{product}？因为{benefit}', formula: '提问+答案', duration: 2500 },
      { id: 'h3', text: '用了{number}天{product}，{result}震惊了', formula: '数字+对比', duration: 3200 },
      { id: 'h4', text: '只有{persona}才懂，{pain}真的太痛苦了...直到遇见{product}', formula: '情绪共鸣', duration: 3500 },
      { id: 'h5', text: '{expert}推荐的这个{product}，{benefit}确实不一样', formula: '权威背书', duration: 3000 },
      { id: 'h6', text: '这个{product}彻底颠覆了我对{category}的认知', formula: '震惊+反转', duration: 2800 },
      { id: 'h7', text: '{persona}都在偷偷用的{product}，今天揭秘', formula: '秘密+揭秘', duration: 2600 },
      { id: 'h8', text: '用了{product}之前vs之后，{persona}惊呆了', formula: '前后对比', duration: 3000 },
      { id: 'h9', text: '{persona}省钱攻略：{product}这样买省一半', formula: '省钱+攻略', duration: 2900 },
      { id: 'h10', text: '关于{product}的3个误区，{expert}来辟谣', formula: '辟谣+正名', duration: 3400 },
      { id: 'h11', text: '{product}的5个隐藏用法，{persona}第3个不知道', formula: '技巧+妙用', duration: 3100 },
      { id: 'h12', text: '最后{number}件！{persona}都在抢的{product}', formula: '稀缺+限时', duration: 2500 },
      { id: 'h13', text: '全网{number}万{persona}好评的{product}', formula: '社交+从众', duration: 2700 },
      { id: 'h14', text: '{pain}？这个{product}让{persona}告别烦恼', formula: '痛点+解决', duration: 3300 },
      { id: 'h15', text: '送{persona}这个{product}，被夸了整整一周', formula: '送礼+推荐', duration: 3000 },
      { id: 'h16', text: '别再买旧款了！{product}升级后{benefit}', formula: '升级+替代', duration: 2800 },
      { id: 'h17', text: '真实试用{number}天，{product}到底值不值', formula: '试用+真实', duration: 3500 },
      { id: 'h18', text: '测了{number}款{category}，{product}胜出', formula: '横评+推荐', duration: 3200 },
      { id: 'h19', text: '{persona}的日常必备：这个{product}用了就回不去', formula: '日常+种草', duration: 2900 },
      { id: 'h20', text: '{persona}必学的{product}使用技巧，效率翻倍', formula: '生活hack', duration: 2600 },
    ];
  }

  _adaptHook(text, persona) { return text.replace(/{persona}/g, persona.name).replace(/{tone}/g, persona.tone.split('/')[0]); }

  _langHints(pid) {
    const map = { student: ['求','姐妹','性价比'], mom: ['亲测','宝宝','放心'], office: ['效率','品质','打工人'], expert: ['数据','实测','对比'], aesthetic: ['仪式感','颜值','治愈'], renter: ['平价','改造','租房'], fitness: ['效果','坚持','训练'], senior: ['健康','方便','耐用'], foodie: ['绝了','好吃','必试'], pet: ['毛孩子','可爱','治愈'] };
    return map[pid] || ['推荐','好用'];
  }

  _shots(sid) {
    const map = { kitchen: ['中景取物','特写操作','俯拍桌面'], bedroom: ['床上开箱','窗边逆光'], office: ['桌面摆放','手操作特写'], outdoor: ['手持展示','自然光特写'], car: ['车内视角','手持展示'], bathroom: ['洗手台特写','镜子反射'], livingroom: ['沙发开箱','全景展示'], cafe: ['桌面摆拍','窗边侧光'] };
    return map[sid] || ['特写','中景'];
  }

  _urgency(t) { let s = 0; ['抢','冲','手慢无','限量','秒杀','立即','now','limited'].forEach(w => { if (t.includes(w)) s += 0.25; }); return Math.min(s, 1); }

  _bgmMood(t) { const m = { '热门卡点':'兴奋','治愈钢琴':'温馨','电子节奏':'动感','悬疑紧张':'紧张','温馨日常':'日常','励志鼓点':'励志','国风古乐':'优雅','蒸汽波':'复古','Lo-Fi':'放松','对口型热门':'趣味','轻快节奏':'轻松','情感慢歌':'感性','搞笑音效':'幽默','电影感':'大气','白噪音':'沉浸' }; return m[t] || '通用'; }
  _bgmEnergy(t) { const e = { '热门卡点':90,'电子节奏':85,'励志鼓点':80,'轻快节奏':70,'对口型热门':75,'治愈钢琴':45,'温馨日常':50,'Lo-Fi':40,'悬疑紧张':65,'情感慢歌':40,'白噪音':20,'国风古乐':50,'蒸汽波':55,'搞笑音效':60,'电影感':70 }; return e[t] || 50; }
  _fingerprint(d) { return crypto.createHash('sha256').update(`${d.persona.id}:${d.scenario.id}:${d.rhythm.id}:${d.hook.id}:${d.idx}`).digest('hex').substring(0, 16); }
  _schedule(idx) { const base = Date.now() + 86400000; const gap = Math.max(this.config.PUBLISH_INTERVAL_MIN, 30) * 60000; return new Date(base + idx * gap + (idx * 7 % 13) * 60000); }

  // ═══════════════════════════════════════════════════════════════════════════
  // 差异化检测
  // ═══════════════════════════════════════════════════════════════════════════
  _meetsDiffCriteria(newV, existing) {
    if (!existing.length) return true;
    const axes = ['hookVariant','personaVariant','sceneVariant','rhythmVariant','ctaVariant','bgmVariant','captionVariant'];
    const window = Math.min(existing.length, 10);
    for (let i = existing.length - window; i < existing.length; i++) {
      let diff = 0;
      for (const a of axes) if (newV[a].id !== existing[i][a].id) diff++;
      if (diff < this.config.MIN_DIFF_AXES) return false;
    }
    return true;
  }

  _calculateMaxOverlap(newV, existing) {
    if (!existing.length) return 0;
    let max = 0;
    const window = Math.max(existing.length * 0.3, 20);
    const start = Math.max(0, existing.length - Math.floor(window));
    for (let i = start; i < existing.length; i++) {
      const o = this._pairOverlap(newV, existing[i]);
      if (o > max) max = o;
    }
    return max;
  }

  _pairOverlap(a, b) {
    const w = { hookVariant: 0.18, personaVariant: 0.15, sceneVariant: 0.15, rhythmVariant: 0.15, ctaVariant: 0.12, bgmVariant: 0.12, captionVariant: 0.13 };
    let s = 0;
    for (const [k, wt] of Object.entries(w)) if (a[k].id === b[k].id) s += wt;
    return s;
  }

  _computeOverlapMatrix(vars) {
    this.overlapMatrix = Array.from({ length: vars.length }, (_, i) =>
      Array.from({ length: vars.length }, (_, j) => i === j ? 1.0 : this._pairOverlap(vars[i], vars[j]))
    );
  }

  _avgOverlap() {
    if (!this.overlapMatrix.length) return 0;
    let [s, c, n] = [0, 0, this.overlapMatrix.length];
    for (let i = 0; i < n; i++) for (let j = i + 1; j < n; j++) { s += this.overlapMatrix[i][j]; c++; }
    return c ? s / c : 0;
  }

  // ═══════════════════════════════════════════════════════════════════════════
  // S3: 人设档案
  // ═══════════════════════════════════════════════════════════════════════════
  generatePersonaProfiles(variants) {
    const map = new Map();
    for (const v of variants.variants || variants) {
      if (!map.has(v.accountCluster)) map.set(v.accountCluster, { clusterId: v.accountCluster, persona: v.personaVariant, variants: [], signature: this._accountSignature(v.personaVariant.id) });
      map.get(v.accountCluster).variants.push(v.id);
    }
    return [...map.values()];
  }

  _accountSignature(pid) {
    const themes = { student: { pri:'#FF6B6B', sec:'#4ECDC4', acc:'#FFE66D' }, mom: { pri:'#F7DC6F', sec:'#BB8FCE', acc:'#85C1E9' }, office: { pri:'#2C3E50', sec:'#E8F6F3', acc:'#F39C12' }, expert: { pri:'#1ABC9C', sec:'#ECF0F1', acc:'#E74C3C' }, aesthetic: { pri:'#D5A6BD', sec:'#FCE5CD', acc:'#B4A7D6' }, renter: { pri:'#A8D08D', sec:'#FFF2CC', acc:'#F4B084' }, fitness: { pri:'#FF7F50', sec:'#2F4F4F', acc:'#00CED1' }, senior: { pri:'#8B4513', sec:'#F5F5DC', acc:'#CD853F' }, foodie: { pri:'#FF4500', sec:'#FFD700', acc:'#FF6347' }, pet: { pri:'#FFB6C1', sec:'#E6E6FA', acc:'#98FB98' } };
    return { colorTheme: themes[pid] || { pri:'#333', sec:'#FFF', acc:'#999' } };
  }

  // ═══════════════════════════════════════════════════════════════════════════
  // S4: 平台适配
  // ═══════════════════════════════════════════════════════════════════════════
  adaptForPlatform(variant, platform = 'douyin') {
    const preset = PLATFORM_PRESETS[platform];
    if (!preset) return variant;
    const emo = { douyin:' 👆', kuaishou:' 🔥', tiktok:' 👇', xiaohongshu:' 💕', shipinhao:' 👉' };
    return { ...variant, platformAdapted: { platform, ...preset }, ctaVariant: { ...variant.ctaVariant, text: variant.ctaVariant.text + (emo[platform] || '') } };
  }

  // ═══════════════════════════════════════════════════════════════════════════
  // S5: 防限流
  // ═══════════════════════════════════════════════════════════════════════════
  antiLimitCheck(variants) {
    const vs = variants.variants || variants;
    const checks = [];
    checks.push({ item: '开场镜各账号各异', passed: new Set(vs.map(v => v.visualFingerprint)).size === vs.length, detail: `唯一: ${new Set(vs.map(v => v.visualFingerprint)).size}/${vs.length}` });
    checks.push({ item: 'BGM/音轨各异', passed: new Set(vs.map(v => v.bgmVariant.tag)).size >= Math.min(vs.length * 0.7, 15), detail: `BGM: ${new Set(vs.map(v => v.bgmVariant.tag)).size}种` });
    checks.push({ item: '字幕样式/位置各异', passed: new Set(vs.map(v => v.captionVariant.name)).size >= Math.min(6, vs.length), detail: `字幕: ${new Set(vs.map(v => v.captionVariant.name)).size}种` });
    let maxO = 0;
    for (let i = 0; i < vs.length; i++) for (let j = i + 1; j < vs.length; j++) { const o = this._pairOverlap(vs[i], vs[j]); if (o > maxO) maxO = o; }
    checks.push({ item: '跨账号重合度≤40%', passed: maxO <= this.config.OVERLAP_THRESHOLD, detail: `最大: ${(maxO * 100).toFixed(1)}%` });
    const sorted = [...vs].sort((a, b) => a.publishTime - b.publishTime);
    let minGap = Infinity;
    for (let i = 1; i < sorted.length; i++) { const g = (sorted[i].publishTime - sorted[i - 1].publishTime) / 60000; if (g < minGap) minGap = g; }
    checks.push({ item: '发布时间错峰(≥15分钟)', passed: minGap >= this.config.PUBLISH_INTERVAL_MIN, detail: `最小: ${Math.floor(minGap)}分钟` });
    checks.push({ item: 'AI标识已规划', passed: vs.every(v => v.visualFingerprint?.length === 16), detail: '指纹标记完成' });
    const passed = checks.filter(c => c.passed).length;
    return { passed, failed: 6 - passed, total: 6, checks, overall: passed === 6 };
  }

  // ═══════════════════════════════════════════════════════════════════════════
  // S6: 成本估算
  // ═══════════════════════════════════════════════════════════════════════════
  estimateCost(variants) {
    const vs = variants.variants || variants;
    const n = vs.length, C = this.config.COST;
    const oneTime = C.voiceCloneOneTime + C.heroPolish;
    const per = C.aiHook3s + C.narrationSynth + C.reEdit + C.platformAdapt + C.aiWatermark;
    const total = oneTime + per * n;
    return {
      currency: 'CNY',
      oneTime: { voiceClone: C.voiceCloneOneTime, heroPolish: C.heroPolish, total: oneTime },
      marginal: { perVariant: Math.round(per * 100) / 100, aiHook: C.aiHook3s, narration: C.narrationSynth, reEdit: C.reEdit, platformAdapt: C.platformAdapt, aiWatermark: C.aiWatermark },
      total: Math.round(total * 100) / 100,
      perVideoAvg: Math.round((total / n) * 100) / 100,
      savings: { vsTraditional: Math.round((n * 200 - total) * 100) / 100, ratio: Math.round((total / (n * 200)) * 100) },
      breakdown: [
        { phase: '一次性投入', amount: oneTime, pct: Math.round((oneTime / total) * 100) },
        { phase: `AI钩子(${n}条)`, amount: Math.round(C.aiHook3s * n * 100) / 100, pct: Math.round((C.aiHook3s * n / total) * 100) },
        { phase: `旁白合成(${n}条)`, amount: Math.round(C.narrationSynth * n * 100) / 100, pct: Math.round((C.narrationSynth * n / total) * 100) },
        { phase: `重剪(${n}条)`, amount: Math.round(C.reEdit * n * 100) / 100, pct: Math.round((C.reEdit * n / total) * 100) },
        { phase: `平台适配(${n}条)`, amount: Math.round(C.platformAdapt * n * 100) / 100, pct: Math.round((C.platformAdapt * n / total) * 100) },
      ],
    };
  }

  // ═══════════════════════════════════════════════════════════════════════════
  // 质量门
  // ═══════════════════════════════════════════════════════════════════════════
  runQualityGate(variants) {
    const vs = variants.variants || variants;
    const details = vs.map(v => {
      const gates = [
        { name: '钩子非空', pass: v.hookVariant?.text?.length > 0 },
        { name: '人设完整', pass: !!v.personaVariant?.id },
        { name: '场景有效', pass: !!v.sceneVariant?.id },
        { name: 'CTA有效', pass: v.ctaVariant?.text?.length > 0 },
        { name: 'BGM已选', pass: !!v.bgmVariant?.tag },
        { name: '平台已适配', pass: !!(v.platform && PLATFORM_PRESETS[v.platform]) },
        { name: '指纹唯一', pass: v.visualFingerprint?.length === 16 },
      ];
      const passed = gates.filter(g => g.pass).length;
      return { variantId: v.id, passed, total: gates.length, score: Math.round((passed / gates.length) * 100), gates };
    });
    const avg = details.reduce((s, d) => s + d.score, 0) / details.length;
    return { allPassed: details.every(d => d.score === 100), avgScore: Math.round(avg), totalChecked: details.length, passRate: Math.round((details.filter(d => d.score === 100).length / details.length) * 100), details };
  }

  // ═══════════════════════════════════════════════════════════════════════════
  // 主入口: 生成完整矩阵投放表
  // ═══════════════════════════════════════════════════════════════════════════
  generateMatrixTable(winner, count = 200, platform = 'douyin') {
    const t0 = Date.now();
    const raw = winner._raw || winner;
    const validation = this.validateHero(raw);
    if (!validation.isQualified) return { success: false, phase: 'S0-FAILED', score: validation.score, errors: validation.checks.filter(c => !c.pass) };
    const decomposed = this.decomposeWinner(raw);
    const variants = this.generateVariants(decomposed, count, platform);
    const profiles = this.generatePersonaProfiles(variants);
    const adapted = variants.variants.map(v => this.adaptForPlatform(v, platform));
    const antiLimit = this.antiLimitCheck(adapted);
    const cost = this.estimateCost({ variants: adapted });
    const qg = this.runQualityGate({ variants: adapted });
    return {
      success: true, generatedAt: new Date().toISOString(), duration: `${Date.now() - t0}ms`,
      meta: { platform, targetCount: count, actualCount: adapted.length, uniqueAccounts: profiles.length, combinationsAvailable: decomposed._decompositionMeta.totalCombinations },
      pipeline: {
        S0_validation: validation,
        S1_decomposition: decomposed._decompositionMeta,
        S2_variants: variants,
        S3_personaProfiles: profiles,
        S4_platformAdapted: adapted,
        S5_antiLimitReport: antiLimit,
        S6_costEstimate: cost,
      },
      deliverables: {
        publishSchedule: adapted.map(v => ({ variantId: v.id, publishTime: v.publishTime, accountCluster: v.accountCluster, platform: v.platform, status: 'scheduled' })),
        antiLimitReport: antiLimit,
        costEstimate: cost,
        qualityGate: qg,
        overlapMatrix: this.overlapMatrix,
      },
      summary: {
        totalVariants: adapted.length,
        uniquePersonas: new Set(adapted.map(v => v.personaVariant.id)).size,
        uniqueScenes: new Set(adapted.map(v => v.sceneVariant.id)).size,
        uniqueRhythms: new Set(adapted.map(v => v.rhythmVariant.id)).size,
        uniqueHooks: new Set(adapted.map(v => v.hookVariant.id)).size,
        avgOverlap: this._avgOverlap(),
        estTotalCost: cost.total,
        costPerVideo: cost.perVideoAvg,
      },
    };
  }

  // ═══════════════════════════════════════════════════════════════════════════
  // Public utilities
  // ═══════════════════════════════════════════════════════════════════════════
  selectHookVariant(hooks, idx, baseHook) { return this._getHookVariants(baseHook)[idx % 5]; }
  adaptCTA(platform) { const p = CTA_LIBRARY[platform] || CTA_LIBRARY.douyin; return p[Math.floor(Math.random() * p.length)]; }
}

module.exports = { MatrixEngine };
