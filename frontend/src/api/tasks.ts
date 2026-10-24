import type { TaskView } from '@/types/task'
import { getJson } from './client'

export function fetchTasks(
  scope: 'all' | 'mine' = 'all',
  caseName?: string,
): Promise<{ tasks: TaskView[] }> {
  const query = new URLSearchParams({ scope })
  if (caseName) query.set('case', caseName)
  return getJson<{ tasks: TaskView[] }>(`/api/tasks?${query.toString()}`)
}
