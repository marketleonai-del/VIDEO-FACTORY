/**
 * ============================================================
 * PromptTemplateEngine — 提示词模板引擎
 * VIDEO-FACTORY 创意系统核心大脑
 * ============================================================
 * 职责:
 *   - 所有 AI 提示词集中管理，支持变量替换与动态进化
 *   - 覆盖: 产品分析、带货脚本、故事板分镜、角度卡、
 *          画面提示、矩阵差异化、质检合规
 *   - 提供 render / renderBatch / validate / evolve 全生命周期
 *
 * 提示词来源:
 *   1. 产品分析      → JSON 结构化卖点提取
 *   2. 带货脚本      → 钩子→卖点→效果→促单 四段式
 *   3. 故事板分镜    → 9 字段完整版（镜号/景别/运镜/时长/画面/台词/素材/锚定/转场）
 *   4. 角度卡        → 6 大角度族 × WinScore 评分
 *   5. 画面提示      → 文生视频 prompt + 续帧一致性约束
 *   6. 矩阵差异化    → 母版复用 + 多维度变体
 *   7. 质检合规      → 120 分制自检清单
 * ============================================================
 */

class PromptTemplateEngine {
  /**
   * @param {Object} skillParams - 技能级全局参数
   *   @property {string} promptSuffix   - 可进化画面后缀
   *   @property {string} brandTone     - 品牌调性 (aggressive/soft/expert)
   *   @property {string} defaultModel  - 默认调用模型
   */
  constructor(skillParams = {}) {
    this.params = skillParams;
    this.templates = this.loadTemplates();
    // 渲染统计，用于追踪模板使用频率与进化决策
    this.renderStats = {};
  }

  // ─────────────────────────────────────────────
  // 1. 模板加载 — 所有提示词集中定义
  // ─────────────────────────────────────────────
  loadTemplates() {
    const suffix = this.params.promptSuffix || '';
    const tone = this.params.brandTone || 'aggressive';

    // 品牌调性映射表，用于动态调整 system 语气
    const toneMap = {
      aggressive: '你是极具攻击性的带货操盘手，懂流量、懂人性、懂平台算法。',
      soft:       '你是温柔共情型带货操盘手，像闺蜜推荐好物一样自然。',
      expert:     '你是领域专家型带货操盘手，用专业数据建立权威信任。'
    };

    return {
      // ── 1.1 产品分析 ──────────────────────
      productAnalysis: {
        system: `${toneMap[tone]}只输出纯 JSON，不要任何解释、不要 markdown 代码块。`,
        user: (
          '深度分析以下产品，输出严格 JSON：\n' +
          '{"品类":"","核心痛点":["",""],"核心卖点":["","",""],"目标人群":"","价格带":"","认知误区":"","竞品通常打的角度":"","差异化机会":""}\n' +
          '产品描述：{{product}}\n' +
          '要求：痛点必须含具体场景；卖点必须可视觉化；认知误区需有数据支撑。'
        )
      },

      // ── 1.2 带货脚本（四段式口播）─────────
      salesScript: {
        system: (
          '你是短视频带货编剧。输出一条 {{duration}}s 竖屏口播脚本，结构：\n' +
          '【前3秒钩子】→【核心卖点可视化】→【使用效果/前后对比】→【促单CTA】\n' +
          '要求：口语化、有画面感、无绝对化用语（"最""第一""100%"等）、' +
          '无未证功效声明、无医疗暗示。只输出脚本正文，不输出结构标签。'
        ),
        user: '产品：{{product}}；核心卖点：{{sellingPoints}}；目标人群：{{audiences}}；平台：{{platform}}；调性：{{tone}}。'
      },

      // ── 1.3 故事板分镜（9字段完整版）──────
      storyboard: {
        system: (
          '你是分镜导演。将脚本拆解为 {{shotCount}} 个分镜，输出 JSON 数组。\n' +
          '每个分镜 9 字段：{"镜号":1,"景别":"近景/中景/特写/全景","运镜":"推/拉/摇/跟/固定/升/降","时长s":0,"画面":"可直接喂给文生视频的画面描述（含主体、场景、光线、色彩）","台词":"该镜口播台词","素材":"真素材/AI生成/混剪","锚定":"@image1产品等锚点","转场":"硬切/叠化/闪白/匹配","音效":"环境音/BGM/无"}\n' +
          '分段规则：单镜≤15s；总时长 {{totalDuration}}s → {{shotCount}} 镜均摊；首镜必须含钩子视觉。只输出 JSON 数组。'
        ),
        user: '脚本：{{script}}；产品锚定：@image1={{product}}；平台画面风格：{{platformStyle}}。'
      },

      // ── 1.4 角度卡（6大角度族 × WinScore）──
      angleCard: {
        system: (
          '你是广告策略师。为产品扫描 6 大角度族产出 {{count}} 个"真正不同"的角度。\n' +
          '角度族定义：\n' +
          'A痛点放大 — 痛苦场景具象化；B反常识 — 打破认知冲突；\n' +
          'C身份认同 — 你是谁/你值得；D省钱算账 — 数字说服；\n' +
          'E信任背书 — 数据/权威/真人；F情绪共鸣 — 故事/情怀/共鸣。\n' +
          '每个角度输出 JSON：\n' +
          '{"角度族":"B反常识","角度名":"","钩子前3秒原话":"","为什么不同":"与竞品差异说明","目标人群":"","WinScore":{"新颖0-25":0,"痛点0-20":0,"钩子力0-20":0,"平台适配0-15":0,"信任度0-10":0,"可视化0-10":0,"合计":0},"合规风险":"低/中/高","推荐平台":""}\n' +
          '要求：① 跨族覆盖（至少3个族）；② 任意两角度重合度≤15%；③ 按 WinScore 合计降序。只输出 JSON 数组。'
        ),
        user: '产品：{{product}}；竞品通常打的角度：{{competitorAngles}}；目标平台：{{platform}}；人群画像：{{audiences}}。'
      },

      // ── 1.5 画面提示 + 续帧一致性 ─────────
      videoPrompt: {
        frame: (
          '{{scene}}，竖屏 {{aspect}}，电影级运镜({{cameraMovement}})，' +
          '{{lighting}}，真实质感，无水印，高质量'
        ),
        suffix: suffix,                          // 可进化后缀（如 "--ar 9:16 --v 6" 等）
        continuity: (
          '【续帧约束】与上一镜头保持主体一致性（人物/产品/场景不变），' +
          '仅改变景别或运镜，光线和色调连贯，角色姿态自然衔接。'
        ),
        negative: (
          '模糊，低质量，变形，多手指，文字错误，品牌水印，' +
          '画面抖动，颜色断层，恐怖谷人脸'
        )
      },

      // ── 1.6 矩阵差异化 ────────────────────
      matrixDiff: {
        system: (
          '你是矩阵运营专家。将 1 个赢家概念放大为 {{count}} 条账号安全变体。\n' +
          '每条变体 JSON：\n' +
          '{"变体号":1,"账号人设":"","平台":"","钩子变体":"（必须从钩子公式库换族）","差异维度":"改了什么：人设/开场镜/BGM/节奏/字幕/场景","跨账号重合度":"≤20%","母版复用比例":"≥80%","发布时段":"","CTA":"","预估表现":{"CTR":"","完播率":""}}\n' +
          '核心策略：复用赢家母版的"产品卖点身体"，只换"钩子头"；\n' +
          '跨账号重合度≤20%；开场镜/BGM/字幕/人设至少改 2 项（防平台限流）；\n' +
          '覆盖不同时段与人群切片。只输出 JSON 数组。'
        ),
        user: '赢家概念：{{winner}}；分发平台：{{platform}}；账号数：{{count}}；钩子库：{{hookLibrary}}。'
      },

      // ── 1.7 质检 / 合规自检 ───────────────
      qualityCheck: {
        system: (
          '【VIDEO-FACTORY 质检引擎 · 120 分制】\n' +
          '评分＜100 自动触发重修（最多 3 轮）。逐项检查：\n\n' +
          '① 内容力(30)：角度真正不同 / 钩子前3秒成立 / 卖点可视化 / 有记忆点\n' +
          '② 真实感(25)：信任点用真素材 / 无恐怖谷 AI 感 / 非完美细节保留 / 有真人痕迹\n' +
          '③ 差异化(20)：段内角度重合≤15% / 跨账号重合≤20% / 6 维度覆盖\n' +
          '④ 合规性(20)：AI 生成标识 / 分区清单 / 零禁用词（最/第一/100%/根治等）/ 无未证功效\n' +
          '⑤ 闭环度(15)：每条带 WinScore / 预留 actual{CTR,完播,GMV} 回填字段 / 可追踪\n' +
          '⑥ 技术范(10)：模型无关提示词 / @image 锚定正确 / 续帧一致性约束 / 9:16 竖屏\n\n' +
          '输出 JSON：{"总分":0,"各维得分":{},"问题清单":[],"重修建议":"","是否通过":true/false}'
        ),
        user: '待检内容：{{content}}；平台：{{platform}}；行业：{{category}}。'
      },

      // ── 1.8 钩子公式库调用 ────────────────
      hookLibrary: {
        system: (
          '你是钩子心理学家。从 10 族 80+ 条钩子公式库中，为产品匹配 {{count}} 条最适合的钩子。\n' +
          '钩子族：1恐惧 2好奇 3反常识 4利益 5身份 6对比 7数字 8故事 9挑衅 10社交\n' +
          '每条输出：{"族":"","钩子公式":"","前3秒口播":"","适用平台":"","预期CTR":""}\n' +
          '按预期 CTR 降序。只输出 JSON 数组。'
        ),
        user: '产品：{{product}}；人群：{{audiences}}；平台：{{platform}}；禁忌：{{bannedHooks}}。'
      }
    };
  }

  // ─────────────────────────────────────────────
  // 2. 变量替换 — 核心渲染引擎
  // ─────────────────────────────────────────────
  /**
   * 渲染单个模板
   * @param {string} templateName - 模板名（如 "salesScript"）
   * @param {Object} variables    - 变量键值对（如 {product: "xx面膜"}）
   * @returns {Object} 渲染后的模板副本
   */
  render(templateName, variables = {}) {
    const template = this.templates[templateName];
    if (!template) {
      throw new Error(`[PromptEngine] 模板 "${templateName}" 不存在。可用: ${this.listTemplates().join(', ')}`);
    }

    // 深拷贝，避免污染原始模板
    const result = JSON.parse(JSON.stringify(template));

    // 执行变量替换（支持 {{key}} 语法，全局替换）
    for (const [key, value] of Object.entries(variables)) {
      const placeholder = `{{${key}}}`;
      const safeValue = String(value ?? '');
      ['system', 'user', 'frame', 'suffix', 'continuity', 'negative'].forEach(field => {
        if (result[field]) result[field] = result[field].split(placeholder).join(safeValue);
      });
    }

    // 清理未替换的占位符（防止 {{xxx}} 泄漏到 AI）
    const cleanPlaceholder = /\{\{\w+\}\}/g;
    ['system', 'user', 'frame', 'suffix', 'continuity', 'negative'].forEach(field => {
      if (result[field]) result[field] = result[field].replace(cleanPlaceholder, '');
    });

    // 记录渲染统计
    this.renderStats[templateName] = (this.renderStats[templateName] || 0) + 1;

    return result;
  }

  // ─────────────────────────────────────────────
  // 3. 批量渲染 — 矩阵内容生产
  // ─────────────────────────────────────────────
  /**
   * 批量渲染（用于矩阵差异化、A/B 测试等多变量场景）
   * @param {string}   templateName    - 模板名
   * @param {Object[]} variablesArray  - 变量数组，每项对应一次 render
   * @returns {Object[]} 渲染结果数组
   */
  renderBatch(templateName, variablesArray) {
    if (!Array.isArray(variablesArray)) {
      throw new Error('[PromptEngine] renderBatch 需要数组类型的 variablesArray');
    }
    return variablesArray.map((vars, idx) => {
      try {
        return this.render(templateName, vars);
      } catch (err) {
        return { _index: idx, _error: err.message };
      }
    });
  }

  // ─────────────────────────────────────────────
  // 4. 模板元信息 — 发现与进化
  // ─────────────────────────────────────────────
  /** 列出所有可用模板名 */
  listTemplates() {
    return Object.keys(this.templates);
  }

  /** 获取单模板原始定义（用于调试） */
  getRaw(templateName) {
    return this.templates[templateName] || null;
  }

  /** 获取渲染统计（用于进化决策：高频模板优先优化） */
  getStats() {
    return { ...this.renderStats, _total: Object.values(this.renderStats).reduce((a, b) => a + b, 0) };
  }

  // ─────────────────────────────────────────────
  // 5. 模板进化 — 运行时热更新
  // ─────────────────────────────────────────────
  /**
   * 热更新指定字段（支持 prompt 进化，不重启服务）
   * @param {string} templateName - 目标模板
   * @param {string} field        - 字段名（system/user/frame/suffix...）
   * @param {string} newContent   - 新内容
   */
  evolve(templateName, field, newContent) {
    if (!this.templates[templateName]) {
      throw new Error(`[PromptEngine] 无法进化不存在的模板: ${templateName}`);
    }
    if (!this.templates[templateName][field]) {
      throw new Error(`[PromptEngine] 模板 ${templateName} 无字段 "${field}"`);
    }
    this.templates[templateName][field] = newContent;
    return { templateName, field, status: 'evolved', at: new Date().toISOString() };
  }

  /**
   * 批量进化后缀（常用于切换模型版本时更新 suffix）
   * @param {string} newSuffix
   */
  evolveSuffix(newSuffix) {
    this.params.promptSuffix = newSuffix;
    this.templates.videoPrompt.suffix = newSuffix;
    return { suffix: newSuffix, status: 'evolved', affected: ['videoPrompt'] };
  }
}

// ─────────────────────────────────────────────────
// 导出（兼容 CommonJS 与 ES Module）
// ─────────────────────────────────────────────────
if (typeof module !== 'undefined' && module.exports) {
  module.exports = PromptTemplateEngine;
  module.exports.PromptTemplateEngine = PromptTemplateEngine;
}
