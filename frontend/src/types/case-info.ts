import type { Flow } from '@/types/case'

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

export interface OutputFile {
  file: string
  url: string
  proxy_url: string
  proxy_state: string
  version?: string
  partial?: boolean
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
}

export interface RoundsPayload {
  rounds: RoundSummary[]
  hint: string
}

export interface CaseInfo {
  name: string
  flow: Flow
  assets: AssetDetail[]
  submit_selected: string[]
  prompt: PromptPayload
  generation_params: GenerationParams
  outputs: OutputFile[]
  rounds: RoundsPayload
  gates: Record<string, { ok: boolean; reason?: string } | undefined>
}
