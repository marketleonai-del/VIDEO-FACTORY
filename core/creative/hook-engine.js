/**
 * VIDEO-FACTORY :: HookEngine v1.0.0 — 钩子角度生成引擎
 * 6大角度族 + 10大钩子族(80条) → 最优带货角度卡
 */
const WIN_SCORE_WEIGHTS = {
  novelty: 0.25, painIntensity: 0.20, hookStrength: 0.20,
  platformFit: 0.15, trustDensity: 0.10, visualScore: 0.10,
};
const SIMILARITY_WEIGHTS = { structure: 0.40, framework: 0.35, style: 0.25 };
const DIVERSITY_THRESHOLD = 0.15;

const PLATFORM_PROFILE = {
  douyin:      { hookStyle: ['反常识','悬念','视觉冲击'], maxLen: 28, emojiOK: true,  urgency: 0.9 },
  kuaishou:    { hookStyle: ['真实','信任','利益前置'],   maxLen: 32, emojiOK: true,  urgency: 0.85 },
  xiaohongshu: { hookStyle: ['情绪共鸣','身份','清单'],   maxLen: 40, emojiOK: true,  urgency: 0.7 },
  shipinhao:   { hookStyle: ['信任','数字','对比'],        maxLen: 36, emojiOK: false, urgency: 0.8 },
  baidu:       { hookStyle: ['专业','数字','互动提问'],     maxLen: 30, emojiOK: false, urgency: 0.75 },
};

class HookEngine {
  constructor(skillParams = {}) {
    this.skillParams = skillParams;
    this.weights = { ...WIN_SCORE_WEIGHTS, ...(skillParams.winWeights || {}) };
    this.angleFamilies = this._buildAngleFamilies();
    this.hookLibrary = this._buildHookLibrary();
    this.platformProfile = { ...PLATFORM_PROFILE, ...(skillParams.platformProfile || {}) };
    this.seenPatterns = new Set();
  }

  // ═══════════════════════════════════════════
  //  6 大角度族
  // ═══════════════════════════════════════════
  _buildAngleFamilies() {
    return {
      A: { id:'A', name:'痛点放大', tag:'你是不是也……', formula:'痛点场景→放大焦虑→产品解药', traits:['高频痛点','代入感强','转化直接'], platforms:['douyin','kuaishou','shipinhao'], aiFriendly:0.95, intensity:0.90 },
      B: { id:'B', name:'反常识', tag:'其实你一直做错了', formula:'常识锚定→打破认知→正确做法', traits:['反差大','完播高','记忆深'], platforms:['douyin','xiaohongshu'], aiFriendly:0.85, intensity:0.85 },
      C: { id:'C', name:'身份认同', tag:'懂的人都在用', formula:'圈层暗号→身份标签→产品入圈', traits:['圈层感','社交货币','复购高'], platforms:['xiaohongshu','douyin'], aiFriendly:0.80, intensity:0.75 },
      D: { id:'D', name:'省钱算账', tag:'算笔账你就懂了', formula:'金额拆解→对比算账→白菜价', traits:['理性驱动','决策快','ROI清晰'], platforms:['kuaishou','shipinhao','baidu'], aiFriendly:0.90, intensity:0.80 },
      E: { id:'E', name:'信任背书', tag:'我替你花2000块踩过坑', formula:'代价锚定→证据展示→真诚推荐', traits:['信任度高','退货低','口碑好'], platforms:['kuaishou','shipinhao','baidu'], aiFriendly:0.88, intensity:0.82 },
      F: { id:'F', name:'情绪共鸣', tag:'看完莫名很爽', formula:'情绪钩子→故事沉浸→产品治愈', traits:['完播高','分享多','品牌感'], platforms:['xiaohongshu','douyin'], aiFriendly:0.82, intensity:0.78 },
    };
  }

  // ═══════════════════════════════════════════
  //  10 大钩子族（80 条模板）
  // ═══════════════════════════════════════════
  _buildHookLibrary() {
    return {
      pain_anxiety: [
        { id:'H1-1', tmpl:'你是不是也{c Pain}？每次{scene}都{feeling}', ai:0.95, power:0.90 },
        { id:'H1-2', tmpl:'{c Pain}的痛，只有{c Role}才懂', ai:0.90, power:0.88 },
        { id:'H1-3', tmpl:'别再让{c Pain}毁掉你的{scene}了', ai:0.92, power:0.85 },
        { id:'H1-4', tmpl:'如果你{c Pain}超过3年，这条视频必看', ai:0.88, power:0.87 },
        { id:'H1-5', tmpl:'以为{wrong}就好了？其实{c Pain}根本没解决', ai:0.85, power:0.89 },
        { id:'H1-6', tmpl:'每天花{time}在{c Pain}上，你不累吗？', ai:0.93, power:0.86 },
        { id:'H1-7', tmpl:'{c Pain}的人，后来都怎么样了？', ai:0.87, power:0.84 },
        { id:'H1-8', tmpl:'你那么努力，为什么{c Pain}还在？', ai:0.90, power:0.88 },
      ],
      curiosity: [
        { id:'H2-1', tmpl:'我偷偷用了{product} 30天，结果{surprise}', ai:0.92, power:0.91 },
        { id:'H2-2', tmpl:'99%的人不知道，{product}还能这么用', ai:0.94, power:0.89 },
        { id:'H2-3', tmpl:'拆开了才知道，为什么它敢卖这个价格', ai:0.88, power:0.87 },
        { id:'H2-4', tmpl:'我把{product}寄给了专家，他的反应让我……', ai:0.85, power:0.90 },
        { id:'H2-5', tmpl:'这个秘密，品牌方从来不告诉你', ai:0.90, power:0.88 },
        { id:'H2-6', tmpl:'打开之前，我以为又被骗了', ai:0.87, power:0.86 },
        { id:'H2-7', tmpl:'用了3年，今天终于决定告诉你们真相', ai:0.89, power:0.88 },
        { id:'H2-8', tmpl:'不敢发朋友圈，但憋不住了', ai:0.82, power:0.85 },
      ],
      benefit_first: [
        { id:'H3-1', tmpl:'省{c Amount}块！学会这招再也不用{action}', ai:0.95, power:0.92 },
        { id:'H3-2', tmpl:'花{c Price}，享受{c Value}的效果', ai:0.93, power:0.90 },
        { id:'H3-3', tmpl:'今天下单立省{c Amount}，错过再等一年', ai:0.90, power:0.88 },
        { id:'H3-4', tmpl:'一瓶=10次{c Service}，算下来每次才{c Price}', ai:0.88, power:0.87 },
        { id:'H3-5', tmpl:'用一次就回本，{c Benefit}太值了', ai:0.91, power:0.85 },
        { id:'H3-6', tmpl:'同事问我为什么最近{benefit}，秘密就是这个', ai:0.87, power:0.86 },
        { id:'H3-7', tmpl:'同样{c Category}，价格差10倍，效果差0', ai:0.89, power:0.88 },
        { id:'H3-8', tmpl:'这个活动不常有，看到就是赚到', ai:0.85, power:0.84 },
      ],
      counter_intuition: [
        { id:'H4-1', tmpl:'别再{common}了！其实{truth}', ai:0.94, power:0.93 },
        { id:'H4-2', tmpl:'你一直{action}的方法，可能是错的', ai:0.91, power:0.89 },
        { id:'H4-3', tmpl:'专家自己都不{common}，而是{c Secret}', ai:0.88, power:0.87 },
        { id:'H4-4', tmpl:'花了{c Amount}学费才明白：{insight}', ai:0.87, power:0.88 },
        { id:'H4-5', tmpl:'{myth}是骗你的，真相是{truth}', ai:0.92, power:0.91 },
        { id:'H4-6', tmpl:'商家不会告诉你的{c Category}真相', ai:0.90, power:0.86 },
        { id:'H4-7', tmpl:'为什么越{common}越{result}？终于搞明白了', ai:0.86, power:0.85 },
        { id:'H4-8', tmpl:'说{c Product}没用的，大概率没搞懂这个', ai:0.85, power:0.84 },
      ],
      pattern_interrupt: [
        { id:'H5-1', tmpl:'停！先别滑走，给你看个好东西', ai:0.88, power:0.92 },
        { id:'H5-2', tmpl:'这不是广告，这是我用了{time}后的真心话', ai:0.85, power:0.89 },
        { id:'H5-3', tmpl:'⚠️ 警告：看完你可能忍不住下单', ai:0.90, power:0.90 },
        { id:'H5-4', tmpl:'（把音量调大）接下来10秒很重要', ai:0.87, power:0.88 },
        { id:'H5-5', tmpl:'我一般不会随便推荐，但这个真的……', ai:0.84, power:0.87 },
        { id:'H5-6', tmpl:'刷到这条说明你需要它，大数据不会骗你', ai:0.89, power:0.86 },
        { id:'H5-7', tmpl:'先别急着买，看完这个对比再说', ai:0.86, power:0.85 },
        { id:'H5-8', tmpl:'最后再说一次，错过真的没了', ai:0.83, power:0.88 },
      ],
      identity: [
        { id:'H6-1', tmpl:'真正的{c Role}，早就不{old_way}了', ai:0.88, power:0.87 },
        { id:'H6-2', tmpl:'懂{c Domain}的人，都在偷偷用{product}', ai:0.90, power:0.89 },
        { id:'H6-3', tmpl:'{c Role}的圈子里，这已经是公开的秘密', ai:0.87, power:0.86 },
        { id:'H6-4', tmpl:'不是所有人都适合{product}，但{c Role}必须试试', ai:0.85, power:0.88 },
        { id:'H6-5', tmpl:'入门级{c Category}不够用？是时候升级了', ai:0.89, power:0.85 },
        { id:'H6-6', tmpl:'{c Role}和{c Role}之间，就差这一个习惯', ai:0.86, power:0.84 },
        { id:'H6-7', tmpl:'用过{product}的，评论区集合', ai:0.82, power:0.86 },
        { id:'H6-8', tmpl:'圈子里的老玩家告诉我：新手别瞎买', ai:0.84, power:0.85 },
      ],
      number_list: [
        { id:'H7-1', tmpl:'{c N}个{c Category}小技巧，第{c K}个最值钱', ai:0.93, power:0.90 },
        { id:'H7-2', tmpl:'每天省{c Amount}块，{c N}天省出{c Big}', ai:0.91, power:0.88 },
        { id:'H7-3', tmpl:'{c N}种{c Category}测评，只有{c K}款值得买', ai:0.90, power:0.89 },
        { id:'H7-4', tmpl:'用了{c N}瓶之后，我总结出这{c K}点', ai:0.88, power:0.87 },
        { id:'H7-5', tmpl:'{c N}块钱和{c Big}块钱的区别，就在这一点', ai:0.86, power:0.86 },
        { id:'H7-6', tmpl:'{c N}年{c Role}告诉你：{c K}步就能搞定', ai:0.89, power:0.88 },
        { id:'H7-7', tmpl:'看完这{c N}条，{c Category}再也坑不了你', ai:0.87, power:0.85 },
        { id:'H7-8', tmpl:'销量{c Big}件，评分{c Score}，它凭什么？', ai:0.85, power:0.87 },
      ],
      contrast: [
        { id:'H8-1', tmpl:'Before：{before} → After：{after}，只用了{product}', ai:0.92, power:0.91 },
        { id:'H8-2', tmpl:'同样是{c Amount}块，别人买{c Cheap}，我买{c Good}', ai:0.89, power:0.88 },
        { id:'H8-3', tmpl:'别人{c Common}，我{c Better}，差距就在{product}', ai:0.87, power:0.87 },
        { id:'H8-4', tmpl:'左边{left} vs 右边{right}，你选哪个？', ai:0.85, power:0.89 },
        { id:'H8-5', tmpl:'不是买不起{c Expensive}，是{product}更有性价比', ai:0.90, power:0.86 },
        { id:'H8-6', tmpl:'{c Old}怎么也没想到，会被{product}取代', ai:0.86, power:0.85 },
        { id:'H8-7', tmpl:'用过{product}之后，再看{c Old}真的回不去了', ai:0.88, power:0.88 },
        { id:'H8-8', tmpl:'{price1} vs {price2}，测完我沉默了', ai:0.84, power:0.87 },
      ],
      emotion: [
        { id:'H9-1', tmpl:'那一刻我真的{c Emotion}了，谢谢你{product}', ai:0.88, power:0.90 },
        { id:'H9-2', tmpl:'有些{c Category}，用过才懂什么叫{c Feeling}', ai:0.90, power:0.88 },
        { id:'H9-3', tmpl:'送给所有{c Role}：你值得{c Better}', ai:0.87, power:0.86 },
        { id:'H9-4', tmpl:'成年人的{c Scene}，从拥有{product}开始', ai:0.86, power:0.87 },
        { id:'H9-5', tmpl:'终于不用在{c Pain}面前{c Helpless}了', ai:0.89, power:0.89 },
        { id:'H9-6', tmpl:'用完第一个{c Time}，我就知道：买对了', ai:0.85, power:0.86 },
        { id:'H9-7', tmpl:'它不是{c Category}，是你{c Scene}的底气', ai:0.88, power:0.88 },
        { id:'H9-8', tmpl:'一个{c Role}的{c Time}：原来可以这么简单', ai:0.84, power:0.85 },
      ],
      interactive: [
        { id:'H10-1', tmpl:'你觉得{c Product}值{c Price}吗？看完再回答', ai:0.91, power:0.88 },
        { id:'H10-2', tmpl:'评论区告诉我，你{c Pain}多久了？', ai:0.89, power:0.87 },
        { id:'H10-3', tmpl:'{c A}和{c B}，你会选哪个？', ai:0.88, power:0.89 },
        { id:'H10-4', tmpl:'有多少人和我一样{c Experience}？', ai:0.86, power:0.86 },
        { id:'H10-5', tmpl:'测一下你的{c Category}智商，能答对{c N}题算你赢', ai:0.87, power:0.85 },
        { id:'H10-6', tmpl:'先别划走！告诉我你{c Pain}几年了', ai:0.90, power:0.88 },
        { id:'H10-7', tmpl:'最后一个问题：{question}？', ai:0.84, power:0.87 },
        { id:'H10-8', tmpl:'我猜你{c Time}内就会回来感谢我', ai:0.86, power:0.86 },
      ],
    };
  }

  // ═══════════════════════════════════════════
  //  S1: 产品 & 人群洞察
  // ═══════════════════════════════════════════
  analyzeProduct(product, platform = 'douyin') {
    const { name, category, price, sellingPoints = [], painPoints = [], competitor = '' } = product;
    const priceBand = price < 30 ? '超低客单' : price < 100 ? '低客单' : price < 300 ? '中客单' : price < 1000 ? '中高客单' : '高客单';
    const audienceMap = { '护肤品':{gender:'女性为主',age:'18-40',tag:'爱美/焦虑/品质感'}, '食品':{gender:'不限',age:'20-50',tag:'吃货/健康/性价比'}, '家居':{gender:'女性为主',age:'25-45',tag:'居家/品质/实用'}, '数码':{gender:'男性为主',age:'18-35',tag:'科技/极客/参数控'}, '服装':{gender:'不限',age:'18-35',tag:'潮流/个性/穿搭'}, '母婴':{gender:'女性为主',age:'25-40',tag:'安全/品质/信赖'}, '健康':{gender:'不限',age:'30-55',tag:'养生/焦虑/功效'} };
    const audience = audienceMap[category] || { gender:'不限', age:'18-45', tag:'大众/实用/性价比' };
    const satMap = { '护肤品':'红海','食品':'红海','家居':'橙海','数码':'橙海','服装':'红海','母婴':'橙海' };
    const goalMap = { '超低客单':'冲动转化','低客单':'快速转化','中客单':'信任转化','中高客单':'深度种草','高客单':'品牌信任' };
    return { productName:name, category, price, priceBand, sellingPoints:sellingPoints.slice(0,5), painPoints:painPoints.slice(0,5), audience, platform, conversionGoal: goalMap[priceBand]||'转化', cognitionStatus:{ market: satMap[category]||'蓝海', userAwareness: platform==='baidu'?'主动搜索':'被动种草', trustBarrier: satMap[category]==='红海'?'高':'中' }, competitorPosition: competitor, timestamp: Date.now() };
  }

  // ═══════════════════════════════════════════
  //  S2: 角度空间扫描
  // ═══════════════════════════════════════════
  scanAngles(insight, count = 6) {
    const candidates = [];
    Object.values(this.angleFamilies).forEach(f => {
      candidates.push(this._genCandidate(f, insight, 0), this._genCandidate(f, insight, 1));
    });
    const scored = candidates.map(c => ({ ...c, _s: this._quickScore(c, insight) })).sort((a, b) => b._s - a._s);
    const selected = [], fIds = new Set();
    for (const c of scored) { if (!fIds.has(c.familyId)) { selected.push(c); fIds.add(c.familyId); } if (selected.length >= Math.min(count, 6)) break; }
    for (const c of scored) { if (selected.length >= count) break; if (!selected.includes(c)) selected.push(c); }
    return selected.map(({ _s, ...r }) => r);
  }

  _genCandidate(family, ins, v) {
    const pain = ins.painPoints[v % ins.painPoints.length] || '困扰';
    const titles = { A:[`${pain}的人，终于有救了`,`你是不是也被${pain}折磨？`], B:[`关于${ins.category}，你一直搞错了`,`${pain}？其实是你方法不对`], C:[`懂${ins.category}的人，都在偷偷用它`,`${ins.category}内行人的选择`], D:[`${ins.price}块？算完这笔账我笑了`,`一年省几千，${ins.category}这样买`], E:[`我替你们踩过${ins.category}的坑了`,`花了冤枉钱才明白，${ins.productName}真香`], F:[`一个${ins.category}让我的生活变了`,`用完${ins.productName}，我终于不焦虑了`] };
    const hooks = this._pickHooks(family, ins);
    return { familyId: family.id, familyName: family.name, angleTag: family.tag, formula: family.formula, variant: v, title: (titles[family.id]||['好物推荐'])[v%2], painPoint: pain, sellingPoint: ins.sellingPoints[v % ins.sellingPoints.length] || '好用', recommendedHooks: hooks.map(h => h.id), hookTemplates: hooks.map(h => h.tmpl), traits: family.traits, aiFriendly: family.aiFriendly, intensity: family.intensity };
  }

  _pickHooks(family, ins) {
    const map = { A:['pain_anxiety','pattern_interrupt'], B:['counter_intuition','curiosity'], C:['identity','emotion'], D:['number_list','benefit_first'], E:['curiosity','contrast'], F:['emotion','interactive'] };
    const hooks = [];
    (map[family.id] || ['curiosity']).forEach(k => { const lib = this.hookLibrary[k]; if (lib) hooks.push(...lib.slice(0, 3)); });
    return hooks.sort((a, b) => (b.ai * b.power) - (a.ai * a.power)).slice(0, 2);
  }

  _quickScore(c, ins) { const f = this.angleFamilies[c.familyId]; return f ? f.aiFriendly * 25 + f.intensity * 20 + (f.platforms.includes(ins.platform) ? 15 : 0) : 0; }

  // ═══════════════════════════════════════════
  //  S3: 钩子公式套用
  // ═══════════════════════════════════════════
  matchHooks(angles) {
    return angles.map(a => {
      const hooks = this._getBestHooks(a);
      return { ...a, matchedHooks: hooks.map(h => ({ hookId: h.id, template: h.tmpl, aiFriendly: h.ai >= 0.88 ? '🟢' : h.ai >= 0.80 ? '🟡' : '🔴', hookPower: h.power, filledExample: this._fill(h.tmpl, a) })) };
    });
  }

  _getBestHooks(angle) {
    const map = { A:['pain_anxiety','pattern_interrupt'], B:['counter_intuition','curiosity'], C:['identity','emotion'], D:['benefit_first','number_list'], E:['curiosity','contrast'], F:['emotion','interactive'] };
    const all = [];
    (map[angle.familyId] || ['curiosity']).forEach(k => { const lib = this.hookLibrary[k]; if (lib) all.push(...lib); });
    return all.sort((a, b) => (b.ai * b.power) - (a.ai * a.power)).slice(0, 3);
  }

  _fill(tmpl, angle) {
    const vars = { 'c Pain': angle.painPoint || '困扰', 'c Product': angle.productName || '这个', 'product': angle.productName || '它', 'scene': '生活中', 'feeling': '很难受', 'c Role': '过来人', 'c Category': angle.category || '这类产品', 'wrong': '随便用', 'time': '很多', 'surprise': '震惊了', 'c Amount': '几百', 'c Price': '几十块', 'c Value': '上千', 'c Service': '专业服务', 'benefit': '变好', 'c N': '5', 'c K': '3', 'c Big': '几千', 'c Score': '4.9', 'common': '老办法', 'truth': '新方法更好', 'action': '搞错', 'c Secret': '有秘诀', 'insight': '很简单', 'myth': '流行说法', 'before': '之前很差', 'after': '现在很好', 'c Cheap': '便宜的', 'c Good': '好货', 'c Better': '更好', 'c Expensive': '贵的', 'c Old': '老办法', 'c Emotion': '感动', 'c Feeling': '舒服', 'c Scene': '每一天', 'c Helpless': '无助', 'c Time': '一周', 'c A': 'A', 'c B': 'B', 'c Experience': '经历过', 'question': '你知道答案吗', 'old_way': '用老方法', 'c Domain': angle.category || '这个', 'result': '更糟糕', 'left': '旧方案', 'right': '新方案', 'price1': '便宜款', 'price2': '贵价款' };
    let s = tmpl;
    for (const [k, v] of Object.entries(vars)) s = s.replace(new RegExp(`\\{${k}\\}`, 'g'), v);
    return s;
  }

  // ═══════════════════════════════════════════
  //  S4: 差异化筛选（重合度 ≤ 15%）
  // ═══════════════════════════════════════════
  filterByDiversity(candidates) {
    const result = [];
    for (const c of candidates) {
      let ok = true;
      for (const r of result) { if (this._sim(c, r) > DIVERSITY_THRESHOLD) { ok = false; break; } }
      if (ok) result.push(c);
      else { const alt = candidates.find(x => x.familyId === c.familyId && x.variant !== c.variant && !result.includes(x) && result.every(s => this._sim(x, s) <= DIVERSITY_THRESHOLD)); if (alt && !result.includes(alt)) result.push(alt); }
    }
    return result;
  }

  _sim(a, b) {
    const ss = a.familyId === b.familyId ? 0.8 : 0.1;
    const shared = (a.recommendedHooks || []).filter(h => (b.recommendedHooks || []).includes(h)).length;
    const total = new Set([...(a.recommendedHooks || []), ...(b.recommendedHooks || [])]).size;
    const fs = total > 0 ? shared / total : 0;
    const wa = new Set((a.title || '').split('')), wb = new Set((b.title || '').split(''));
    const sharedW = [...wa].filter(w => wb.has(w)).length, totalW = new Set([...wa, ...wb]).size;
    const sty = totalW > 0 ? sharedW / totalW : 0;
    return ss * SIMILARITY_WEIGHTS.structure + fs * SIMILARITY_WEIGHTS.framework + sty * SIMILARITY_WEIGHTS.style;
  }

  // ═══════════════════════════════════════════
  //  S5: WinScore 评分
  // ═══════════════════════════════════════════
  scoreWinScore(c) {
    const f = this.angleFamilies[c.familyId];
    const s = (this.noveltyScore(c) * this.weights.novelty + this.painScore(c) * this.weights.painIntensity + this.hookStrength(c) * this.weights.hookStrength + this.platformFit(c) * this.weights.platformFit + this.trustDensity(c) * this.weights.trustDensity + this.visualScore(c) * this.weights.visualScore) * 100;
    return Math.min(100, Math.max(0, Math.round(s)));
  }
  noveltyScore(c) { const f = this.angleFamilies[c.familyId]; return f ? 1 - f.intensity * 0.3 : 0.5; }
  painScore(c)   { const f = this.angleFamilies[c.familyId]; return f ? f.intensity : 0.5; }
  hookStrength(c){ const h = c.matchedHooks || []; return h.length ? h.reduce((s, x) => s + (x.hookPower || 0.5), 0) / h.length : 0.5; }
  platformFit(c) { const f = this.angleFamilies[c.familyId]; return f && f.platforms.includes(c.platform || 'douyin') ? 0.9 : 0.5; }
  trustDensity(c){ return { E:0.95, D:0.85, A:0.80 }[c.familyId] || 0.60; }
  visualScore(c) { return { B:0.95, F:0.90, D:0.85 }[c.familyId] || 0.70; }

  // ═══════════════════════════════════════════
  //  S6: 输出完整角度卡
  // ═══════════════════════════════════════════
  generateAngleCard(product, platform = 'douyin', count = 6) {
    const ins = this.analyzeProduct(product, platform);
    let angles = this.scanAngles(ins, count * 2);
    angles = this.matchHooks(angles);
    angles = this.filterByDiversity(angles);
    angles = angles.map(a => ({ ...a, platform, winScore: this.scoreWinScore(a), scoreBreakdown: { novelty: Math.round(this.noveltyScore(a) * this.weights.novelty * 100), pain: Math.round(this.painScore(a) * this.weights.painIntensity * 100), hook: Math.round(this.hookStrength(a) * this.weights.hookStrength * 100), platformFit: Math.round(this.platformFit(a) * this.weights.platformFit * 100), trust: Math.round(this.trustDensity(a) * this.weights.trustDensity * 100), visual: Math.round(this.visualScore(a) * this.weights.visualScore * 100) } }));
    angles.sort((a, b) => b.winScore - a.winScore);
    angles = angles.slice(0, count);
    const angleItems = angles.map((a, i) => ({
      rank: i + 1,
      angleId: `${a.familyId}-${a.variant}`,
      family: a.familyName,
      tag: a.angleTag,
      title: a.title,
      formula: a.formula,
      winScore: a.winScore,
      WinScore: a.winScore,
      scoreBreakdown: a.scoreBreakdown,
      traits: a.traits,
      aiFriendly: a.aiFriendly,
      intensity: a.intensity,
      hooks: a.matchedHooks || [],
      complianceCheck: this._compliance(a),
    }));
    const report = {
      meta: { engine: 'HookEngine v1.0.0', product: ins.productName, platform, generatedAt: new Date().toISOString(), totalAngles: angles.length },
      insight: ins,
      angles: angleItems,
      diversity: { method: 'structure*0.4 + framework*0.35 + style*0.25', threshold: DIVERSITY_THRESHOLD, maxPairSim: this._maxPairSim(angles), pass: this._maxPairSim(angles) <= DIVERSITY_THRESHOLD },
    };
    angleItems.meta = report.meta;
    angleItems.insight = report.insight;
    angleItems.angles = angleItems;
    angleItems.diversity = report.diversity;
    angleItems.report = report;
    return angleItems;
  }

  _maxPairSim(angles) { let m = 0; for (let i = 0; i < angles.length; i++) for (let j = i + 1; j < angles.length; j++) { const s = this._sim(angles[i], angles[j]); if (s > m) m = s; } return Math.round(m * 100) / 100; }

  _compliance(a) {
    const risk = ['最','第一','国家级','绝对','100%','保证','根治','特效'].filter(w => (a.title || '').includes(w));
    return { status: risk.length ? '⚠️ 需审核' : '✅ 通过', riskWords: risk, advice: risk.length ? `建议修改: 避免使用 ${risk.join('、')}` : '合规' };
  }

  // ═══════════════════════════════════════════
  //  工具方法
  // ═══════════════════════════════════════════
  fillHookTemplate(hookId, variables) {
    for (const lib of Object.values(this.hookLibrary)) {
      const h = lib.find(x => x.id === hookId);
      if (h) { let t = h.tmpl; for (const [k, v] of Object.entries(variables)) t = t.replace(new RegExp(`\\{${k}\\}`, 'g'), v); return { hookId, template: h.tmpl, filled: t, ai: h.ai, power: h.power }; }
    }
    return null;
  }

  getStats() {
    return { angleFamilies: Object.keys(this.angleFamilies).length, hookFamilies: Object.keys(this.hookLibrary).length, totalHooks: Object.values(this.hookLibrary).reduce((s, a) => s + a.length, 0), weights: this.weights, diversityThreshold: DIVERSITY_THRESHOLD };
  }
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = HookEngine;
  module.exports.HookEngine = HookEngine;
  module.exports.WIN_SCORE_WEIGHTS = WIN_SCORE_WEIGHTS;
  module.exports.DIVERSITY_THRESHOLD = DIVERSITY_THRESHOLD;
  module.exports.SIMILARITY_WEIGHTS = SIMILARITY_WEIGHTS;
}
if (typeof window !== 'undefined') {
  window.HookEngine = HookEngine; window.WIN_SCORE_WEIGHTS = WIN_SCORE_WEIGHTS;
}
