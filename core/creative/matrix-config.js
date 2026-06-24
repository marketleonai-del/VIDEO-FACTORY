/**
 * ============================================================================
 * VIDEO-FACTORY :: Matrix Configuration & Data Libraries
 * ============================================================================
 */

// ── 核心配置 ──
const MATRIX_CONFIG = {
  MAX_VARIANTS: 200,
  MIN_VARIANTS: 10,
  OVERLAP_THRESHOLD: 0.75,
  MIN_DIFF_AXES: 2,
  MAX_SAME_HOOK_PER_BATCH: 15,
  QUALITY_GATES: 120,
  PUBLISH_INTERVAL_MIN: 15,
  PUBLISH_WINDOW_HOURS: 72,
  COST: {
    voiceCloneOneTime: 500,
    heroPolish: 800,
    aiHook3s: 2.5,
    narrationSynth: 3.0,
    reEdit: 5.0,
    platformAdapt: 1.5,
    aiWatermark: 0.5,
  },
  AXES: {
    HOOK: 'hook', PERSONA: 'persona', SCENE: 'scene',
    RHYTHM: 'rhythm', CTA: 'cta', BGM: 'bgm',
  },
};

// ── 人设库 ──
const PERSONA_LIBRARY = [
  { id: 'student',    name: '学生党',   tone: '性价比/求推荐',    visual: '宿舍/书桌/校园',      ageRange: '18-24', trustLevel: 72 },
  { id: 'mom',        name: '宝妈',     tone: '安全/省心/实测',   visual: '居家/温馨/育儿',      ageRange: '28-38', trustLevel: 88 },
  { id: 'office',     name: '上班族',   tone: '效率/品质/精致',   visual: '办公桌/通勤/咖啡',    ageRange: '25-35', trustLevel: 78 },
  { id: 'expert',     name: '专业测评', tone: '数据/拆解/对比',   visual: '实验室/工具/仪器',    ageRange: '28-40', trustLevel: 92 },
  { id: 'aesthetic',  name: '精致生活', tone: '仪式感/颜值/氛围', visual: 'ins风/光影/摆件',    ageRange: '22-32', trustLevel: 75 },
  { id: 'renter',     name: '租房党',   tone: '平价/改造/实用',   visual: '出租屋/收纳/好物',    ageRange: '22-30', trustLevel: 70 },
  { id: 'fitness',    name: '健身达人', tone: '功能/效果/自律',   visual: '健身房/户外/运动',    ageRange: '20-35', trustLevel: 82 },
  { id: 'senior',     name: '银发族',   tone: '健康/方便/耐用',   visual: '居家/简约/大字',      ageRange: '55-70', trustLevel: 85 },
  { id: 'foodie',     name: '吃货',     tone: '美味/猎奇/过瘾',   visual: '厨房/餐桌/夜市',      ageRange: '20-40', trustLevel: 76 },
  { id: 'pet',        name: '铲屎官',   tone: '可爱/萌宠/治愈',   visual: '宠物/家居/温馨',      ageRange: '24-35', trustLevel: 80 },
];

// ── 场景库 ──
const SCENARIO_LIBRARY = [
  { id: 'kitchen',    name: '厨房',     mood: '烟火气/实用',     lighting: '暖光/自然光' },
  { id: 'bedroom',    name: '卧室',     mood: '私密/治愈',       lighting: '柔光/氛围灯' },
  { id: 'office',     name: '办公',     mood: '效率/专业',       lighting: '冷白光' },
  { id: 'outdoor',    name: '户外',     mood: '自由/活力',       lighting: '自然光/逆光' },
  { id: 'car',        name: '车内',     mood: '便携/应急',       lighting: '自然光/车内灯' },
  { id: 'bathroom',   name: '浴室',     mood: '清爽/洁净',       lighting: '白光/柔光' },
  { id: 'livingroom', name: '客厅',     mood: '家庭/休闲',       lighting: '暖光' },
  { id: 'cafe',       name: '咖啡馆',   mood: '文艺/慢节奏',     lighting: '暖光/侧光' },
];

// ── 节奏库 ──
const RHYTHM_LIBRARY = [
  { id: 'fastcut',    name: '快剪',     desc: '0.8-1.5s/切',   energy: 95,  bestFor: '冲动消费/年轻人' },
  { id: 'talkshow',   name: '口播',     desc: '2-4s/切',       energy: 65,  bestFor: '信任建立/详细讲' },
  { id: 'story',      name: '剧情',     desc: '叙事节奏',       energy: 70,  bestFor: '情感共鸣/代入' },
  { id: 'asmr',       name: 'ASMR',     desc: '慢速沉浸',       energy: 40,  bestFor: '感官体验/助眠类' },
  { id: 'montage',    name: '蒙太奇',   desc: '画面堆叠',       energy: 85,  bestFor: '视觉冲击/氛围' },
  { id: 'tutorial',   name: '教程',     desc: '步骤演示',       energy: 55,  bestFor: '功能型产品' },
];

// ── 平台预设 ──
const PLATFORM_PRESETS = {
  douyin: {
    name: '抖音', aspect: '9:16', hookStyle: '冲突/悬念/反差', pace: 'fast',
    ctaType: '小黄车', maxDuration: 60,
    bestPublishTimes: ['07:30','12:00','18:00','21:30'],
    captionStyle: '大字幕/居中/emoji', filter: '美颜/清晰',
    musicStrategy: '热门BGM/卡点', features: ['小黄车','商品橱窗','DOU+'],
  },
  kuaishou: {
    name: '快手', aspect: '9:16', hookStyle: '信任/真实/接地气', pace: 'medium-fast',
    ctaType: '直播预约/小黄车', maxDuration: 120,
    bestPublishTimes: ['06:00','11:30','17:00','20:00'],
    captionStyle: '朴实/口语化', filter: '原生/少美颜',
    musicStrategy: '接地气的BGM', features: ['小黄车','直播','磁力金牛'],
  },
  tiktok: {
    name: 'TikTok', aspect: '9:16', hookStyle: '视觉冲击/卡点/英文', pace: 'fast',
    ctaType: 'Shop链接', maxDuration: 60,
    bestPublishTimes: ['08:00','12:30','19:00','22:00'],
    captionStyle: '英文/大字体/动态', filter: 'Trendy/鲜艳',
    musicStrategy: 'Trending Audio', features: ['TikTok Shop','Spark Ads'],
  },
  xiaohongshu: {
    name: '小红书', aspect: '4:3', hookStyle: '高颜值/氛围/种草', pace: 'medium',
    ctaType: '评论区引导', maxDuration: 300,
    bestPublishTimes: ['08:00','12:00','20:00'],
    captionStyle: '精致/排版/符号', filter: '胶片/ins风',
    musicStrategy: '轻柔BGM', features: ['笔记','商品卡片','薯条'],
  },
  shipinhao: {
    name: '视频号', aspect: '9:16', hookStyle: '社交货币/标题党', pace: 'medium',
    ctaType: '公众号/小店', maxDuration: 60,
    bestPublishTimes: ['07:00','12:00','18:30','21:00'],
    captionStyle: '标题党/引导转发', filter: '自然/清晰',
    musicStrategy: '流行BGM', features: ['微信小店','直播','裂变'],
  },
};

// ── CTA模板库 ──
const CTA_LIBRARY = {
  douyin:      ['链接在左下角，点进去有惊喜价','小黄车里同款，趁现在还有库存','评论区置顶链接，手慢无','现在下单比双11还便宜','厂家直发，点链接抢现货','限时福利，点链接领券','今天拍今天发，不等预售','厂家补贴，点链接看价格','左下角同款，仅剩最后几十件','库存告急，点进去抢'],
  kuaishou:    ['直播间里给你们炸福利','信老铁的，点链接试试','厂家直供，不满意包退','今天拍今天发，不等预售','老铁们，链接在评论区','厂家直发，没有中间商','信我一次，点进去看看','福利价只在今天，点链接'],
  tiktok:      ['Link in bio, grab yours now','Shop now before sold out','Use my code for 20% off','Limited stock, click below','Get yours via link in bio','Flash sale, click now','Free shipping, link below','Grab it before it\'s gone','Use code SAVE20 at checkout','Link in profile, shop now'],
  xiaohongshu: ['链接在评论区，姐妹们冲','戳左下角同款好物','关注我不迷路，持续更新','私信我发链接给你','左下角同款，亲测好用','评论区有链接，自取','同款在左下角，点进去','姐妹们冲，链接在评论'],
  shipinhao:   ['点击下方链接了解更多','转发给需要的朋友','关注我看更多实测','进小店有更多优惠','点击链接，惊喜不断','喜欢的朋友点进来看看','评论区链接，欢迎选购','关注+转发，福利不停'],
};

// ── BGM标签库 ──
const BGM_TAGS = ['热门卡点','治愈钢琴','电子节奏','悬疑紧张','温馨日常','励志鼓点','国风古乐','蒸汽波','Lo-Fi','对口型热门','轻快节奏','情感慢歌','搞笑音效','电影感','白噪音'];

// ── 字幕样式库 ──
const CAPTION_STYLES = [
  { name: '居中粗体', position: 'center', font: 'bold', color: '#FFFFFF', stroke: '#000000' },
  { name: '底部条',   position: 'bottom', font: 'medium', color: '#FFFFFF', bg: 'rgba(0,0,0,0.6)' },
  { name: '左侧竖排', position: 'left', font: 'bold', color: '#FFE066', stroke: '#000000' },
  { name: '弹幕风',   position: 'float', font: 'medium', color: '#FFFFFF', animation: 'float' },
  { name: '渐变大字', position: 'center', font: 'large', color: 'gradient', stroke: '#000000' },
  { name: '简约细体', position: 'bottom', font: 'light', color: '#E8E8E8', bg: 'transparent' },
];

module.exports = {
  MATRIX_CONFIG, PERSONA_LIBRARY, SCENARIO_LIBRARY,
  RHYTHM_LIBRARY, PLATFORM_PRESETS, CTA_LIBRARY,
  BGM_TAGS, CAPTION_STYLES,
};
