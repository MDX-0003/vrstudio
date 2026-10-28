import type { Flow, Gates } from '@/types/case'

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

export interface GenerationParams {
  output: OutputOption[]
  input: InputOption[]
  job: string
  modes: ModeInfo[]
  current: {
    resolution: string
    scale: string
    mode: string
    aspect_ratio: string
    session_id: number
    project_id: number
    episode: number
  }
  fixed: { model_id: number; duration_s: number; num: number }
  saved: boolean
  auto_duration_s?: number
  error?: string
}

export interface OutputFile extends FileInfo {
  file: string
  proxy_url: string
  proxy_state: string
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

export interface StepFiles {
  inputs: FileInfo[]
  outputs: FileInfo[]
}

export interface KeyframePick {
  round: string
  index: number
  asset: string
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
  gates: Gates
  previews: { vp?: string }
  viewport_options: ViewportOption[]
  viewport_footprint: Footprint[]
  prompt: PromptPayload
  generation_params: GenerationParams
  rounds: RoundsPayload
  meta: CaseMeta
  keyframe_hint?: string
}
