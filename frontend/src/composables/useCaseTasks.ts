import { onUnmounted, ref } from 'vue'
import { fetchTasks } from '@/api/tasks'
import type { TaskView } from '@/types/task'

function createdAt(task: TaskView): number {
  const value = new Date(task.created_at).getTime()
  return Number.isNaN(value) ? 0 : value
}

/**
 * 单个 case 的任务轮询。每个页面只用一份，避免各页各写一个 setInterval。
 */
export function useCaseTasks(caseName: string, kinds?: string[]) {
  const tasks = ref<TaskView[]>([])
  const latest = ref<TaskView | null>(null)
  const error = ref('')
  let timer: ReturnType<typeof setInterval> | null = null
  let inFlight = false

  async function refresh(): Promise<TaskView[]> {
    if (inFlight || document.hidden) return tasks.value
    inFlight = true
    try {
      const data = await fetchTasks('all')
      const rows = data.tasks
        .filter((task) => task.case === caseName)
        .filter((task) => !kinds || kinds.includes(task.kind))
        .sort((a, b) => createdAt(b) - createdAt(a))
      tasks.value = rows
      latest.value = rows[0] ?? latest.value
      error.value = ''
      return rows
    } catch (err) {
      error.value = err instanceof Error ? err.message : '读取任务失败'
      return tasks.value
    } finally {
      inFlight = false
    }
  }

  function start(intervalMs = 2000): void {
    void refresh()
    if (timer) clearInterval(timer)
    timer = setInterval(() => void refresh(), intervalMs)
  }

  function stop(): void {
    if (timer) {
      clearInterval(timer)
      timer = null
    }
  }

  onUnmounted(stop)

  return { tasks, latest, error, refresh, start, stop }
}
