import type { AssetRow, CandidateRound, CaseRow, Flow, PromptVariable } from '@/types/case'

export const flowMeta: Record<Flow, { label: string; tag: 'primary' | 'success' | 'warning' }> = {
  patch: { label: '局部重绘', tag: 'primary' },
  fullredraw: { label: '全流程重绘', tag: 'success' },
  gen: { label: '简单生成', tag: 'warning' },
}

export const mockCases: CaseRow[] = [
  {
    name: 'dacne',
    flow: 'patch',
    owner: 'MDX',
    updatedAt: '2026-10-23 16:42',
    taskStatus: 'running',
    taskLabel: '空间合并 · 进行中',
    outputs: 3,
  },
  {
    name: 'walk_4k',
    flow: 'patch',
    owner: 'MDX',
    updatedAt: '2026-10-23 15:08',
    taskStatus: 'done',
    taskLabel: '首尾帧 · 已完成',
    outputs: 5,
  },
  {
    name: 'walk_02',
    flow: 'patch',
    owner: 'LQ',
    updatedAt: '2026-10-23 14:20',
    taskStatus: 'queued',
    taskLabel: '抽取 · 排队第 2 位',
    outputs: 2,
  },
  {
    name: 'ship_01',
    flow: 'fullredraw',
    owner: 'MDX',
    updatedAt: '2026-10-22 19:35',
    taskStatus: 'failed',
    taskLabel: '提交生成 · 额度不足',
    outputs: 1,
  },
  {
    name: 'simple_gen_01',
    flow: 'gen',
    owner: 'WY',
    updatedAt: '2026-10-22 11:02',
    taskStatus: 'idle',
    taskLabel: '等待下一步',
    outputs: 4,
  },
  {
    name: 'web_demo',
    flow: 'patch',
    owner: 'MDX',
    updatedAt: '2026-10-21 17:50',
    taskStatus: 'idle',
    taskLabel: '等待下一步',
    outputs: 0,
  },
]

export const mockAssets: AssetRow[] = [
  {
    id: 'v1',
    name: '@Video1 · clip.mp4',
    kind: 'video',
    role: '编辑母版',
    meta: '1920×1080 · 9.83 秒 · 60fps',
    selected: true,
    tone: 'linear-gradient(135deg, #1e3a8a, #3b82f6)',
  },
  {
    id: 'i1',
    name: '@Image1 · i1_frame.png',
    kind: 'image',
    role: '主体参考',
    meta: '1620×1080 · 首帧定稿',
    selected: true,
    tone: 'linear-gradient(135deg, #7c2d12, #f59e0b)',
  },
  {
    id: 'i2',
    name: '@Image2 · i2_sheet.png',
    kind: 'image',
    role: '主体参考',
    meta: '1080×1920 · 人物三视图',
    selected: false,
    tone: 'linear-gradient(135deg, #134e4a, #14b8a6)',
  },
  {
    id: 'i3',
    name: '@Image3 · last_frame_raw.png',
    kind: 'image',
    role: '场景参考',
    meta: '1920×1080 · 尾帧原始图',
    selected: false,
    tone: 'linear-gradient(135deg, #4c1d95, #8b5cf6)',
  },
]

export const mockPromptVariables: PromptVariable[] = [
  {
    key: 'subject',
    label: '原主体',
    help: '写画面里要被替换的主体；模型据此判断改哪个对象。',
    value: '画面中央跳舞的白色素体人偶（表面光滑的 CG 白模，身体带深色接缝线，无面部特征）',
  },
  {
    key: 'target',
    label: '重绘结果',
    help: '写替换后的外观与风格；决定成片里主体最终长什么样。',
    value: '与 @Image1 一致的真实人物；只取脸型、发型、体型、服装、配饰与配色，不取参考图的背景、姿势与构图。',
  },
  {
    key: 'plot',
    label: '情节',
    help: '按时间顺序写原片的动作与情节；模型据此保持动作顺序与节奏。',
    value: '约 10 秒的独舞（60fps，共 590 帧）。古庙废墟场景中，人偶在画面中央的空地上起舞：0–2 秒从直立站定开始向前俯身，一手抬至面前；2–5 秒双臂向两侧展开，上身向一侧倾斜，重心横移；5–8 秒转身侧对镜头，双臂收于胸前，向画面右侧撤步并以大跨步展开双臂；8–10 秒收回画面中央，单臂高举过头后落下，最终回到直立站定。',
  },
  {
    key: 'action',
    label: '动作复现规则',
    help: '写哪些动作要素必须复现，哪些允许在同一时间窗内重新设计。',
    value: '保持动作顺序、行进方向、画面位置、起止时间与节奏与人偶一致；允许在同一时间窗内重新设计关节角度、重心路径与身体姿态，让动作在新身体上自然流畅。',
  },
  {
    key: 'preserve',
    label: '必须保持',
    help: '写场景里不能变化的部分；用来防止模型顺手改背景。',
    value: '古庙废墟场景与其中的固定元素：金色兽像和雕花石祭坛、左侧残破石阶、四周巨大树根与石墙、地面石板与散落碎石；从顶部洒下的暖金色顶光、光柱与浮尘；整个场景的构图、光照方向与暖黄色调；镜头位置与焦距固定不变。',
  },
  {
    key: 'allowed_changes',
    label: '允许变化',
    help: '只列允许改变的内容；未列出的主体、数量、外观、位置、时间与镜头全部保持原样。',
    value: '1. 主体：画面中央的白色素体人偶；变化：整体替换为与 @Image1 外观一致的真实人物；时间：0–10 秒全片；幅度：仅替换人偶本身，其在画面中的位置、大小与占比保持不变。',
  },
]

export const mockCandidates: CandidateRound[] = [
  { id: 'r1', title: '首帧批次 · 4 张', time: '16:12', status: 'picked', images: 4 },
  { id: 'r2', title: '尾帧批次 · 4 张', time: '16:18', status: 'ready', images: 4 },
  { id: 'r3', title: '首尾帧视频 · 预演', time: '16:31', status: 'ready', images: 1 },
]
