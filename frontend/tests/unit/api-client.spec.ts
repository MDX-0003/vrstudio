import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiRequestError, forgetRevision, getJson, postJson, rememberRevision } from '@/api/client'
import { createUpload, putChunk } from '@/api/uploads'

afterEach(() => {
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('api client', () => {
  it('把 502 归一到后端未启动提示', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('', { status: 502 })))
    await expect(getJson('/api/cases')).rejects.toThrow('后端服务没有启动')
  })

  it('保留后端 error 文本并带状态码', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(jsonResponse({ error: 'case 不存在' }, 404)))
    const error = await getJson('/api/case/nope').catch((err: unknown) => err)
    expect(error).toBeInstanceOf(ApiRequestError)
    expect((error as ApiRequestError).status).toBe(404)
    expect((error as ApiRequestError).message).toBe('case 不存在')
  })

  it('POST 使用 JSON 请求体', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ ok: true }))
    vi.stubGlobal('fetch', fetchMock)
    await postJson('/api/me', { display_name: '小王' })
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/me',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ display_name: '小王' }),
      }),
    )
  })

  it('aborts a request that exceeds the timeout', async () => {
    vi.useFakeTimers()
    vi.stubGlobal(
      'fetch',
      vi.fn((_url: string, init: RequestInit) => {
        return new Promise((_resolve, reject) => {
          init.signal?.addEventListener('abort', () => {
            reject(new DOMException('Aborted', 'AbortError'))
          })
        })
      }),
    )

    const pending = getJson('/api/cases')
    const assertion = expect(pending).rejects.toThrow('请求超时')
    await vi.advanceTimersByTimeAsync(30_000)
    await assertion
  })

  it('sends a remembered revision and forgets it on demand', async () => {
    const fetchMock = vi.fn(() => Promise.resolve(jsonResponse({})))
    vi.stubGlobal('fetch', fetchMock)

    rememberRevision('rev-case', 7)
    await getJson('/api/case/rev-case')
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/case/rev-case',
      expect.objectContaining({
        headers: expect.objectContaining({ 'X-Case-Revision': '7' }),
      }),
    )

    forgetRevision('rev-case')
    fetchMock.mockClear()
    await getJson('/api/case/rev-case')
    const headers = fetchMock.mock.calls[0]?.[1]?.headers as Record<string, string>
    expect(headers['X-Case-Revision']).toBeUndefined()
  })

  it('bounds the revision cache', async () => {
    for (let index = 0; index < 101; index += 1) rememberRevision(`rev-${index}`, index)
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({}))
    vi.stubGlobal('fetch', fetchMock)

    await getJson('/api/case/rev-0')
    const headers = fetchMock.mock.calls[0]?.[1]?.headers as Record<string, string>
    expect(headers['X-Case-Revision']).toBeUndefined()
  })
})

describe('upload api', () => {
  it('创建上传会话并带上文件大小', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        jsonResponse({ upload_id: 'a'.repeat(32), chunk_size: 8388608, max_size: 100 }),
      )
    vi.stubGlobal('fetch', fetchMock)
    const session = await createUpload('ref.png', 42)
    expect(session.upload_id).toBe('a'.repeat(32))
    expect(fetchMock).toHaveBeenCalledWith(
      '/api/uploads',
      expect.objectContaining({
        method: 'POST',
        body: JSON.stringify({ name: 'ref.png', size: 42 }),
      }),
    )
  })

  it('分片 PUT 带 offset 且使用二进制请求体', async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ received: 5, size: 5 }))
    vi.stubGlobal('fetch', fetchMock)
    const chunk = new Blob(['hello'])
    await putChunk('b'.repeat(32), 0, chunk)
    expect(fetchMock).toHaveBeenCalledWith(
      `/api/uploads/${'b'.repeat(32)}?offset=0`,
      expect.objectContaining({
        method: 'PUT',
        body: chunk,
        headers: expect.objectContaining({ 'Content-Type': 'application/octet-stream' }),
      }),
    )
  })
})
