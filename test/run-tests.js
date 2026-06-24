#!/usr/bin/env node
/**
 * VIDEO-FACTORY 测试套件
 * 
 * 运行方式:
 *   node test/run-tests.js          # 运行全部测试
 *   node test/run-tests.js --fast   # 跳过FFmpeg等慢测试
 *   npm test                        # 通过 package.json 运行
 */

'use strict';

const path = require('path');

// ============================================
// 简易测试框架（零依赖，不依赖任何外部库）
// ============================================
class TestRunner {
  constructor(options = {}) {
    this.tests = [];
    this.passed = 0;
    this.failed = 0;
    this.skipped = 0;
    this.options = options;
  }

  /** 注册一个测试用例 */
  test(name, fn, opts = {}) {
    this.tests.push({ name, fn, skip: opts.skip || false, slow: opts.slow || false });
  }

  /** 运行所有测试 */
  async run() {
    console.log('\n🧪 VIDEO-FACTORY 测试套件 v3.0.0');
    console.log('=====================================\n');

    const startTime = Date.now();

    for (const { name, fn, skip, slow } of this.tests) {
      // --fast 模式下跳过慢测试
      if (this.options.fast && slow) {
        console.log(`  ⏭️  ${name} (跳过 - fast模式)`);
        this.skipped++;
        continue;
      }

      if (skip) {
        console.log(`  ⏭️  ${name} (跳过)`);
        this.skipped++;
        continue;
      }

      try {
        await fn();
        console.log(`  ✅ ${name}`);
        this.passed++;
      } catch (e) {
        console.log(`  ❌ ${name}: ${e.message}`);
        if (this.options.verbose && e.stack) {
          console.log(`     ${e.stack.split('\n').slice(1, 3).join('\n     ')}`);
        }
        this.failed++;
      }
    }

    const duration = Date.now() - startTime;
    const total = this.passed + this.failed + this.skipped;

    console.log('\n-------------------------------------');
    console.log(`📊 总计: ${total} | ✅ 通过: ${this.passed} | ❌ 失败: ${this.failed} | ⏭️ 跳过: ${this.skipped}`);
    console.log(`⏱️  耗时: ${duration}ms`);
    console.log('=====================================\n');

    process.exit(this.failed > 0 ? 1 : 0);
  }

  // ---- 断言方法 ----

  /** 严格相等断言 */
  assertEqual(actual, expected, msg) {
    if (actual !== expected) {
      throw new Error(`${msg || 'Assertion failed'}: expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
    }
  }

  /** 深度相等断言（简单对象） */
  assertDeepEqual(actual, expected, msg) {
    const actualJson = JSON.stringify(actual);
    const expectedJson = JSON.stringify(expected);
    if (actualJson !== expectedJson) {
      throw new Error(`${msg || 'Deep equal failed'}: expected ${expectedJson}, got ${actualJson}`);
    }
  }

  /** 真值断言 */
  assertTrue(value, msg) {
    if (!value) throw new Error(msg || 'Expected value to be truthy');
  }

  /** 假值断言 */
  assertFalse(value, msg) {
    if (value) throw new Error(msg || 'Expected value to be falsy');
  }

  /** 包含断言 */
  assertIncludes(haystack, needle, msg) {
    if (!haystack.includes(needle)) {
      throw new Error(`${msg || 'Includes failed'}: "${needle}" not in "${haystack}"`);
    }
  }

  /** 抛出异常断言 */
  async assertThrows(fn, expectedMsg, msg) {
    try {
      await fn();
      throw new Error(msg || `Expected function to throw${expectedMsg ? ` "${expectedMsg}"` : ''}`);
    } catch (e) {
      if (expectedMsg && !e.message.includes(expectedMsg)) {
        throw new Error(`${msg || 'Wrong exception'}: expected "${expectedMsg}", got "${e.message}"`);
      }
    }
  }
}

// ============================================
// 解析命令行参数
// ============================================
const testOptions = {
  fast: process.argv.includes('--fast'),
  verbose: process.argv.includes('--verbose')
};

const runner = new TestRunner(testOptions);

// ============================================
// 测试用例定义
// ============================================

// 项目根目录路径（测试文件位于 test/ 子目录）
const projectRoot = path.resolve(__dirname, '..');

// ---- 1. SkillParams 序列化/反序列化 ----
runner.test('SkillParams序列化/反序列化', () => {
  const SkillParams = require(path.join(projectRoot, 'core/evolution/SkillParams'));
  const params = new SkillParams();
  const serialized = params.serialize();
  
  runner.assertTrue(typeof serialized === 'string', 'serialize() 应返回字符串');
  
  const deserialized = SkillParams.deserialize(serialized);
  runner.assertEqual(deserialized.version, params.version, '版本号应一致');
});

// ---- 2. QualitySignals 质量计算 ----
runner.test('QualitySignals质量计算', () => {
  const QualitySignals = require(path.join(projectRoot, 'core/evolution/QualitySignals'));
  
  const highQuality = QualitySignals.computeQuality({
    success: true, retries: 0, regenerated: false, qcScore: 110
  });
  runner.assertTrue(highQuality > 0.5, '高质量信号应 > 0.5');

  const lowQuality = QualitySignals.computeQuality({
    success: false, retries: 3, regenerated: true, qcScore: 50
  });
  runner.assertTrue(lowQuality < 0.5, '低质量信号应 < 0.5');
});

// ---- 3. EvolutionEngine 进化一轮 ----
runner.test('EvolutionEngine进化一轮', () => {
  const EvolutionEngine = require(path.join(projectRoot, 'core/evolution/EvolutionEngine'));
  const SkillParams = require(path.join(projectRoot, 'core/evolution/SkillParams'));
  
  const engine = new EvolutionEngine(new SkillParams());
  const result = engine.evolveRound({ success: true, retries: 0, qcScore: 115 });
  
  runner.assertTrue(result !== null, '进化应返回结果');
  runner.assertTrue(result !== undefined, '进化结果不应为 undefined');
});

// ---- 4. HookEngine 生成角度卡 ----
runner.test('HookEngine生成角度卡', () => {
  const HookEngine = require(path.join(projectRoot, 'core/creative/hook-engine'));
  const SkillParams = require(path.join(projectRoot, 'core/evolution/SkillParams'));
  
  const engine = new HookEngine(new SkillParams());
  const card = engine.generateAngleCard('便携榨汁杯', 'douyin', 6);
  
  runner.assertTrue(Array.isArray(card), '角度卡应为数组');
  runner.assertTrue(card.length > 0, '应生成至少一个角度卡');
  runner.assertTrue(card[0].WinScore > 0, 'WinScore应 > 0');
});

// ---- 5. MatrixEngine 生成矩阵 ----
runner.test('MatrixEngine生成矩阵', () => {
  const MatrixEngine = require(path.join(projectRoot, 'core/creative/matrix-engine'));
  const HookEngine = require(path.join(projectRoot, 'core/creative/hook-engine'));
  const SkillParams = require(path.join(projectRoot, 'core/evolution/SkillParams'));
  
  const hookEngine = new HookEngine(new SkillParams());
  const matrixEngine = new MatrixEngine(hookEngine, new SkillParams());
  
  const winner = {
    hook: '测试钩子',
    structure: '8要素',
    sellingPoint: '便携'
  };
  const table = matrixEngine.generateMatrixTable(winner, 10, 'douyin');
  
  runner.assertTrue(table.variants && table.variants.length > 0, '应生成变体列表');
});

// ---- 6. QualityGate 质检评分 ----
runner.test('QualityGate质检评分', () => {
  const QualityGate = require(path.join(projectRoot, 'core/quality/QualityGate'));
  const SkillParams = require(path.join(projectRoot, 'core/evolution/SkillParams'));
  
  const gate = new QualityGate(new SkillParams());
  
  const script = {
    hook: '你有没有过——',
    shots: [{ source: 'R' }, { source: 'AI' }],
    winScore: 85
  };
  
  const result = gate.inspect(script, { aiLabeled: true, voice1Locked: true });
  runner.assertTrue(result.totalScore > 0, '总分应 > 0');
  runner.assertTrue(result.passed !== undefined, '应有 passed 字段');
});

// ---- 7. FFmpegUtils 检查可用性（慢测试） ----
runner.test('FFmpegUtils检查可用性', async () => {
  const FFmpegUtils = require(path.join(projectRoot, 'core/video/ffmpeg-utils'));
  const ffmpeg = new FFmpegUtils();
  const available = await ffmpeg.check();
  console.log(`    ffmpeg ${available ? '✅ 可用' : '⏭️ 未安装'}`);
  // 不强制要求 ffmpeg 安装，仅做可用性检查
}, { slow: true });

// ---- 8. VoiceLockManager 状态检查 ----
runner.test('VoiceLockManager状态检查', () => {
  const { VoiceLockManager } = require(path.join(projectRoot, 'core/audio/voice-lock'));
  const vlm = new VoiceLockManager();
  const check = vlm.selfCheck();
  
  runner.assertEqual(check.voiceLocked, false, '初始状态应未锁定');
  runner.assertTrue(check.ready !== undefined, '应有 ready 字段');
});

// ---- 9. PromptTemplates 渲染 ----
runner.test('PromptTemplate渲染', () => {
  const PromptTemplateEngine = require(path.join(projectRoot, 'core/creative/prompt-templates'));
  const SkillParams = require(path.join(projectRoot, 'core/evolution/SkillParams'));
  
  const engine = new PromptTemplateEngine(new SkillParams());
  const rendered = engine.render('productAnalysis', { product: '测试产品' });
  
  runner.assertTrue(typeof rendered === 'object', '应返回对象');
  runner.assertTrue(rendered.user.includes('测试产品'), 'user消息应包含产品名');
});

// ---- 10. AgentProtocol 初始化 ----
runner.test('AgentProtocol初始化', () => {
  const AgentProtocol = require(path.join(projectRoot, 'core/swarm/agent-protocol'));
  const protocol = new AgentProtocol('http://localhost:8089', 'test_001', 'generator');
  
  runner.assertEqual(protocol.role, 'generator', '角色应一致');
  runner.assertEqual(protocol.agentId, 'test_001', 'agentId应一致');
  runner.assertEqual(protocol.hqUrl, 'http://localhost:8089', 'HQ地址应一致');
});

// ============================================
// 运行测试
// ============================================
runner.run();
