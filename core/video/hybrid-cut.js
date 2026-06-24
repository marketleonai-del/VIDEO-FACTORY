const { execSync } = require('child_process');
const fs = require('fs');
const path = require('path');
const https = require('https');
const { URL } = require('url');

/**
 * =============================================================================
 * HybridCut Engine - AI + 真实素材混剪引擎
 * =============================================================================
 *
 * 核心逻辑:
 * 1. AI只做0-3秒钩子（最高杠杆），3秒后有序拼接真实素材
 * 2. 信任关键点用真素材（证言/真人脸/效果对比）
 * 3. 难拍/贵/不可能镜头用AI补
 * 4. 全片锁单一声纹 @voice1
 * 5. 逐镜标注 [真R/AI/混H]
 *
 * 转化链路: 痛点 → 旧法失败 → 救星 → 反差 → 产品接入 → 爽点 → 多场景 → CTA
 * =============================================================================
 */

class HybridCutEngine {
  constructor(ffmpegPath = 'ffmpeg', options = {}) {
    this.ffmpeg = ffmpegPath;
    this.tempDir = options.tempDir || './.uvg-out/temp';
    this.outputDir = options.outputDir || './.uvg-out/output';
    this.aiAPI = options.aiAPI || null;

    for (const dir of [this.tempDir, this.outputDir]) {
      if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
    }
  }

  // ==========================================================================
  // S1: 素材入库 & 智能标注
  // ==========================================================================

  ingestMaterials(materials) {
    return materials.map(m => ({
      ...m,
      meta: this.probeVideo(m.filePath),
      trustValue: this.assessTrustValue(m),
      replaceable: this.assessReplaceability(m),
      qualityOk: this.assessQuality(m),
      tags: this.autoTag(m),
      suggestedSlots: this.suggestSlots(m)
    }));
  }

  probeVideo(filePath) {
    try {
      const out = execSync(`${this.ffmpeg} -i "${filePath}" 2>&1`, { encoding: 'utf-8', timeout: 30000 });
      const dur = out.match(/Duration: (\d{2}):(\d{2}):(\d{2}\.\d{2})/);
      const duration = dur ? parseInt(dur[1]) * 3600 + parseInt(dur[2]) * 60 + parseFloat(dur[3]) : 0;
      const res = out.match(/(\d{2,4})x(\d{2,4})/);
      const fps = out.match(/(\d+(?:\.\d+)?) fps/);
      return { duration, width: res ? parseInt(res[1]) : 0, height: res ? parseInt(res[2]) : 0, fps: fps ? parseFloat(fps[1]) : 24, hasAudio: out.includes('Audio:') };
    } catch (e) {
      return { duration: 0, width: 0, height: 0, fps: 24, hasAudio: false };
    }
  }

  assessTrustValue(m) {
    const high = ['客户证言', '真人口播', '效果对比', '品牌资质', '真人脸'];
    const mid = ['产品实拍', '使用过程', '开箱'];
    if (high.some(t => m.type?.includes(t))) return 'high';
    if (mid.some(t => m.type?.includes(t))) return 'mid';
    return 'low';
  }

  assessReplaceability(m) {
    const irreplaceable = ['客户证言', '真人脸', '品牌资质', '真人口播'];
    return !irreplaceable.some(t => m.type?.includes(t));
  }

  assessQuality(m) {
    const h = m.meta?.height || m.quality || 0;
    return (m.duration || 0) >= 1 && h >= 720;
  }

  autoTag(m) {
    const tags = [m.type];
    if (m.type?.includes('真人')) tags.push('人脸');
    if (m.type?.includes('产品')) tags.push('产品');
    if (m.type?.includes('效果')) tags.push('对比');
    return tags;
  }

  suggestSlots(m) {
    const map = {
      '客户证言': ['效果展示', 'CTA前'],
      '真人口播': ['痛点引入', '产品讲解'],
      '产品实拍': ['产品接入', '爽点'],
      '效果对比': ['反差对比', '信任背书'],
      '使用过程': ['多场景', '爽点'],
      'B-roll': ['转场', '多场景'],
      '开箱': ['救星登场']
    };
    return map[m.type] || ['通用'];
  }

  // ==========================================================================
  // S2: 逐镜「真 vs AI」决策矩阵
  // ==========================================================================

  decideRealOrAI(scene, materials = []) {
    const s = scene.toLowerCase();
    const trust = ['证言', '真人脸', '效果对比', '品牌资质', '手与产品交互'];
    const ai = ['多场景切换', '极限演示', '空镜', '转场', '微观', '危险', '不可能'];
    const hybrid = ['产品展示', 'b-roll', '背景', '氛围'];

    if (trust.some(x => s.includes(x))) return 'R';
    if (ai.some(x => s.includes(x))) return 'AI';
    if (hybrid.some(x => s.includes(x))) return 'H';

    return materials.filter(m => m.trustValue === 'high' && m.qualityOk).length > 0 ? 'R' : 'AI';
  }

  decideScriptSources(script, materials) {
    return script.map(shot => ({ ...shot, source: this.decideRealOrAI(shot.content, materials) }));
  }

  // ==========================================================================
  // S3: AI补拍提示词生成
  // ==========================================================================

  generateAIPrompt(scene, anchors = {}, style = '手机实拍') {
    let prompt = scene;
    if (anchors.product) prompt += '，画面中产品为 @image1 同款';
    if (anchors.persona) prompt += '，人物特征锚定 @image3';

    const styles = {
      '手机实拍': '，竖屏9:16，手持实拍质感，轻微自然晃动，真实生活场景，明亮自然光',
      '电影感': '，竖屏9:16，电影级运镜，浅景深，高级色调，专业布光',
      '微距': '，竖屏9:16，微距特写，极致细节，纹理清晰，专业产品摄影',
      '快节奏': '，竖屏9:16，快速剪辑感，动感运镜，节奏强烈，视觉冲击力'
    };
    prompt += styles[style] || styles['手机实拍'];

    return {
      prompt,
      anchors,
      suffix: '保留真实纹理细节，no text, 防穿模，自然光影' + (style === '手机实拍' ? '，轻微手抖，环境反射' : ''),
      negative: 'morphing, extra fingers, deformed hands, text, watermark, logo, 产品变形, 穿模, 不自然面部, 恐怖谷',
      parameters: { ratio: '9:16', duration: 3, fps: 24, resolution: '720x1280' }
    };
  }

  // ==========================================================================
  // S4: 爆款AI开头生成（前3秒钩子 / Pattern Interrupt）
  // ==========================================================================

  async generateHookVideo(product, hookAngle = '视觉打断', anchors = {}, outputPath) {
    const hooks = {
      '痛点焦虑': `极端特写：${product}暴露出的令人震惊的问题，微距镜头，产品缺陷暴露，强烈视觉冲击力，纹理清晰，让观众瞬间皱眉头`,
      '反常识': `反常使用${product}的场景，出乎意料的用法，好奇心驱动，打破常规认知，"还能这样？"的瞬间`,
      '视觉打断': `${product}突然大特写冲入画面，极端机位，Pattern Interrupt，3秒内抓住注意力，非常规构图，视觉震撼`,
      '数字冲击': `10个${product}排成震撼阵列，数字视觉化，数量冲击感，密集排列，视觉压迫感`,
      '对比反差': `左右分屏：使用前 vs 使用后，同一时间同一画面内极端对比，反差巨大`,
      '悬念开箱': `一只手撕开${product}包装的瞬间，慢动作，期待感，神秘氛围，包装撕开的光线变化`,
      '极端测试': `${product}承受极限测试，火烧/水压/摔落，产品极限状态，张力十足`,
      '偷窥视角': `透过缝隙偷看到${product}的惊人效果，第一人称视角，窥视感，好奇心被强烈激发`
    };

    const prompt = this.generateAIPrompt(hooks[hookAngle] || hooks['视觉打断'], anchors, '快节奏');
    const hookPath = outputPath || path.join(this.tempDir, `hook_${Date.now()}.mp4`);

    if (this.aiAPI) {
      await this.callVideoAPI(prompt, hookPath, { mute: true });
    } else {
      this.createPlaceholderVideo(hookPath, `[HOOK] ${hookAngle}: ${product}`, 3);
    }

    return { videoPath: hookPath, prompt, duration: 3, angle: hookAngle, mute: true };
  }

  createPlaceholderVideo(outputPath, text, duration = 3) {
    const safe = text.replace(/'/g, "'\\''").substring(0, 80);
    try {
      execSync(`${this.ffmpeg} -f lavfi -i "color=c=black:s=720x1280:d=${duration}" -vf "drawtext=text='${safe}':fontsize=22:fontcolor=white:x=(w-text_w)/2:y=(h-text_h)/2" -r 24 -pix_fmt yuv420p -an -y "${outputPath}"`, { timeout: 30000 });
    } catch (e) {
      console.warn(`[HybridCut] 占位视频创建失败: ${e.message}`);
    }
  }

  // ==========================================================================
  // S5: 真 + AI 一致性锚定
  // ==========================================================================

  buildConsistencyCard(materials, aiScenes = []) {
    const real = materials.filter(m => m.trustValue === 'high');
    const prod = real.filter(m => m.tags?.includes('产品')).map(m => ({ id: m.id, filePath: m.filePath }));
    const face = real.filter(m => m.tags?.includes('人脸')).map(m => ({ id: m.id, filePath: m.filePath }));

    return {
      productLocked: prod.length > 0,
      personaLocked: face.length > 0,
      lightingMatched: '自然暖光 5500K',
      qualityUnified: '手机实拍质感',
      anchors: { product: prod[0] || null, scene: prod[1] || null, persona: face[0] || null },
      constraints: { aspectRatio: '9:16', targetResolution: '720x1280', lightingTemp: '5500K' }
    };
  }

  // ==========================================================================
  // S6: 人感护栏校验
  // ==========================================================================

  humanFeelCheck(editList, category = 'general') {
    const shots = editList.filter(s => s.shot);
    const total = shots.length || 1;
    const ai = shots.filter(s => s.source === 'AI').length;
    const real = shots.filter(s => s.source === 'R').length;
    const aiRatio = ai / total;

    const thresholds = { beauty: 0.3, health: 0.2, food: 0.25, general: 0.5 };
    const threshold = thresholds[category] || 0.5;

    const checklist = {
      口播自然度: real > 0,
      非完美真实细节: aiRatio < threshold,
      节奏呼吸感: total <= 8,
      真脸优先: shots.some((s, i) => s.source === 'R' && i < 3),
      AI占比合规: aiRatio <= threshold
    };

    const pass = Object.values(checklist).every(v => v);
    const suggestions = [];
    if (aiRatio > threshold) suggestions.push(`AI占比 ${Math.round(aiRatio * 100)}% 超限 ${Math.round(threshold * 100)}%，增加真素材`);
    if (!checklist['口播自然度']) suggestions.push('增加真人口播素材');
    if (!checklist['真脸优先']) suggestions.push('前3秒放入真人出镜');
    if (!checklist['节奏呼吸感']) suggestions.push('精简至6-8镜');

    return {
      pass, aiRatio: Math.round(aiRatio * 100) + '%', humanFeelScore: Math.max(0, Math.round(10 - aiRatio * 10)),
      checklist, suggestions, category
    };
  }

  // ==========================================================================
  // S7: 生成混剪台本
  // ==========================================================================

  generateEditScript(product, angle, materials, targetDuration = 30) {
    const script = [];
    let t = 0;
    const hi = materials.filter(m => m.trustValue === 'high' && m.qualityOk);
    const mid = materials.filter(m => m.trustValue === 'mid' && m.qualityOk);

    // 镜1: AI钩子 0-3s
    script.push({ shot: 1, source: 'AI', time: '0-3s', content: `${angle.hook || '视觉打断'}钩子`, aiPrompt: this.generateAIPrompt(`${product} ${angle.hook || '视觉打断'}`, { product }), trust: 'low', humanFeel: 7, note: '静音，统一配音@voice1' });
    t = 3;

    // 镜2: 真人口播痛点 3-8s
    const d2 = Math.min(5, hi[0]?.meta?.duration || 5);
    script.push({ shot: 2, source: 'R', time: `${t}-${t + d2}s`, content: `真人口播：${angle.pain || '痛点共鸣'}`, material: hi[0]?.id, materialFile: hi[0]?.filePath, trust: 'high', humanFeel: 10, note: '真实情绪，自然停顿' });
    t += d2;

    // 镜3: AI过渡 旧法失败
    if (t < targetDuration * 0.4) {
      script.push({ shot: 3, source: 'AI', time: `${t}-${t + 3}s`, content: '旧方法失败场景 / 问题放大', aiPrompt: this.generateAIPrompt(`${product} 旧方法无效、令人沮丧的场景`, { product }, '电影感'), trust: 'low', humanFeel: 7, note: '强化痛点' });
      t += 3;
    }

    // 镜4: 真素材 救星登场
    const d4 = Math.min(5, mid[0]?.meta?.duration || 5);
    script.push({ shot: 4, source: 'R', time: `${t}-${t + d4}s`, content: `${angle.solution || '救星'}登场：${product}出现`, material: mid[0]?.id, materialFile: mid[0]?.filePath, trust: 'high', humanFeel: 9, note: '产品首次亮相' });
    t += d4;

    // 镜5: AI多场景快切
    if (t < targetDuration * 0.7) {
      const d5 = Math.min(5, targetDuration - t - 7);
      if (d5 > 2) {
        script.push({ shot: 5, source: 'AI', time: `${t}-${t + d5}s`, content: '多场景快切：使用效果展示', aiPrompt: this.generateAIPrompt(`${product} 多个使用场景快速切换，爽点展示`, { product }, '快节奏'), trust: 'low', humanFeel: 7, note: '展示适用性' });
        t += d5;
      }
    }

    // 镜6: 真素材 效果/证言
    const d6 = Math.min(5, hi[1]?.meta?.duration || 4);
    if (d6 > 0 && hi[1]) {
      script.push({ shot: 6, source: 'R', time: `${t}-${t + d6}s`, content: '真实效果对比 / 客户证言', material: hi[1]?.id, materialFile: hi[1]?.filePath, trust: 'high', humanFeel: 10, note: '信任高潮，真脸+真效果' });
      t += d6;
    }

    // 镜7: CTA
    const d7 = Math.max(3, targetDuration - t);
    script.push({ shot: 7, source: 'AI', time: `${t}-${t + d7}s`, content: 'CTA结尾：产品展示+行动号召', aiPrompt: this.generateAIPrompt(`${product} 产品居中展示，干净背景，适合叠加CTA`, { product }), trust: 'low', humanFeel: 8, note: '结尾定格' });

    script._meta = {
      consistencyCard: this.buildConsistencyCard(materials),
      humanCheck: this.humanFeelCheck(script),
      totalShots: script.length,
      targetDuration
    };
    return script;
  }

  // ==========================================================================
  // S8: ffmpeg 实际拼接执行
  // ==========================================================================

  async executeEdit(editScript, outputPath, options = {}) {
    const shots = editScript.filter(s => s.shot);
    const processed = [];
    console.log(`[HybridCut] 开始混剪，共 ${shots.length} 镜`);

    for (const shot of shots) {
      const shotFile = path.join(this.tempDir, `shot_${shot.shot}_${Date.now()}.mp4`);
      try {
        if (shot.source === 'AI') {
          await this.renderAIShot(shot, shotFile, options);
        } else {
          await this.renderRealShot(shot, shotFile);
        }
        const normFile = path.join(this.tempDir, `norm_${shot.shot}.mp4`);
        await this.normalizeVideo(shotFile, normFile);
        processed.push(normFile);
      } catch (e) {
        console.error(`[HybridCut] 镜${shot.shot} 失败: ${e.message}`);
        this.createPlaceholderVideo(shotFile, `[缺失] 镜${shot.shot}`, this.parseDuration(shot.time));
        processed.push(shotFile);
      }
    }

    // concat 拼接
    const concatFile = path.join(this.tempDir, `concat_${Date.now()}.txt`);
    fs.writeFileSync(concatFile, processed.map(f => `file '${path.resolve(f)}'`).join('\n'));

    const final = outputPath || path.join(this.outputDir, `hybrid_${Date.now()}.mp4`);
    try {
      execSync(`${this.ffmpeg} -f concat -safe 0 -i "${concatFile}" -c copy -movflags +faststart -y "${final}"`, { timeout: 300000 });
    } catch (e) {
      console.warn('[HybridCut] 直接拼接失败，重编码中...');
      await this.reencodeConcat(processed, final);
    }

    if (!options.keepTemp) this.cleanup(processed.concat([concatFile]));

    return {
      outputPath: final,
      shots: shots.length,
      sources: { AI: shots.filter(s => s.source === 'AI').length, R: shots.filter(s => s.source === 'R').length }
    };
  }

  async renderAIShot(shot, outputFile, options = {}) {
    if (this.aiAPI) {
      await this.callVideoAPI(shot.aiPrompt, outputFile, { mute: true, ...options });
    } else {
      this.createPlaceholderVideo(outputFile, `[AI] ${(shot.aiPrompt?.prompt || shot.content).substring(0, 50)}`, this.parseDuration(shot.time));
    }
  }

  async renderRealShot(shot, outputFile) {
    if (!shot.materialFile) throw new Error(`镜${shot.shot} 缺少素材路径`);
    const [s, e] = shot.time.replace(/s/g, '').split('-').map(Number);
    const d = e - s;
    execSync(`${this.ffmpeg} -i "${shot.materialFile}" -ss ${s} -t ${d} -vf "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,fps=24,format=yuv420p" -c:v libx264 -preset fast -crf 23 -c:a aac -b:a 128k -ar 44100 -movflags +faststart -y "${outputFile}"`, { timeout: 120000 });
  }

  async normalizeVideo(input, output) {
    execSync(`${this.ffmpeg} -i "${input}" -vf "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,fps=24,format=yuv420p,setdar=9/16" -c:v libx264 -preset fast -crf 23 -c:a aac -b:a 128k -ar 44100 -movflags +faststart -y "${output}"`, { timeout: 120000 });
  }

  async reencodeConcat(files, output) {
    const inputs = files.map((f, i) => `-i "${f}"`).join(' ');
    const chain = files.map((_, i) => `[${i}:v][${i}:a]`).join('');
    const n = files.length;
    execSync(`${this.ffmpeg} ${inputs} -filter_complex "${chain}concat=n=${n}:v=1:a=1[outv][outa]" -map "[outv]" -map "[outa]" -c:v libx264 -preset fast -crf 23 -c:a aac -b:a 128k -movflags +faststart -y "${output}"`, { timeout: 300000 });
  }

  // ==========================================================================
  // S9: AI视频生成API（支持丽帧/可灵/Seedance/Veo）
  // ==========================================================================

  async callVideoAPI(promptConfig, outputPath, options = {}) {
    if (!this.aiAPI) throw new Error('未配置AI视频生成API');
    const provider = this.aiAPI.provider || 'lizhen';
    console.log(`[HybridCut] 调用 ${provider} API: ${promptConfig.prompt?.substring(0, 40)}...`);

    switch (provider) {
      case 'lizhen': return this.callProviderAPI(promptConfig, outputPath, options, 'lizhen');
      case 'keling': return this.callProviderAPI(promptConfig, outputPath, options, 'keling');
      case 'seedance': return this.callProviderAPI(promptConfig, outputPath, options, 'seedance');
      case 'veo': return this.callProviderAPI(promptConfig, outputPath, options, 'veo');
      default: throw new Error(`不支持的AI提供商: ${provider}`);
    }
  }

  async callProviderAPI(promptConfig, outputPath, options, name) {
    const payload = {
      prompt: promptConfig.prompt,
      negative_prompt: promptConfig.negative,
      width: 720, height: 1280,
      duration: options.duration || 3,
      fps: 24,
      image_ref: promptConfig.anchors?.product ? [promptConfig.anchors.product].filter(Boolean) : []
    };

    if (this.aiAPI.endpoint) {
      try {
        const res = await this.httpPost(this.aiAPI.endpoint, payload, this.aiAPI.key);
        if (res.video_url) await this.downloadFile(res.video_url, outputPath);
        return outputPath;
      } catch (e) {
        console.warn(`[HybridCut] ${name} API调用失败，使用占位: ${e.message}`);
      }
    }

    this.createPlaceholderVideo(outputPath, `[${name}] ${payload.prompt.substring(0, 40)}`, payload.duration);
    return outputPath;
  }

  // ==========================================================================
  // 工具方法
  // ==========================================================================

  parseDuration(timeStr) {
    if (typeof timeStr === 'number') return timeStr;
    const p = timeStr.replace(/s/g, '').split('-');
    return p.length === 2 ? parseInt(p[1]) - parseInt(p[0]) : parseInt(p[0]) || 3;
  }

  async httpPost(url, payload, apiKey) {
    return new Promise((resolve, reject) => {
      const u = new URL(url);
      const data = JSON.stringify(payload);
      const req = https.request({ hostname: u.hostname, port: u.port || 443, path: u.pathname, method: 'POST', headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${apiKey}`, 'Content-Length': data.length } }, res => { let b = ''; res.on('data', c => b += c); res.on('end', () => { try { resolve(JSON.parse(b)); } catch { resolve(b); } }); });
      req.on('error', reject);
      req.write(data);
      req.end();
    });
  }

  async downloadFile(url, targetPath) {
    return new Promise((resolve, reject) => {
      const file = fs.createWriteStream(targetPath);
      https.get(url, r => { if (r.statusCode !== 200) { reject(new Error(`HTTP ${r.statusCode}`)); return; } r.pipe(file); file.on('finish', () => { file.close(); resolve(targetPath); }); }).on('error', reject);
    });
  }

  cleanup(files) {
    for (const f of files) { try { if (fs.existsSync(f)) fs.unlinkSync(f); } catch (e) {} }
  }

  static version() {
    return { name: 'HybridCut', version: '1.0.0', build: '2025-07-17' };
  }
}

module.exports = { HybridCutEngine };
