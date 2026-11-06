export type TaskState = 'queued' | 'running' | 'done' | 'failed'

/** 提交任务复用了已有轮次：本次没有发请求、没有扣额度。 */
export interface ReusedRound {
  round: string
  reason: 'key' | 'resumed'
  task_id: number | null
}

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
  /** 服务端对可识别报错的归类；目前只有 `geometry`（裁回的几何前提闸门）。 */
  error_kind: string | null
  reused: ReusedRound | null
  request_id: string | null
  tail: string[]
}
