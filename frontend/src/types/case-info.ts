import type { Flow, Gates } from '@/types/case'
import type { Pole } from '@/utils/erpMath'

export interface AssetDetail {
  id: string
  kind: 'video' | 'image'
  role: string
  label: string
  path: string
  source: string
  role_fixed: boolean
  url: string
  name: string
  rel: string | null
  exists: boolean
  mtime: number | null
  size: number | null
  width: number | null
  height: number | null
  duration: number | null
  fps: number | null
  open_url?: string | null
}

export interface PromptPayload {
  template: string
  available: string[]
  template_text: string
  placeholders: string[]
  geometry_keys: string[]
  computed_keys: string[]
  labels: Record<string, string>
  help: Record<string, string>
  vars: Record<string, string>
  rendered: string
  render_error: string | null
  error?: string
}

export interface OutputOption {
  value: string
  width: number
  height: number
}

export interface InputOption {
  value: string
  width: number
  height: number
  ok: boolean
  reason: string
}

export interface ModeInfo {
  value: string
  label: string
  takes_video: boolean
  ratios: string[]
  max_count: number
  max_videos: number
  max_images: number
  video_required: boolean
  duration: { type: string; min?: number; max?: number }
}

export interface ModelOption {
  id: number
  name: string
  description: string
}

export interface GenerationParams {
  output: OutputOption[]
  input: InputOption[]
  job: string
  modes: ModeInfo[]
  models: ModelOption[]
  image_models: ModelOption[]
  image_models_error?: string | null
  current: {
    resolution: string
    scale: string
    mode: string
    aspect_ratio: string
    session_id: number
    project_id: number
    episode: number
  }
  fixed: {
    video_model_id: number
    image_model_id: number
    duration_s: number
    num: number
  }
  saved: boolean
  auto_duration_s?: number
  error?: string
}

export interface OutputFile extends FileInfo {
  file: string
  /** 视频交付物才有代理；图片流程的 PNG 成片不带这两个字段。 */
  proxy_url?: string
  proxy_state?: string
  proxy_width?: number | null
  version?: string
  partial?: boolean
}

export interface RoundFiles {
  result: FileInfo
  run_json: FileInfo
}

export interface RoundSummary {
  mark: string
  round: string
  backend: string
  stage: string
  gate: string
  result: string
  updated: string
  round_dir: string
  files?: RoundFiles
}

export interface RoundsPayload {
  rounds: RoundSummary[]
  hint: string
}

export interface Viewport {
  yaw_deg: number
  pitch_deg: number
  fov_h_deg: number
  width: number
  height: number
}

export interface Inner {
  x: number
  y: number
  width: number
  height: number
}

export interface FrameRange {
  start: number
  end: number
}

/** 选区范围的剪贴板负载（决策 92）：patch 选区页的导出/导入共用这一份格式。 */
export interface RegionPayload {
  kind: string
  version: number
  case: string
  exported_at: string
  viewport: Viewport
  inner: Inner
  frames: FrameRange
}

export interface ViewportOption {
  label: string
  width: number
  height: number
  aspect: string
}

export interface Footprint {
  x: number
  y: number
  w: number
  h: number
}

export interface Coverage {
  fov_deg: number
  footprint: [number, number]
  inner_deg: number
  density: { '1080p': number; '4K': number }
}

export interface FileInfo {
  name: string
  path: string
  rel: string | null
  exists: boolean
  mtime: number | null
  size: number | null
  open_url?: string | null
}

export interface ExtractVersion {
  version: string
  extracted: boolean
  has_result: boolean
}

/** 图片轮次里的一张候选；index 就是 adopt 请求要带的下标（从 1 开始）。 */
export interface ImageCandidate {
  index: number
  name: string
  exists: boolean
  url: string
}

/** 图片流程的生成轮次（最新在前）；adopted 表示这一轮被采用。 */
export interface ImageRound {
  round: string
  stage?: string
  gate?: string
  adopted: boolean
  results: ImageCandidate[]
}

/** patchimg 的贴回报告。 */
export interface PatchImageReport {
  erp: [number, number]
  viewport: Record<string, unknown>
  inner: Record<string, unknown>
  normalize: { from: [number, number]; to: [number, number]; allow_crop: boolean }
  local: { support_pixels: number; changed_inside: number; maxdiff_outside: number }
  output: string
}

/** fullRedrawImg 的拼接报告。 */
export interface FullRedrawImageReport {
  source: [number, number]
  canvas: [number, number]
  /** quality 是“次优比最优”的比值，逐位相同时为无穷，后端落盘/下发前转成 null。 */
  registration: {
    d_star: number
    correlation: number
    quality: number | null
    mae_min: number
  }
  gate: string
  seam: Record<string, unknown>
  verdict: string
  reasons: string[]
  aspect_drift_pct: number
  output: string
}

export type ImageReport = PatchImageReport | FullRedrawImageReport

/** 素材的只读媒体元数据；读不到时为 null。 */
export interface MediaMeta {
  width: number | null
  height: number | null
  duration: number | null
  fps: number | null
}

/** relight 的一个槽位：没填时为 null。 */
export interface RelightSlot {
  asset: string
  kind: 'video' | 'image'
  path: string
  sha256: string | null
  size: number | null
  meta: MediaMeta | null
  entry: FileInfo
}

/** relight 面板数据：两个槽位、草稿参数、当前成片与小样。 */
export interface RelightPayload {
  gt: RelightSlot | null
  atmo: RelightSlot | null
  /** 底版的媒体类型；两个槽位都空时为 null。 */
  media: 'video' | 'image' | null
  draft: {
    current?: string
    scene?: string
    strength?: number
    local_shading?: number
    highlight_clip?: boolean
    clip_knee?: number
    clip_max?: number
    ref_frame?: number
  }
  current: FileInfo | null
  current_report: FileInfo | null
  samples: FileInfo[]
  /** 最近一次小样的报告 JSON；老小样没有报告时为 null。 */
  sample_report: FileInfo | null
  /** 区域覆盖定义；没有区域或文件坏掉时为 null（坏掉时 regions_error 给出原因）。 */
  regions: RelightRegionDoc | null
  regions_error: string | null
}

/** 一个生效区间，单位秒，左闭右开（决策 91）。 */
export interface TimeRange {
  start: number
  end: number
}

/** 区域覆盖的一个区域（决策 88）。`params` 里 null 表示沿用默认值。 */
export interface RelightRegion {
  id: string
  name: string
  color: string
  enabled: boolean
  verts: Array<[number, number]>
  /** 非空表示含该极点的盖帽；缺省或 null 为普通闭合多边形（决策 89）。 */
  pole?: Pole | null
  feather_deg: number
  params: {
    strength: number | null
    local_shading: number | null
    clip: { knee: number; max: number } | null
  }
  /** null = 整段生效；非空为解析端并集后的区间列表（决策 91）。 */
  ranges: TimeRange[] | null
}

export interface RelightRegionDoc {
  version: 1
  regions: RelightRegion[]
}

export interface StepFiles {
  inputs: FileInfo[]
  outputs: FileInfo[]
  /** fullredraw 扩充步的第二组文件：按平台限额缩放后的上传件（决策 87）。 */
  uploads?: FileInfo[]
}

/** 上传件的参数摘要，来自 `derived/upload_pad_<W>x<H>.json`（vrgen prep 写）。 */
export interface PadUploadInfo {
  file: FileInfo
  canvas: [number, number] | null
  source: [number, number] | null
  upload: { path: string; width: number; height: number; pixels: number } | null
  mask: { path: string; width: number; height: number } | null
  scaled: boolean | null
  scale: string | null
  scale_percent: number | null
  limit: { max_pixel: number; max_bytes: number } | null
  video_model_id: number | null
  mode: string | null
  created_utc: string | null
}

export interface KeyframePick {
  round: string
  index: number
  asset: string
  /** raw = 直接用当前抽取的原始帧定稿（未重绘）；generated = 从候选里挑的。 */
  source?: 'raw' | 'generated'
  file?: FileInfo | null
}

export interface KeyframeRef {
  asset: string
  role: 'subject_ref' | 'detail_ref'
  note: string
  label: string
  url: string | null
  exists: boolean
  file: FileInfo | null
}

export interface KeyframeState {
  enabled: boolean
  error: string | null
  refs: KeyframeRef[]
  refs_default: boolean
  role_help: Record<string, string>
  first: KeyframePick | null
  last: KeyframePick | null
  extract: string | null
  stale: boolean
  complete: boolean
}

export interface RawFrame {
  id: string
  role: string
  label: string
  name: string
  path: string
  rel: string | null
  exists: boolean
  mtime: number | null
  width: number | null
  height: number | null
  size: number | null
  url: string
  open_url?: string | null
}

export interface CaseMeta {
  owner_id: string | null
  owner_name: string | null
  created_at: string | null
  updated_at: string | null
  updated_by_id: string | null
  updated_by_name: string | null
  revision: number
}

export interface RoleSpec {
  name: string
  label: string
  help: string
  kinds: string[] | null
  modes: string[]
  open: boolean
  reserved: boolean
}

export type OutputMode = 'case' | 'input'

export interface OutputOption {
  mode: OutputMode
  width: number
  height: number
}

export interface CaseInfo {
  name: string
  flow: Flow
  source_url: string
  source_path: string | null
  source_file: FileInfo | null
  viewport: Viewport | null
  inner: Inner | null
  frames: FrameRange | null
  frame_count: number | null
  fps: number | null
  duration_s: number | null
  proxy: { state: string; url: string; width?: number | null } | null
  extract_current: string | null
  adopt_round: string | null
  /** 图片流程采用的候选序号；视频流程为 null。 */
  adopt_index?: number | null
  /** fullRedrawImg 的拼接输入素材 id；null = 用生成采用的结果。 */
  merge_input?: string | null
  /** 本次可选的输出尺寸：案例分辨率，以及按拼接输入自身尺寸派生的尺寸（可能更大也可能更小）。 */
  output_options?: OutputOption[]
  /** 图片流程的生成轮次（最新在前）；视频流程为空数组。 */
  image_rounds?: ImageRound[]
  /** 图片流程 merge 的 report.json；还没出片时为 null。 */
  image_report?: ImageReport | null
  /** relight 的槽位、草稿与当前成片；其他流程为 null。 */
  relight?: RelightPayload | null
  assets: AssetDetail[]
  raw_frames: RawFrame[]
  keyframe: KeyframeState
  submit_selected: string[]
  submit_roles: Record<string, string> | null
  submit_entries: { asset: string; role: string }[]
  role_help: Record<string, string>
  roles: RoleSpec[]
  derived: FileInfo[]
  extracts: ExtractVersion[]
  outputs: OutputFile[]
  step_files: Record<string, StepFiles>
  /** fullredraw 最新一组上传件的摘要；还没导出时为 null。 */
  pad_upload?: PadUploadInfo | null
  gates: Gates
  previews: { vp?: string }
  viewport_options: ViewportOption[]
  viewport_footprint: Footprint[]
  coverage: Coverage | null
  fov_range: [number, number]
  prompt: PromptPayload
  generation_params: GenerationParams
  rounds: RoundsPayload
  meta: CaseMeta
  keyframe_hint?: string
}
