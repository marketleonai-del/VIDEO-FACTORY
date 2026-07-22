# 电影级转场手法库（拼接点设计）

每个拼接点从下面选一种手法。条目格式统一：原理 → 什么时候用 → A 段末镜头怎么写 → B 段首镜头怎么写 → 带货示例 → 风险与对策。

写提示词时的通用要求：A 段末和 B 段首的描述要**互为镜像**——A 的段末状态卡里写了什么（姿态/视线/道具位置/光线/镜头方向），B 的首镜头就必须逐项回应什么。

## 目录

1. 动作匹配剪辑（Match on Action）——最强隐形剪辑
2. 特写桥（Insert Bridge）——带货最实用
3. 遮挡切（Blocking Wipe）
4. 视线匹配（Eyeline Match）
5. 进出画（Exit / Enter Frame）
6. 甩镜转场（Whip Pan）
7. 图形匹配（Graphic Match）
8. 景别阶梯与 30 度规则（兜底规则，必读）
9. J-cut / L-cut（音频先行/延后）
10. 节奏切（Beat Cut）
11. 同动作蒙太奇（Rhythmic Montage）
12. 光色桥（Color Bridge，后期配合）

---

## 1. 动作匹配剪辑（Match on Action）

**原理**：人眼追踪运动时会忽略剪辑点。让一个动作跨越接缝——A 段里动作开始，B 段里同一动作完成——观众的注意力在动作上，剪辑就"消失"了。这是电影剪辑里最基础也最强的隐形手法。

**什么时候用**：任何有清晰动作的接缝。拿起产品、开盖、按下开关、递向镜头、转身、坐下。

**关键操作——重叠拍两遍**：不要让 A 段刚好停在动作中点、B 段刚好从中点开始（AI 生成对不齐）。正确做法：
- A 段末：生成动作的前 2/3（如：手伸向产品并握住、开始抬起）
- B 段首：从动作的 1/3 处重新开始生成到完成（如：手握着产品抬起、举到胸前展示）
- 两段重叠约 1 秒，后期在动作最快的瞬间（运动模糊最大处）切开，各丢弃重叠的一半。运动模糊会掩盖两段之间的细微差异。

**A 段末写法**：`Her hand reaches toward the [product] on the counter and grips it, starting to lift — motion continues as the clip ends`（段末状态卡记录：手已握住产品、开始上抬、肘部角度）

**B 段首写法**：`Continuing the same motion: her hand, already gripping the [product], lifts it up to chest level and presents it toward the camera`（换景别！A 是中景，B 就用特写）

**带货示例**：段2末：宝妈的手伸向料理机按钮按下（俯拍中景）→ 段3首：手指按下按钮的特写，机器启动，食材开始搅动。

**风险与对策**：两段手部细节（美甲、袖口）不一致会穿帮 → 一致性锁里必须锁定手部特征；切点选在运动最快帧，模糊掩差。

## 2. 特写桥（Insert Bridge）——带货首选

**原理**：产品特写/手部特写里没有背景、没有人脸——跨段最容易漂移的两样东西都不在画面里。用一个特写镜头做"桥"，桥的两端接什么都不穿帮。

**什么时候用**：跨段误差风险高的接缝（换场景、人物动作复杂）；不知道用什么手法时的安全牌；需要突出产品细节的位置（本来就该给特写）。

**A 段末写法**：让镜头推向产品或手部，段末停在特写：`camera pushes in to an extreme close-up of the [product], filling the frame, shallow depth of field, background completely blurred`

**B 段首写法**：从同一特写的**另一个角度**开始，再拉开进入新内容：`extreme close-up of the [product] from a slightly different angle, camera slowly pulls back to reveal [新场景/人物]`

**带货示例**：段3末推进到精华液瓶口滴管特写 → 段4首从滴管 45 度侧面特写开始，拉开是手背试用场景。

**风险与对策**：产品本身跨段变形 → 产品锁定串（exact shape, colors, materials, ZERO morphing）必须在两段提示词里逐字一致；两个特写角度差要超过 30 度（见手法 8）。

## 3. 遮挡切（Blocking Wipe）

**原理**：前景物体（手、身体、门、路人、布料）扫过镜头，画面被短暂遮满的那几帧就是天然剪辑点——观众以为是同一镜头里的遮挡。

**什么时候用**：换场景的接缝（家里→超市）；人物走动中的接缝；需要"一镜到底感"时。

**A 段末写法**：`as she turns, her shoulder sweeps across and briefly fills the frame, blocking the camera completely for a moment`（或：手拿产品从镜头前扫过 / 拉开的衣物掠过镜头）

**B 段首写法**：`the frame starts fully blocked by a dark blur (shoulder passing), then clears to reveal [新场景]`——B 段第 0 帧必须也是被遮挡状态，后期在全遮帧对切。

**带货示例**：段4末：模特转身衣摆扫过镜头全黑一瞬 → 段5首：从暗到亮拉开，已在户外场景穿着同一件衣服。

**风险与对策**：AI 可能不给足"全遮帧" → 提示词里明确 `completely blocking/filling the frame`；后期若没全遮，用 2 帧黑场补救（ffmpeg.md）。

## 4. 视线匹配（Eyeline Match）

**原理**：A 段人物看向画外某处，观众本能想知道"在看什么"，B 段给出所看之物。观众的好奇心把两段缝在一起——这也是天然的"卖点引出"结构。

**什么时候用**：人物段 → 产品段的接缝；引出对比对象、引出效果展示。

**A 段末写法**：`she pauses and looks down to her left, eyes fixed on something off-screen, slight smile of surprise`（段末状态卡记录视线方向！）

**B 段首写法**：B 段首镜头 = 她视线方向看到的东西，机位模拟她的视点：`POV shot angled down-left: the [product] sitting on the kitchen counter in warm morning light`

**带货示例**：段1末：主妇疲惫地抬头看向右上方货架 → 段2首：货架上的产品（她的视点），手入画拿取。

**风险与对策**：视线方向和 B 段机位方向必须一致（她往左看，B 段就是左侧视点），方向搞反观众会"晕一下"。

## 5. 进出画（Exit / Enter Frame）

**原理**：主体走出画面后，画面剩下 0.5 秒空镜；空镜没有需要对齐的主体，是天然剪辑点。B 段从另一处空镜开始，主体从同方向入画。

**什么时候用**：场景切换；时间跳跃（"第二天"）；节奏需要喘口气的位置。

**A 段末写法**：`she walks out of the frame to the right, leaving an empty shot of the kitchen for a brief moment`

**B 段首写法**：`empty shot of the living room, she enters the frame from the left carrying the [product]`——出画向右、入画从左，方向连贯（银幕方向一致性）。

**带货示例**：段2末：拿着吸尘器走出画右 → 段3首：客厅空镜，从画左入画开始清洁演示。

**风险与对策**：空镜别留太长（超过 1 秒节奏就垮）；出入画方向必须相反侧（右出→左入），同侧出入会像"弹回来"。

## 6. 甩镜转场（Whip Pan）

**原理**：镜头快速横甩产生运动模糊，模糊帧里什么都看不清——在模糊中切换，两段任何差异都被掩盖。

**什么时候用**：高能量内容（促销段、多场景快切）；风格年轻化的带货视频。慎用于慢节奏内容。

**A 段末写法**：`at the end, the camera whips rapidly to the right, image dissolving into horizontal motion blur`

**B 段首写法**：`the clip starts mid whip-pan (horizontal motion blur moving right), then the camera abruptly settles on [新场景]`——两段甩的**方向必须相同**（都向右），后期在最模糊帧对切。

**带货示例**：段5末：镜头从产品甩向右 → 段6首：甩入定格在促销场景（主播指向购物车）。

**风险与对策**：AI 对 whip pan 的执行不稳定，可能甩一半就停 → 生成 2-3 次挑最模糊的；实在不行后期用 xfade 的 `slideleft` 0.15 秒模拟（ffmpeg.md）。

## 7. 图形匹配（Graphic Match）

**原理**：A 段末与 B 段首的画面构图/形状/颜色相似（圆接圆、线接线），视觉系统把两个画面理解为"同一件事的延续"。《2001 太空漫游》骨头接飞船就是这个手法。

**什么时候用**：换场景但想保持"高级感"的接缝；产品形状有特点时（圆瓶、长条、环形）。

**A 段末写法**：`final frame composition: the round [product cap] centered in frame, filling about one third of the picture`

**B 段首写法**：`opening composition mirrors the previous shot: a round coffee cup centered in frame at the same scale, then camera moves to reveal [新内容]`

**带货示例**：段3末：圆形粉饼盒居中特写 → 段4首：同构图的圆镜子居中，拉开是化妆场景。

**风险与对策**：需要两段构图精确呼应，AI 达成率中等 → 把构图写死（centered, filling 1/3 of frame），不行就退回特写桥。

## 8. 景别阶梯与 30 度规则（兜底规则，必读）

**原理**：这不是一种"手法"，是所有拼接点的底线。电影剪辑的 30 度规则：相邻镜头如果机位变化小于 30 度、景别相同，观众会感知为"画面跳了一下"（跳切）。AI 分段生成天然容易犯这个错——每段都从相似的中景开始。

**执行**：
- 景别五级：远景 → 全景 → 中景 → 近景 → 特写。相邻两段接缝处**至少跨 2 级**（中景→特写 ✓，中景→近景 ✗）。
- 机位方向变化 > 30 度，或者干脆换轴线侧（注意不要越轴：人物朝向别左右颠倒）。
- 检查方式：把每段的首/末镜头景别列成表，扫一遍相邻对。

**带货示例（60 秒 6 段的景别设计）**：段1末中景 → 段2首特写 → 段2末全景 → 段3首近景 → 段3末特写 → 段4首中景……锯齿形交替。

## 9. J-cut / L-cut（音频先行/延后）

**原理**：耳朵对连续性的判断优先于眼睛。J-cut：B 段的声音在 A 段画面还没结束时就进来；L-cut：A 段的声音延续到 B 段画面里。声音跨过接缝，观众就认定"这是连续的"。

**什么时候用**：**每一个接缝都用**——这是后期手法，零生成成本。有口播的视频尤其重要。

**口播文案设计**：把句子写成**跨段句**——一句话的前半在 A 段说完，后半落在 B 段。例：A 段末口播"而且它最厉害的是……"（画面切换）B 段首"……只要一分钟就能完成"。观众追着句子走，根本不看接缝。

**实现**：口播和 BGM 单独成轨，不用各段视频自带的音频。剪辑时音频轨跨接缝铺，画面切点和声音切点错开 0.3-0.8 秒。FFmpeg 实现见 ffmpeg.md 的音频部分。

## 10. 节奏切（Beat Cut）

**原理**：画面切点卡在 BGM 鼓点/重音上，音乐的节奏感"解释"了切换——观众预期在节拍处看到变化。

**什么时候用**：促销段、蒙太奇段、多产品展示。和任何视觉手法可叠加。

**实现**：选 BGM 后确定 BPM，把拼接点对齐到节拍网格（ffmpeg.md 给了按拍切齐的命令思路）。设计分镜时就按节拍安排段长（如 BPM 120 → 每拍 0.5s → 段长设计成拍数的整数倍）。

## 11. 同动作蒙太奇（Rhythmic Montage）

**原理**：多个场景/人物做同一个动作连续快切（不同人打开同一款产品、同一产品在三个场景使用）。此时"切换"本身就是内容风格，拼接感被节奏合法化。

**什么时候用**：信任建立段（多人都在用）、场景覆盖段（厨房/办公室/车里都能用）。适合把 3-4 个 5 秒短段消化成一个"段落"。

**写法**：每个短段用完全相同的动作描述和构图描述，只换场景与人物：`the same action: hands opening the [product] lid, centered composition — setting: office desk / car interior / gym locker`。

**风险与对策**：产品一致性压力大（多段同框产品）→ 产品锁定串逐字复制到每段。

## 12. 光色桥（Color Bridge，后期配合）

**原理**：跨段光线/色调统一后，即使内容有切换，观众也觉得"是同一部片子"。反过来，色温跳变哪怕内容完美衔接也会露馅。

**生成端**：所有段的提示词共享同一个光线锁（时间/光源方向/色温描述逐字一致，见 continuity.md）。

**后期端**：拼接前统一色彩（ffmpeg.md 的色彩匹配部分）；接缝处可加 3-5 帧（0.12-0.2 秒）微溶解 xfade——时长低于肉眼察觉阈值，但能把残余的光色差"揉"过去。**微溶解不要超过 0.25 秒**，否则会显出"叠影"，变成明显的转场效果而不是隐形缝合。

---

## 手法选择速查

| 接缝情形 | 首选 | 备选 |
|---|---|---|
| 同场景、有动作 | 1 动作匹配 | 2 特写桥 |
| 换场景 | 3 遮挡切 / 5 进出画 | 7 图形匹配 |
| 人物段→产品段 | 4 视线匹配 | 2 特写桥 |
| 高风险接缝（怕穿帮） | 2 特写桥 | 12 光色桥加厚 |
| 促销/高能段 | 6 甩镜 / 10 节奏切 | 11 蒙太奇 |
| 所有接缝叠加 | 8 景别阶梯（底线）+ 9 J/L-cut（免费）+ 12 光色桥 | — |
