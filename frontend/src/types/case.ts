export type Flow = 'patch' | 'fullredraw' | 'gen'

export type TaskStatus = 'idle' | 'queued' | 'running' | 'done' | 'failed'

export type AssetKind = 'video' | 'image'

export interface CaseRow {
  name: string
  flow: Flow
  owner: string
  updatedAt: string
  taskStatus: TaskStatus
  taskLabel: string
  nextStepKey: string
  outputs: number
}

export interface Gate {
  ok: boolean
  ready?: boolean
  reason?: string
  evidence?: string
}

export type Gates = Record<string, Gate | undefined>

export interface CaseBrief {
  name: string
  flow: Flow
  gates: Gates
  updated_at: string | null
  outputs: number
  owner: string | null
  task_status: string | null
}

export interface SourcesResponse {
  dir: string
  files: string[]
}

export interface ModeOption {
  value: string
  label?: string
}

export interface ModesResponse {
  modes: ModeOption[]
}

export interface CreateCasePayload {
  flow: Flow
  name?: string
  video?: string
  /** 分片上传完成返回的 file_id；与 video 二选一。 */
  upload_id?: string
  mode?: string
}

export interface AssetRow {
  id: string
  name: string
  kind: AssetKind
  role: string
  roleRaw?: string
  roleFixed?: boolean
  meta: string
  selected: boolean
  tone: string
  url?: string
  exists?: boolean
  rel?: string | null
  path?: string
  open_url?: string | null
}

export interface PromptVariable {
  key: string
  label: string
  help: string
  value: string
}
