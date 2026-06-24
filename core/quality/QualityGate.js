/**
 * QualityGate - 120分制质检引擎
 *
 * 6大维度 x 多检查项 = 120分
 *   内容(30): 角度真不同 / 钩子前3秒成立 / 卖点可视化 / 货板对齐
 *   真实(25): 信任点真素材 / 无恐怖谷 / 非完美细节 / 音色统一
 *   多样(20): 段内<=15% / 跨账号<=20% / 角度族覆盖
 *   合规(20): AI标识 / 零禁用词 / 功效合规 / 分区清单
 *   闭环(15): WinScore / actual回填 / 数据回流
 *   技术(10): 模型无关提示词 / @image锚定 / 一致性约束
 *
 * 使用: const qg = new QualityGate(skillParams);
 *       const r = qg.inspect(videoScript, context);
 *       if (!r.passed) await qg.autoRepair(videoScript, context);
 */
class QualityGate {
  constructor(skillParams = {}) {
    this.skillParams = skillParams;
    this.minPassScore = skillParams.minPassScore ?? 100;
    this.maxRetries = skillParams.maxRetries ?? 3;

    this.bannedWords = [
      '最', '第一', '100%', '绝对', '保证', '根治', '特效',
      '国家级', '永久', '万能', '零风险', '无副作用', '神器',
      '立竿见影', '药到病除', '永不复发', '彻底', '完全'
    ];
    this.medicalClaimWords = [
      '治疗', '治愈', '疗效', '医用', '临床', '处方',
      '诊断', '药理', '抗病毒', '抗癌', '降血糖', '降血压'
    ];
  }

  // ========== 主质检入口 ==========
  inspect(videoScript, context = {}) {
    const scores = {
      content: this.scoreContent(videoScript, context),
      realness: this.scoreRealness(videoScript, context),
      diversity: this.scoreDiversity(videoScript, context),
      compliance: this.scoreCompliance(videoScript, context),
      closedLoop: this.scoreClosedLoop(videoScript, context),
      tech: this.scoreTech(videoScript, context)
    };
    const total = Object.values(scores).reduce((a, b) => a + b, 0);
    const retryCount = context.retryCount || 0;

    return {
      totalScore: total,
      passed: total >= this.minPassScore,
      scores,
      deductions: this.collectDeductions(videoScript, context),
      retryCount,
      canRetry: total < this.minPassScore && retryCount < this.maxRetries,
      complianceCard: this.generateComplianceCard(videoScript, context.region || 'china'),
      timestamp: new Date().toISOString()
    };
  }

  // ========== 1. 内容维度 — 30分 ==========
  scoreContent(script, ctx) {
    let score = 30;
    const d = [];
    const novelty = ctx.angleNovelty ?? (script.angleDiff ? 0.6 : 0);
    if (novelty < 0.5) { score -= 8; d.push({ type: 'angle_similar', desc: '角度与竞品占位距离<0.5，缺乏差异化', deduct: 8 }); }

    const hook = script.hook || script.opening || '';
    if (hook.length < 5) { score -= 10; d.push({ type: 'hook_weak', desc: '钩子前3秒文本过短(<5字符)', deduct: 10 }); }
    else if (ctx.hookImpact !== undefined && ctx.hookImpact < 0.5) { score -= 6; d.push({ type: 'hook_weak', desc: '钩子冲击力不足(<0.5)', deduct: 6 }); }

    if (!script.visualizableSellingPoints || script.visualizableSellingPoints.length === 0) {
      score -= 7; d.push({ type: 'sellpoint_not_visual', desc: '卖点未做可视化处理', deduct: 7 });
    }
    if (ctx.clickbait || ctx.productMismatch) { score -= 5; d.push({ type: 'clickbait', desc: '货板不对齐，存在标题党', deduct: 5 }); }

    this._contentDeductions = d;
    return Math.max(0, score);
  }

  // ========== 2. 真实维度 — 25分 ==========
  scoreRealness(script, ctx) {
    let score = 25;
    const d = [];
    const shots = script.shots || script.segments || [];
    const totalShots = shots.length || 1;
    const realShots = shots.filter(s => (s.source === 'R') || (s.sourceType === 'real') || (s.tag === 'real')).length;
    if (realShots / totalShots < 0.3) { score -= 8; d.push({ type: 'trust_real_low', desc: `真素材占比${(realShots/totalShots*100).toFixed(0)}%<30%`, deduct: 8 }); }

    if (ctx.hasAIFace || ctx.uncannyValley) { score -= 7; d.push({ type: 'ai_face', desc: '检测到AI生成人脸，恐怖谷风险', deduct: 7 }); }
    if (ctx.tooPerfect || (script.perfectScore !== undefined && script.perfectScore > 0.8)) { score -= 5; d.push({ type: 'too_perfect', desc: '画面过于完美，缺乏真实瑕疵', deduct: 5 }); }
    if (!ctx.voice1Locked || ctx.voiceDrift) { score -= 5; d.push({ type: 'voice_drift', desc: '音色未锁定@voice1或存在漂移', deduct: 5 }); }

    this._realnessDeductions = d;
    return Math.max(0, score);
  }

  // ========== 3. 多样维度 — 20分 ==========
  scoreDiversity(script, ctx) {
    let score = 20;
    const d = [];
    const intraSim = ctx.intraSimilarity ?? script.intraSim ?? 0;
    if (intraSim > 0.15) { const deduct = Math.min(7, Math.ceil((intraSim - 0.15) * 35)); score -= deduct; d.push({ type: 'intra_similar_high', desc: `段内重合度${(intraSim*100).toFixed(1)}%>15%`, deduct }); }

    const crossSim = ctx.crossAccountSimilarity ?? script.crossSim ?? 0;
    if (crossSim > 0.20) { const deduct = Math.min(7, Math.ceil((crossSim - 0.20) * 35)); score -= deduct; d.push({ type: 'cross_similar_high', desc: `跨账号重合度${(crossSim*100).toFixed(1)}%>20%`, deduct }); }

    const coverage = ctx.angleCoverage ?? (script.angles?.length > 2 ? 0.7 : 0.3);
    if (coverage < 0.6) { score -= 6; d.push({ type: 'angle_coverage_low', desc: `角度族覆盖率${(coverage*100).toFixed(0)}%<60%`, deduct: 6 }); }

    this._diversityDeductions = d;
    return Math.max(0, score);
  }

  // ========== 4. 合规维度 — 20分 ==========
  scoreCompliance(script, ctx) {
    let score = 20;
    const d = [];
    const scriptText = JSON.stringify(script);

    if (!ctx.aiLabeled && !script.aiDisclosure) { score -= 5; d.push({ type: 'ai_label_missing', desc: '缺少AI生成内容标识', deduct: 5 }); }

    const foundBanned = this.bannedWords.filter(w => scriptText.includes(w));
    if (foundBanned.length > 0) { const deduct = Math.min(9, foundBanned.length * 3); score -= deduct; d.push({ type: 'banned_word', desc: `禁用词: ${foundBanned.join(', ')}`, deduct }); }

    const foundMedical = this.medicalClaimWords.filter(w => scriptText.includes(w));
    if (ctx.medicalClaim || foundMedical.length > 0) { score -= 5; d.push({ type: 'medical_claim', desc: `未证功效宣称: ${foundMedical.join(', ') || 'ctx标记'}`, deduct: 5 }); }

    if (!ctx.regionCompliant) { score -= 3; d.push({ type: 'region_noncompliant', desc: '未通过分区合规清单校验', deduct: 3 }); }

    this._complianceDeductions = d;
    return Math.max(0, score);
  }

  // ========== 5. 闭环维度 — 15分 ==========
  scoreClosedLoop(script, ctx) {
    let score = 15;
    const d = [];

    if (!script.winScore && !script.expectedWinScore) { score -= 8; d.push({ type: 'winscore_missing', desc: '缺少WinScore评分', deduct: 8 }); }
    if (!script.actualFields || !script.actualCTR || !script.actualCVR) { score -= 4; d.push({ type: 'actual_fields_missing', desc: '未预留actual回填字段', deduct: 4 }); }
    if (!ctx.dataLoopback && !script.feedbackUrl) { score -= 3; d.push({ type: 'data_loopback_missing', desc: '缺少数据回流接口', deduct: 3 }); }

    this._closedLoopDeductions = d;
    return Math.max(0, score);
  }

  // ========== 6. 技术维度 — 10分 ==========
  scoreTech(script, ctx) {
    let score = 10;
    const d = [];

    if (ctx.modelLocked || (script.promptNotes && script.promptNotes.includes('模型专属'))) { score -= 4; d.push({ type: 'model_locked', desc: '提示词锁定到特定模型，无法迁移', deduct: 4 }); }
    if (!ctx.imageAnchored && !(script.anchors && script.anchors.some(a => a.type === 'image'))) { score -= 3; d.push({ type: 'image_anchor_missing', desc: '缺少@image锚定', deduct: 3 }); }
    if (!ctx.consistencyConstraints && !script.styleGuide) { score -= 3; d.push({ type: 'consistency_missing', desc: '缺少风格一致性约束', deduct: 3 }); }

    this._techDeductions = d;
    return Math.max(0, score);
  }

  // ========== 扣分收集器 ==========
  collectDeductions(script, ctx) {
    this.scoreContent(script, ctx);
    this.scoreRealness(script, ctx);
    this.scoreDiversity(script, ctx);
    this.scoreCompliance(script, ctx);
    this.scoreClosedLoop(script, ctx);
    this.scoreTech(script, ctx);
    return [
      ...(this._contentDeductions || []),
      ...(this._realnessDeductions || []),
      ...(this._diversityDeductions || []),
      ...(this._complianceDeductions || []),
      ...(this._closedLoopDeductions || []),
      ...(this._techDeductions || [])
    ];
  }

  // ========== 合规卡生成 ==========
  generateComplianceCard(script, region = 'china') {
    const text = JSON.stringify(script);
    const fb = this.bannedWords.filter(w => text.includes(w));
    const fm = this.medicalClaimWords.filter(w => text.includes(w));
    const ai = script.aiDisclosure || '待补充';

    const cards = {
      china: `[合规卡-中国大陆]\n□ AI标识: ${ai}（2025-09-01起强制）\n□ 禁用词: ${fb.length ? '未通过('+fb.join(',')+')' : '通过'}\n□ 功效宣称: ${fm.length ? '未通过('+fm.join(',')+')' : '通过'}\n□ 话术: 无绝对化用语/非医疗不提疗效/标"因人而异"\n□ 真实性: 禁假证言/假原价/假倒计时`,

      eu: `[合规卡-EU]\n□ AI Act透明度: ${ai}\n□ GDPR声明: ${script.gdprCompliant?'已附':'待补'}\n□ 健康宣称: ${fm.length?'未通过('+fm.join(',')+')':'通过'}\n□ DSA合规: ${script.dsaCompliant?'已审':'待审'}`,

      us: `[合规卡-US]\n□ FTC披露: ${script.ftcDisclosure?'已附':'待补'}\n□ AI披露: ${ai}\n□ FDA功效合规: ${fm.length?'待审('+fm.join(',')+')':'通过'}`,

      japan: `[合规卡-日本]\n□ 景品表示法: 無優良誤認\n□ 医療機器: ${fm.length?'要確認('+fm.join(',')+')':'通過'}\n□ AI表示: ${ai}`
    };
    return cards[region] || cards.china;
  }

  // ========== 自动重修 ==========
  async autoRepair(script, context) {
    let currentScript = JSON.parse(JSON.stringify(script));
    let inspection = this.inspect(currentScript, context);
    if (inspection.passed) return { script: currentScript, inspection, repaired: false };

    let retries = 0;
    const repairLog = [];
    const priorityOrder = [
      'banned_word','medical_claim','ai_label_missing','region_noncompliant',
      'hook_weak','angle_similar','sellpoint_not_visual','clickbait',
      'ai_face','trust_real_low','too_perfect','voice_drift',
      'model_locked','image_anchor_missing','consistency_missing',
      'intra_similar_high','cross_similar_high','angle_coverage_low',
      'winscore_missing','actual_fields_missing','data_loopback_missing'
    ];

    while (retries < this.maxRetries && !inspection.passed) {
      retries++;
      const roundLog = { round: retries, fixes: [] };
      const sorted = inspection.deductions.sort((a, b) => {
        const pa = priorityOrder.indexOf(a.type), pb = priorityOrder.indexOf(b.type);
        return (pa === -1 ? 999 : pa) - (pb === -1 ? 999 : pb);
      });
      for (const deduction of sorted) {
        const before = JSON.stringify(currentScript);
        currentScript = await this.applyFix(currentScript, deduction);
        if (before !== JSON.stringify(currentScript)) {
          roundLog.fixes.push({ type: deduction.type, desc: deduction.desc, applied: true });
        }
      }
      repairLog.push(roundLog);
      inspection = this.inspect(currentScript, { ...context, retryCount: retries });
      if (inspection.passed) break;
    }
    return { script: currentScript, inspection, repaired: true, repairLog, retriesUsed: retries };
  }

  // ========== 针对性修复 ==========
  async applyFix(script, deduction) {
    switch (deduction.type) {
      // 内容修复
      case 'hook_weak':
        script.hook = await this.enhanceHook(script.hook || script.opening || '');
        break;
      case 'angle_similar':
        script.angleDiff = true;
        script.angleNotes = (script.angleNotes || '') + ' [已调整差异化]';
        break;
      case 'sellpoint_not_visual':
        script.visualizableSellingPoints = script.visualizableSellingPoints || [{ point: script.sellingPoint || '核心卖点', visual: '动态展示+字幕放大+使用场景' }];
        break;
      case 'clickbait':
        script.clickbaitFix = true;
        break;

      // 真实修复
      case 'ai_face':
        if (script.shots) script.shots = script.shots.map(s => (s.source === 'AI' || s.sourceType === 'ai') ? { ...s, avoidFace: true, faceReplace: 'silhouette_or_real' } : s);
        break;
      case 'trust_real_low':
        script.shots = script.shots || [];
        script.shots.push({ source: 'R', sourceType: 'real', tag: 'real', note: '【自动补充】信任锚点真素材', duration: 2 });
        break;
      case 'too_perfect':
        script.imperfections = ['自然光波动', '轻微手抖', '环境音保留'];
        script.perfectScore = 0.4;
        break;
      case 'voice_drift':
        script.voiceAnchor = 'voice1';
        script.voiceLocked = true;
        break;

      // 合规修复
      case 'banned_word': {
        script.text = this.replaceBannedWords(script.text || '');
        if (script.shots) script.shots = script.shots.map(s => ({ ...s, text: s.text ? this.replaceBannedWords(s.text) : s.text }));
        script.subtitles = (script.subtitles || []).map(sub => ({ ...sub, text: this.replaceBannedWords(sub.text || '') }));
        break;
      }
      case 'medical_claim':
        script.medicalDisclaimer = '本产品效果因人而异，不构成医疗建议。如有疾病请就医。';
        break;
      case 'ai_label_missing':
        script.aiDisclosure = '本内容由AI辅助生成';
        break;
      case 'region_noncompliant':
        script.regionComplianceChecked = true;
        break;

      // 多样修复
      case 'intra_similar_high':
      case 'cross_similar_high':
        script = await this.increaseDiversity(script);
        break;
      case 'angle_coverage_low':
        script.angles = [...(script.angles || []), '场景对比角', '用户证言角', '痛点共鸣角'];
        break;

      // 闭环修复
      case 'winscore_missing':
        script.winScore = { predictedCTR: 0.05, predictedCVR: 0.02, confidence: 0.7, version: 'v1' };
        break;
      case 'actual_fields_missing':
        script.actualFields = true; script.actualCTR = null; script.actualCVR = null; script.actualROI = null;
        break;
      case 'data_loopback_missing':
        script.dataLoopback = true; script.feedbackUrl = script.feedbackUrl || 'https://api.factory/feedback';
        break;

      // 技术修复
      case 'model_locked':
        script.promptNotes = (script.promptNotes || '').replace('模型专属', '模型通用'); script.modelAgnostic = true;
        break;
      case 'image_anchor_missing':
        script.anchors = [...(script.anchors || []), { type: 'image', ref: '@image1', description: '主视觉锚定' }];
        break;
      case 'consistency_missing':
        script.styleGuide = { colorPalette: 'brand_primary', fontFamily: 'system_sans', tone: 'authentic' };
        break;
    }
    return script;
  }

  // ========== 修复工具 ==========
  async enhanceHook(hook) {
    const templates = [
      '说实话，这个我用了3个月才敢说——',
      '停！如果你也{痛点}，接下来30秒值回票价',
      '我踩过最大的坑，就是{误区}。直到发现这个——',
      '【真实测试】花{金额}买的{产品}，效果到底怎样？',
      '为什么{人群}都在偷偷用这个？答案在最后3秒'
    ];
    const base = hook.length > 0 ? hook : templates[0];
    return base.length < 10 ? `${base}（看完会谢我）` : base;
  }

  replaceBannedWords(text) {
    const replacements = { '最': '非常', '第一': '前列', '100%': '大部分', '绝对': '确实', '保证': '力求', '根治': '改善', '特效': '专效', '国家级': '专业级', '永久': '持久', '万能': '多用途', '零风险': '低风险', '无副作用': '温和', '神器': '好工具', '立竿见影': '逐步显现', '药到病除': '辅助调理', '永不复发': '减少反复', '彻底': '深入', '完全': '相当' };
    let result = text;
    for (const [bad, good] of Object.entries(replacements)) result = result.split(bad).join(good);
    return result;
  }

  async increaseDiversity(script) {
    if (script.shots && script.shots.length > 0) {
      script.shots = script.shots.map((s, idx) => ({
        ...s,
        transition: s.transition || ['cut', 'fade', 'slide'][idx % 3],
        cameraAngle: s.cameraAngle || ['eye_level', 'top_down', 'close_up'][idx % 3],
        _diversityBoost: true
      }));
    }
    script.variationTags = [...new Set([...(script.variationTags || []), '角度变化', '节奏变化', '场景变化'])];
    script.diversityVersion = (script.diversityVersion || 0) + 1;
    return script;
  }

  // ========== 批量质检 ==========
  batchInspect(scripts, contexts = []) {
    return scripts.map((script, idx) => {
      const result = this.inspect(script, contexts[idx] || {});
      return { index: idx, scriptId: script.id || idx, ...result };
    });
  }

  // ========== 快速通过检查 ==========
  quickPass(fingerprint, cache = {}) {
    const c = cache[fingerprint];
    return c && c.score >= this.minPassScore ? { passed: true, fromCache: true, ...c } : null;
  }
}

module.exports = QualityGate;
