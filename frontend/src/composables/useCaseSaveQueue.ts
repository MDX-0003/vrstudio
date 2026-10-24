import { ref } from 'vue'

export interface CaseSaveEntry<T> {
  caseName: string
  key: string
  payload: T
}

export interface CaseSaveQueueOptions {
  delayMs?: number
  onFlush?: (ok: boolean, caseName?: string) => void
}

/**
 * 带 case 作用域的防抖保存队列。
 *
 * 每个条目在 schedule 时捕获自己的 case 名；切换 case 后，旧条目仍然写回旧 case，
 * 不会因为使用“当前 caseName”而串写到新 case。flush(caseName) 只处理指定 case。
 */
export function useCaseSaveQueue<T>(
  save: (entry: CaseSaveEntry<T>) => Promise<unknown>,
  options: CaseSaveQueueOptions = {},
) {
  const delayMs = options.delayMs ?? 400
  const pending = new Map<string, CaseSaveEntry<T>>()
  const lastError = ref<unknown>(null)
  let timer: ReturnType<typeof setTimeout> | null = null
  let activeFlush: Promise<boolean> | null = null

  function idOf(caseName: string, key: string): string {
    return `${caseName}\u0000${key}`
  }

  function clearTimer(): void {
    if (timer) {
      clearTimeout(timer)
      timer = null
    }
  }

  function restartTimer(): void {
    clearTimer()
    timer = setTimeout(() => {
      timer = null
      void flush()
    }, delayMs)
  }

  async function flushInternal(caseName?: string): Promise<boolean> {
    clearTimer()
    const entries = [...pending.entries()].filter(
      ([, entry]) => !caseName || entry.caseName === caseName,
    )
    if (!entries.length) return true
    let ok = true
    lastError.value = null
    for (const [id, entry] of entries) {
      try {
        await save(entry)
        pending.delete(id)
      } catch (err) {
        lastError.value = err
        ok = false
      }
    }
    options.onFlush?.(ok, caseName)
    return ok
  }

  function schedule(caseName: string, key: string, payload: T): void {
    if (!caseName) return
    pending.set(idOf(caseName, key), { caseName, key, payload })
    restartTimer()
  }

  function flush(caseName?: string): Promise<boolean> {
    if (activeFlush) {
      return activeFlush.then(() => flush(caseName))
    }
    const task = flushInternal(caseName).finally(() => {
      if (activeFlush === task) activeFlush = null
    })
    activeFlush = task
    return task
  }

  function cancel(caseName?: string): void {
    clearTimer()
    if (!caseName) {
      pending.clear()
      return
    }
    for (const [id, entry] of [...pending.entries()]) {
      if (entry.caseName === caseName) pending.delete(id)
    }
  }

  function has(caseName?: string): boolean {
    for (const entry of pending.values()) {
      if (!caseName || entry.caseName === caseName) return true
    }
    return false
  }

  return { schedule, flush, cancel, has, lastError }
}
