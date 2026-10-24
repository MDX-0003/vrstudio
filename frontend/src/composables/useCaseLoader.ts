import { ref } from 'vue'

export interface CaseLoaderOptions<T> {
  fetchCase: (name: string) => Promise<T>
  apply: (data: T) => void
  errorMessage?: (err: unknown) => string
}

/**
 * case 文档加载器：用请求序号 + case 名双重校验，旧响应不得覆盖新 case。
 */
export function useCaseLoader<T>(options: CaseLoaderOptions<T>) {
  const loading = ref(false)
  const error = ref('')
  let seq = 0
  let current = ''

  async function load(name: string): Promise<T | null> {
    const ticket = ++seq
    current = name
    loading.value = true
    error.value = ''
    try {
      const data = await options.fetchCase(name)
      if (ticket !== seq || name !== current) return null
      options.apply(data)
      return data
    } catch (err) {
      if (ticket !== seq || name !== current) return null
      error.value =
        options.errorMessage?.(err) ?? (err instanceof Error ? err.message : '读取 case 失败')
      return null
    } finally {
      if (ticket === seq) loading.value = false
    }
  }

  return { loading, error, load }
}
