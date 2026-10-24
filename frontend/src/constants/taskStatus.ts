import type { TaskState } from '@/types/task'

export interface TaskStatusMeta {
  text: string
  type: 'primary' | 'warning' | 'success' | 'danger' | 'info'
}

export const TASK_STATUS_META: Record<TaskState, TaskStatusMeta> = {
  queued: { text: '排队中', type: 'warning' },
  running: { text: '运行中', type: 'primary' },
  done: { text: '已完成', type: 'success' },
  failed: { text: '失败', type: 'danger' },
}

export function taskStatusMeta(status: string): TaskStatusMeta {
  return TASK_STATUS_META[status as TaskState] ?? { text: status, type: 'info' }
}
