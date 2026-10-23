import type { TaskView } from '@/types/task'
import { getJson } from './client'

export function fetchTasks(scope: 'all' | 'mine' = 'all'): Promise<{ tasks: TaskView[] }> {
  return getJson<{ tasks: TaskView[] }>(`/api/tasks?scope=${scope}`)
}
