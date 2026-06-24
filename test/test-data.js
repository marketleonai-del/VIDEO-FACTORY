/**
 * =============================================================================
 * VIDEO-FACTORY v3.0 — 测试数据
 * =============================================================================
 *
 * 提供完整的测试数据集，覆盖：
 *   - 3 个不同品类的测试产品（小家电/家电/健身器材）
 *   - 标准带货脚本（含 5 镜分镜）
 *   - 3 种类型素材（真人口播/产品实拍/效果对比）
 *   - 完整隐式信号（用于进化引擎测试）
 *
 * 使用方式：
 * ```javascript
 * const testData = require('./test/test-data');
 * const product = testData.testProducts[0];           // 便携榨汁杯
 * const script = testData.testScript;                  // 标准脚本
 * const materials = testData.testMaterials;            // 素材库
 * const signals = testData.testSignals;                // 成功信号
 * const failSignals = testData.testSignalsFailure;     // 失败信号
 * ```
 *
 * 每个数据集都附带 `meta` 字段说明其用途和预期行为。
 * =============================================================================
 */

'use strict';

module.exports = {

  // ============================================================================
  // 测试产品集（3 个不同品类，覆盖常见带货场景）
  // ============================================================================
  testProducts: [
    {
      name: '便携榨汁杯',
      category: '小家电',
      price: 99,
      currency: 'CNY',
      painPoints: ['清洗麻烦', '续航短', '榨完就分层'],
      sellingPoints: ['10秒榨汁', 'USB充电', '易清洗', '小巧便携', '一键操作'],
      targetAudience: ['上班族', '健身人群', '学生党', '宝妈', '户外爱好者'],
      usageScenarios: ['办公室', '健身房', '户外', '宿舍', '旅行'],
      platform: 'douyin',
      conversionGoal: '冲动转化',
      meta: {
        description: '低客单小家电，适合痛点放大+反常识角度',
        expectedTopAngle: 'A痛点放大 或 B反常识',
        estimatedWinScore: '75-86'
      }
    },
    {
      name: '除螨仪',
      category: '家电',
      price: 299,
      currency: 'CNY',
      painPoints: ['螨虫过敏', '床品清洁难', '传统晒被麻烦'],
      sellingPoints: ['UV杀菌', '强力吸力', '无线便携', '噪音低', '长续航'],
      targetAudience: ['过敏人群', '有娃家庭', '宠物主人', '洁癖星人', '租房党'],
      usageScenarios: ['卧室', '沙发', '宠物窝', '婴儿床', '车内'],
      platform: 'douyin',
      conversionGoal: '信任转化',
      meta: {
        description: '中客单家电，适合信任背书+痛点放大角度',
        expectedTopAngle: 'E信任背书 或 A痛点放大',
        estimatedWinScore: '78-85'
      }
    },
    {
      name: '筋膜枪',
      category: '健身器材',
      price: 199,
      currency: 'CNY',
      painPoints: ['肌肉酸痛', '按摩贵', '运动后恢复慢', '手法不专业'],
      sellingPoints: ['6档调节', '静音', '长续航', '4个按摩头', '深度放松'],
      targetAudience: ['健身爱好者', '久坐白领', '运动员', '老年人', '瑜伽练习者'],
      usageScenarios: ['健身房', '办公室', '家中', '户外', '运动后'],
      platform: 'kuaishou',
      conversionGoal: '快速转化',
      meta: {
        description: '中客单健身器材，适合身份认同+数字冲击角度',
        expectedTopAngle: 'C身份认同 或 D省钱算账',
        estimatedWinScore: '72-82'
      }
    }
  ],

  // ============================================================================
  // 测试脚本（标准 5 镜分镜，含混剪决策标记）
  // ============================================================================
  testScript: {
    product: '筋膜枪',
    hook: '你有没有过——运动完肌肉酸痛到睡不着？',
    angle: {
      family: 'A痛点放大',
      title: '运动完肌肉酸痛的人，终于有救了',
      winScore: 85
    },
    shots: [
      {
        shot: 1,
        source: 'AI',
        scene: '运动后腿酸痛特写（微距，肌肉紧绷）',
        time: '0-3',
        content: '痛点放大：运动后肌肉酸痛的崩溃瞬间',
        aiPrompt: {
          prompt: '运动后腿酸痛特写，微距镜头，肌肉紧绷纹理，强烈视觉冲击力，竖屏9:16，手持实拍质感',
          anchors: { product: null },
          style: '快节奏',
          mute: true
        },
        trust: 'low',
        humanFeel: 7,
        note: 'AI钩子，静音，叠加@voice1旁白'
      },
      {
        shot: 2,
        source: 'R',
        scene: '真人口播痛点',
        time: '3-8',
        content: '真人口播：你有没有过运动完肌肉酸痛到睡不着？',
        material: 'R1',
        trust: 'high',
        humanFeel: 10,
        note: '真实情绪，自然停顿，原声(=@voice1基准)'
      },
      {
        shot: 3,
        source: 'AI',
        scene: '筋膜枪使用演示（多机位快切）',
        time: '8-15',
        content: '救星登场：筋膜枪出现，6档调节演示',
        aiPrompt: {
          prompt: '手持筋膜枪按摩腿部肌肉，多机位快切，专业运动场景，竖屏9:16，产品居中清晰',
          anchors: { product: '@image1' },
          style: '快节奏',
          mute: true
        },
        trust: 'low',
        humanFeel: 7,
        note: 'AI补拍，静音，@voice1旁白讲解卖点'
      },
      {
        shot: 4,
        source: 'R',
        scene: '真实放松效果（面部特写+腿部）',
        time: '15-20',
        content: '效果见证：使用后真实放松表情+腿部对比',
        material: 'R3',
        trust: 'high',
        humanFeel: 10,
        note: '信任高潮，真脸+真效果，自然光'
      },
      {
        shot: 5,
        source: 'AI',
        scene: 'CTA产品展示（干净背景，居中）',
        time: '20-25',
        content: 'CTA：筋膜枪居中展示，价格标签+行动号召',
        aiPrompt: {
          prompt: '筋膜枪产品居中展示，干净白色背景，适合叠加CTA文字，竖屏9:16，产品摄影质感',
          anchors: { product: '@image1' },
          style: '手机实拍',
          mute: true
        },
        trust: 'low',
        humanFeel: 8,
        note: '结尾定格，静音，@voice1 CTA旁白'
      }
    ],
    voiceTrack: {
      voiceId: 'voice1',
      source: '真人克隆（CosyVoice）',
      script: '你是不是也运动完肌肉酸痛到睡不着？（停顿）我之前也是，每次健完身腿像灌了铅一样。（节奏加快）直到我用了这个筋膜枪——6档调节，想轻想重自己控制。（强调）最关键的是，它几乎没噪音，办公室用也不尴尬。（CTA）现在下单只要199，链接在左下角，别让自己再难受了。',
      emotion: '亲切自然',
      pacing: '前慢后快',
      duration: 25
    },
    platform: 'douyin',
    duration: 25,
    winScore: 85,
    meta: {
      description: '标准5镜混剪台本：AI钩子+真人痛点+AI演示+真人效果+AI CTA',
      aiRatio: '3/5 = 60%',
      realRatio: '2/5 = 40%',
      expectedHumanFeel: 8.4,
      costEstimate: '$0.42 (3s AI钩子 × $0.07/s × 2镜)'
    }
  },

  // ============================================================================
  // 测试素材库（3 种类型，含混剪决策所需元数据）
  // ============================================================================
  testMaterials: [
    {
      id: 'R1',
      type: '真人口播',
      filePath: './test/fixtures/R1_host.mp4',
      duration: 15,
      resolution: { width: 1080, height: 1920 },
      fps: 30,
      trustValue: 'high',
      replaceable: false,
      qualityOk: true,
      tags: ['真人口播', '人脸', '痛点'],
      suggestedSlots: ['痛点引入', '产品讲解'],
      meta: { description: '真人博主口播，自然停顿，适合痛点引入' }
    },
    {
      id: 'R2',
      type: '产品实拍',
      filePath: './test/fixtures/R2_product.mp4',
      duration: 20,
      resolution: { width: 1080, height: 1920 },
      fps: 30,
      trustValue: 'medium',
      replaceable: true,
      qualityOk: true,
      tags: ['产品实拍', '产品', '展示'],
      suggestedSlots: ['产品接入', '爽点'],
      meta: { description: '产品360度实拍，光线良好，适合产品亮相' }
    },
    {
      id: 'R3',
      type: '效果对比',
      filePath: './test/fixtures/R3_compare.mp4',
      duration: 10,
      resolution: { width: 1080, height: 1920 },
      fps: 30,
      trustValue: 'high',
      replaceable: false,
      qualityOk: true,
      tags: ['效果对比', '人脸', '对比'],
      suggestedSlots: ['反差对比', '信任背书'],
      meta: { description: '使用前后真实对比，真脸出镜，信任核心素材' }
    }
  ],

  // ============================================================================
  // 测试隐式信号（用于进化引擎测试）
  // ============================================================================

  // ── 成功信号（高质量生成）─────────────────────────────
  testSignals: {
    success: true,
    retries: 0,
    regenerated: false,
    qcScore: 110,
    qcPass: true,
    assembleOk: true,
    ms: 120000,
    model: 'kuaizi',
    scene: '痛点放大',
    angle: 'A',
    saved: true,
    reused: false,
    aborted: false,
    advancedUsed: true,
    platform: 'douyin',
    durationSec: 30,
    meta: {
      description: '高质量成功信号：0重试/高质检分/正常耗时/已保存',
      expectedQuality: 0.92,
      expectedEvolveDirection: '强化 A 族权重'
    }
  },

  // ── 失败信号（生成异常）───────────────────────────────
  testSignalsFailure: {
    success: false,
    retries: 3,
    regenerated: true,
    qcScore: 65,
    qcPass: false,
    assembleOk: false,
    ms: 450000,
    model: 'kuaizi',
    scene: '情绪共鸣',
    angle: 'F',
    saved: false,
    reused: false,
    aborted: true,
    advancedUsed: false,
    platform: 'douyin',
    durationSec: 60,
    meta: {
      description: '低质量失败信号：3重试/质检未通过/超时/用户放弃',
      expectedQuality: 0.15,
      expectedEvolveDirection: '弱化 F 族权重，回归保守'
    }
  },

  // ── 中等信号（一般质量）───────────────────────────────
  testSignalsMedium: {
    success: true,
    retries: 1,
    regenerated: false,
    qcScore: 95,
    qcPass: true,
    assembleOk: true,
    ms: 180000,
    model: 'kuaizi',
    scene: '反常识',
    angle: 'B',
    saved: false,
    reused: false,
    aborted: false,
    advancedUsed: true,
    platform: 'kuaishou',
    durationSec: 15,
    meta: {
      description: '中等质量信号：1重试/质检刚过/稍慢/未保存',
      expectedQuality: 0.68,
      expectedEvolveDirection: '微调 B 族权重'
    }
  },

  // ============================================================================
  // 矩阵测试数据（用于矩阵引擎测试）
  // ============================================================================
  testMatrixConfig: {
    winner: {
      hook: '去污前后对比冲击',
      structure: '痛点→崩溃→救星→反差→产品→爽点→多场景→CTA',
      coreSellingPoint: '强力去污+温和不伤手',
      platform: 'douyin',
      duration: 25,
      winScore: 88
    },
    variants: 6,
    accounts: [
      { id: 'A', persona: '宝妈', platform: 'kuaishou' },
      { id: 'B', persona: '硬核测评', platform: 'douyin' },
      { id: 'C', persona: '精致生活', platform: 'xiaohongshu' }
    ],
    expected: {
      maxCrossOverlap: 0.20,
      minAxesDiff: 2,
      eachPassQC: true
    }
  },

  // ============================================================================
  // 蜂群测试数据（用于 AgentCoordinator 测试）
  // ============================================================================
  testSwarm: {
    agents: [
      { role: 'generator', count: 10, capabilities: { model: 'lizhen', maxConcurrent: 3 } },
      { role: 'evolver', count: 5, capabilities: { model: 'default', maxConcurrent: 5 } },
      { role: 'qa', count: 3, capabilities: { maxConcurrent: 10 } },
      { role: 'creative', count: 2, capabilities: { maxConcurrent: 2 } }
    ],
    batchTask: {
      product: { name: '便携榨汁杯', category: '小家电', sellingPoints: ['10秒榨汁', 'USB充电'] },
      count: 20,
      expectedTime: 10  // 预估秒数（小规模测试）
    }
  },

  // ============================================================================
  // 进化参数测试数据（用于 EvolutionEngine 测试）
  // ============================================================================
  testSkillParams: {
    angleWeights: { A: 1.0, B: 1.0, C: 1.0, D: 1.0, E: 1.0, F: 1.0 },
    modelBias: { kuaizi: 1.0, agnes: 0.8, kling: 1.0, seedance: 0.9 },
    hookTemplates: { H1: 1.0, H2: 1.0, H3: 1.0, H4: 1.0, H5: 1.0, H6: 1.0, H7: 1.0, H8: 1.0, H9: 1.0, H10: 1.0 },
    promptSuffix: '手机实拍质感, no text',
    qcThreshold: 100,
    version: 'v0-test'
  },

  // ============================================================================
  // 元数据
  // ============================================================================
  meta: {
    version: '3.0.0',
    generatedAt: '2026-07-17',
    totalProducts: 3,
    totalShots: 5,
    totalMaterials: 3,
    signalVariations: 3,
    coverage: ['小家电', '家电', '健身器材'],
    platforms: ['douyin', 'kuaishou', 'xiaohongshu']
  }
};
