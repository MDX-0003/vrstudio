import { describe, expect, it } from 'vitest'
import { useCaseLoader } from '@/composables/useCaseLoader'

interface Row {
  name: string
}

function deferred<T>() {
  let resolve!: (value: T) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

describe('useCaseLoader', () => {
  it('ignores an older response after a newer case starts loading', async () => {
    const a = deferred<Row>()
    const b = deferred<Row>()
    const applied: string[] = []
    const loader = useCaseLoader<Row>({
      fetchCase: (name) => (name === 'A' ? a.promise : b.promise),
      apply: (data) => applied.push(data.name),
    })

    const loadA = loader.load('A')
    const loadB = loader.load('B')

    b.resolve({ name: 'B' })
    await loadB
    a.resolve({ name: 'A' })
    await loadA

    expect(applied).toEqual(['B'])
    expect(loader.loading.value).toBe(false)
    expect(loader.error.value).toBe('')
  })

  it('reports an error and stops loading when the current request fails', async () => {
    const loader = useCaseLoader<Row>({
      fetchCase: async () => {
        throw new Error('后端未启动')
      },
      apply: () => undefined,
    })

    const result = await loader.load('A')

    expect(result).toBeNull()
    expect(loader.loading.value).toBe(false)
    expect(loader.error.value).toBe('后端未启动')
  })

  it('does not report an error from a request that has been superseded', async () => {
    const a = deferred<Row>()
    const b = deferred<Row>()
    const loader = useCaseLoader<Row>({
      fetchCase: (name) => (name === 'A' ? a.promise : b.promise),
      apply: () => undefined,
    })

    const loadA = loader.load('A')
    const loadB = loader.load('B')

    b.resolve({ name: 'B' })
    await loadB
    a.reject(new Error('旧请求失败'))
    await loadA

    expect(loader.error.value).toBe('')
    expect(loader.loading.value).toBe(false)
  })
})
