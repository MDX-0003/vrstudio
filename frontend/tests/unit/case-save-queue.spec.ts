import { afterEach, describe, expect, it, vi } from 'vitest'
import { useCaseSaveQueue } from '@/composables/useCaseSaveQueue'

describe('useCaseSaveQueue', () => {
  afterEach(() => {
    vi.useRealTimers()
  })

  it('writes each entry back to the case captured at schedule time', async () => {
    const save = vi.fn(async (_entry: unknown) => undefined)
    const queue = useCaseSaveQueue(save, { delayMs: 50 })

    queue.schedule('A', 'subject', 'a')
    queue.schedule('B', 'subject', 'b')

    await queue.flush('B')

    expect(save).toHaveBeenCalledTimes(1)
    expect(save.mock.calls[0][0]).toMatchObject({
      caseName: 'B',
      key: 'subject',
      payload: 'b',
    })
    expect(queue.has('A')).toBe(true)
    expect(queue.has('B')).toBe(false)
    queue.cancel()
  })

  it('does not write A when only B is flushed', async () => {
    const save = vi.fn(async (_entry: unknown) => undefined)
    const queue = useCaseSaveQueue(save, { delayMs: 50 })

    queue.schedule('A', 'plot', 'old')
    await queue.flush('B')

    expect(save).not.toHaveBeenCalled()

    await queue.flush('A')
    expect(save).toHaveBeenCalledWith({
      caseName: 'A',
      key: 'plot',
      payload: 'old',
    })
  })

  it('debounces automatic flush and keeps the latest value for one key', async () => {
    vi.useFakeTimers()
    const save = vi.fn(async (_entry: unknown) => undefined)
    const queue = useCaseSaveQueue(save, { delayMs: 100 })

    queue.schedule('A', 'subject', 'first')
    queue.schedule('A', 'subject', 'second')
    await vi.advanceTimersByTimeAsync(100)

    expect(save).toHaveBeenCalledTimes(1)
    expect(save.mock.calls[0][0]).toMatchObject({ payload: 'second' })
  })

  it('keeps failed entries pending', async () => {
    const save = vi.fn(async () => {
      throw new Error('网络失败')
    })
    const queue = useCaseSaveQueue(save, { delayMs: 50 })

    queue.schedule('A', 'subject', 'value')
    const ok = await queue.flush('A')

    expect(ok).toBe(false)
    expect(queue.has('A')).toBe(true)
    expect(queue.lastError.value).toBeInstanceOf(Error)
    queue.cancel()
  })

  it('cancels pending entries for one case only', async () => {
    const save = vi.fn(async (_entry: unknown) => undefined)
    const queue = useCaseSaveQueue(save, { delayMs: 50 })

    queue.schedule('A', 'subject', 'a')
    queue.schedule('B', 'subject', 'b')
    queue.cancel('A')

    expect(queue.has('A')).toBe(false)
    expect(queue.has('B')).toBe(true)
    queue.cancel()
  })

  it('reports the flushed case and result to the caller', async () => {
    const save = vi.fn(async (_entry: unknown) => undefined)
    const onFlush = vi.fn()
    const queue = useCaseSaveQueue(save, { delayMs: 50, onFlush })

    queue.schedule('A', 'subject', 'a')
    await queue.flush('A')

    expect(onFlush).toHaveBeenCalledWith(true, 'A')
  })

  it('serializes overlapping flushes so one value is not saved twice', async () => {
    let release!: () => void
    const save = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          release = resolve
        }),
    )
    const queue = useCaseSaveQueue(save, { delayMs: 50 })

    queue.schedule('A', 'subject', 'a')
    const first = queue.flush('A')
    const second = queue.flush('A')
    release()
    await Promise.all([first, second])

    expect(save).toHaveBeenCalledTimes(1)
  })
})
