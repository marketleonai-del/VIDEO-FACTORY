# 生成层一致性控制（防跨段漂移）

跨段视觉跳变（人变脸、产品变形、场景换家具、光线换时间）是拼接感的第二大来源。控制手段按成本从低到高：一致性锁（纯文字，必用）→ 首帧图参考 → 续帧法。

## 1. 一致性锁（每段提示词必带）

四把锁写成固定文字块，**逐字复制**到每一段的提示词里。不要每段重新措辞——同义改写就是漂移的开始。

**人物 DNA 串**（有人物时）：一次性锁死可识别特征，20-35 词。
```
[DNA] a 28-year-old Mexican woman, shoulder-length wavy dark brown hair, light olive skin,
small gold stud earrings, wearing the same mustard yellow knit sweater, natural makeup,
consistent facial features across all shots
```
要点：写"可辨识锚点"（发型发色、肤色、一件不换的衣服、一个小饰品），不写笼统的"beautiful"。服装是最强的跨段身份锚——**全片不换装**，必须换装就安排在"进出画/遮挡切"接缝处换。

**产品锁定串**：
```
[PRODUCT-LOCK] the exact same [product name]: keep exact shape, colors, materials, logo
position and proportions, strict chromatic fidelity, ZERO morphing, no design variation
```
产品图能上传就每段都上传同一张，文字锁照写。

**场景锚点**：每个场景选 2-3 个可辨识背景物写死。
```
[SCENE] the same small bright kitchen: white subway tile backsplash, a potted basil plant
on the left of the counter, wooden cutting board leaning against the wall
```
换场景的段就换一套锚点，但同场景的所有段逐字同串。

**光线锁**：
```
[LIGHT] consistent warm morning sunlight from a window on the right, soft shadows,
color temperature around 5000K, same time of day across all shots
```
全片一个时间感。剧情需要时间流逝时，光线变化只安排在"进出画"接缝处发生。

**通用后缀**（画质与防崩，与各段内容无关，逐字复制）：
```
[SUFFIX] 4K, UGC style, shot on smartphone, handheld breathing effect, natural lighting,
strictly NO mirror selfie, NO text, NO subtitles, anatomically correct hands, no extra fingers
```

每段提示词组装顺序：`[DNA] + [PRODUCT-LOCK] + [SCENE] + [LIGHT] + 本段镜头内容 + [SUFFIX]`。

## 2. 续帧法（首尾帧接力）

**做法**：A 段生成后，抽出最后一帧 → 作为 B 段的首帧参考图（图生视频/首帧图模式）→ B 段提示词写 `continuing seamlessly from the exact provided first frame: ...`。

抽末帧命令（跳过可能的结尾冻结，抽倒数第 0.3 秒）：
```bash
ffmpeg -sseof -0.3 -i segA.mp4 -frames:v 1 -q:v 2 lastframe.jpg
```

**什么时候用续帧法**：
- 同场景强连续内容（一镜到底感、连续演示过程）；
- 人脸出镜且已发生漂移（续帧是最强的"人脸锚"）；
- 动作匹配剪辑的两段（B 段首帧 = A 段动作 1/3 处的帧，注意抽帧位置要在重叠区起点而不是最后一帧）。

**什么时候不用（很重要）**：
- 接缝处按设计要**跨 2 级景别**——续帧法会让 B 段从 A 段的同一画面开始，天然违反景别阶梯，产生"顿一下再动"的感觉。用了续帧就要求 B 段提示词开头立即有摄影机动作（push in/pull back/pan）拉开景别差；
- 换场景的接缝（首帧带着旧场景，模型会"舍不得"离开）；
- 画质衰减链：末帧是压缩后的一帧，连续续 4-5 段会看到画质逐段变糊。**续帧链不要超过 3 段**，第 4 段回到"文字锁 + 原始产品图/人设图"重新起锚。

**续帧 vs 重描 决策表**：

| 接缝情形 | 推荐 |
|---|---|
| 同场景连续动作、演示过程 | 续帧法 |
| 接缝手法=特写桥/遮挡切/进出画/甩镜 | 重描法（文字锁+参考图，不续帧） |
| 人脸漂移返工 | 续帧法或人设图首帧 |
| 换场景 | 重描法 |
| 已连续续帧 3 段 | 强制重描起锚 |

## 3. 固定人设图与三视图

有固定出镜人设时，让用户提供（或先生成）人设参考图：正面/侧面/半身三视图 + 手部特写（美甲、肤色——动作匹配剪辑时手是主角）。每段生成都挂同一组参考图。没有参考图时，第一段生成后立即从中截取最清晰的正面帧充当人设图，供后续段使用。

## 4. 段末状态卡 → 下段首镜头的转换规则

段末状态卡（SKILL.md Phase 3 模板）不是存档，是给下一段的"施工图"。转换规则：

- 状态卡每一项都必须在 B 段首镜头描述里有着落：继承（同景别手法）、回应（视线匹配给出所见）、或合法打断（遮挡切/出画后可以重置）；
- 镜头方向项决定 B 段机位：30 度规则的基准就是状态卡里记录的 A 段末机位；
- 动作项决定重叠区：写清"动作进行到哪一步"，B 段从倒退 1/3 处重新起动作。

## 5. 生成参数纪律

- 全部段用同一模型、同一档位（分辨率/帧率/时长档）生成。中途换模型 = 质感跳变，必然穿帮；
- 同一段允许抽卡多次，但选片标准先看"两端"（首镜头是否符合承接设计、末镜头是否达到段末状态卡），再看中间内容。两端不对，中间再好也不能用；
- 每段生成后立即登记实际可用时长（掐掉结尾冻结帧），拼接前汇总核对总时长。
