import { onUnmounted, ref, toValue, watch, type MaybeRef } from 'vue'
import { fetchTasks } from '@/api/tasks'
import type { TaskView } from '@/types/task'

function createdAt(task: TaskView): number {
  const value = new Date(task.created_at).getTime()
  return Number.isNaN(value) ? 0 : value
}

/**
 * 轮询每次都会返回新对象；latest 只在任务语义变化时换引用，避免页面
 * watch(latest) 对同一条已完成任务反复弹“完成/失败”提示。
 */
function sameLatest(a: TaskView | null, b: TaskView | null): boolean {
  if (!a || !b) return a === b
  if (a.id !== b.id) return false
  if (a.status !== b.status) return false
  // 终态不再跟随 tail/queue 变化，提示只发一次。
  if (a.status === 'done' || a.status === 'failed') return true
  return (
    a.queue_position === b.queue_position &&
    a.error === b.error &&
    a.error_kind === b.error_kind &&
    a.tail.length === b.tail.length &&
    a.tail.every((line, index) => line === b.tail[index])
  )
}

/**
 * 单个 case 的任务轮询。caseName 可以是 ref；切换 case 时清空旧任务并重新拉取。
 * 每个页面只用一份，避免各页各写一个 setInterval。
 */
export function useCaseTasks(caseName: MaybeRef<string>, kinds?: string[]) {
  const tasks = ref<TaskView[]>([])
  const latest = ref<TaskView | null>(null)
  const error = ref('')
  let timer: ReturnType<typeof setInterval> | null = null
  let inFlight = false

  function currentCase(): string {
    return toValue(caseName)
  }

  async function refresh(): Promise<TaskView[]> {
    if (inFlight || document.hidden) return tasks.value
    const requested = currentCase()
    if (!requested) return tasks.value
    inFlight = true
    try {
      const data = await fetchTasks('all', requested)
      if (requested !== currentCase()) return tasks.value
      const rows = data.tasks
        .filter((task) => !kinds || kinds.includes(task.kind))
        .sort((a, b) => createdAt(b) - createdAt(a))
      tasks.value = rows
      const next = rows[0] ?? null
      if (!sameLatest(latest.value, next)) latest.value = next
      error.value = ''
      return rows
    } catch (err) {
      if (requested !== currentCase()) return tasks.value
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

  watch(currentCase, () => {
    tasks.value = []
    latest.value = null
    void refresh()
  })

  onUnmounted(stop)

  return { tasks, latest, error, refresh, start, stop }
}
