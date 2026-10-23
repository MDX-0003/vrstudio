export type TaskState = 'queued' | 'running' | 'done' | 'failed'

export interface TaskView {
  id: string
  kind: string
  case: string
  owner_id: string | null
  owner_name: string | null
  project_id: number | null
  episode_id: number | null
  session_id: number | null
  status: TaskState
  queue_position: number | null
  created_at: string
  started_at: string | null
  finished_at: string | null
  progress: unknown
  log_path: string | null
  error: string | null
  request_id: string | null
  tail: string[]
}
