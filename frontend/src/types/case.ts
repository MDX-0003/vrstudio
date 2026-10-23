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
  outputs: number
}

export interface AssetRow {
  id: string
  name: string
  kind: AssetKind
  role: string
  meta: string
  selected: boolean
  tone: string
}

export interface PromptVariable {
  key: string
  label: string
  help: string
  value: string
}

export interface CandidateRound {
  id: string
  title: string
  time: string
  status: 'ready' | 'picked' | 'failed'
  images: number
}
