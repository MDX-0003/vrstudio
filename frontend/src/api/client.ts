export class ApiRequestError extends Error {
  status: number
  payload: unknown

  constructor(message: string, status: number, payload?: unknown) {
    super(message)
    this.name = 'ApiRequestError'
    this.status = status
    this.payload = payload
  }
}

const revisions = new Map<string, number>()
const DEFAULT_TIMEOUT_MS = 30_000
const BLOB_TIMEOUT_MS = 120_000

export function rememberRevision(caseName: string, revision: number): void {
  if (caseName && Number.isFinite(revision)) revisions.set(caseName, revision)
}

export function forgetRevision(caseName: string): void {
  revisions.delete(caseName)
}

function caseNameOf(path: string): string | null {
  const match = path.match(/^\/api\/case\/([^/?#]+)/)
  return match ? decodeURIComponent(match[1]) : null
}

function withRevision(path: string, base: Record<string, string>): Record<string, string> {
  const name = caseNameOf(path)
  if (name && revisions.has(name)) {
    return { ...base, 'X-Case-Revision': String(revisions.get(name)) }
  }
  return base
}

function noteRevision(path: string, response: Response): void {
  const name = caseNameOf(path)
  const raw = response.headers.get('x-case-revision')
  if (!name || raw === null) return
  const value = Number(raw)
  if (Number.isFinite(value)) revisions.set(name, value)
}

function noteConflict(data: unknown, status: number): void {
  if (status !== 409 || !data || typeof data !== 'object') return
  if ((data as { code?: string }).code !== 'revision_conflict') return
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new CustomEvent('case-conflict', { detail: data }))
  }
}

function failure(data: unknown, status: number): ApiRequestError {
  noteConflict(data, status)
  return new ApiRequestError(describeError(data, status), status, data)
}

async function parseBody(response: Response): Promise<unknown> {
  const text = await response.text()
  if (!text) return null
  try {
    return JSON.parse(text)
  } catch {
    return text
  }
}

function describeError(data: unknown, status: number): string {
  if (status === 502 || status === 504) {
    return '后端服务没有启动，请先运行 uv run vrstudio serve'
  }
  if (data && typeof data === 'object' && 'error' in data) {
    return String((data as { error: unknown }).error)
  }
  if (typeof data === 'string' && data.trim()) {
    return data
  }
  return `请求失败（${status}）`
}

async function request<T>(
  path: string,
  init: RequestInit,
  timeoutMs = DEFAULT_TIMEOUT_MS,
): Promise<T> {
  const controller = new AbortController()
  const timer = timeoutMs > 0 ? setTimeout(() => controller.abort(), timeoutMs) : null
  try {
    const response = await fetch(path, { ...init, signal: controller.signal })
    noteRevision(path, response)
    const data = await parseBody(response)
    if (!response.ok) throw failure(data, response.status)
    return data as T
  } catch (err) {
    if (err instanceof ApiRequestError) throw err
    if ((err as { name?: string } | null)?.name === 'AbortError') {
      throw new ApiRequestError('请求超时，请检查后端或网络', 0)
    }
    throw err
  } finally {
    if (timer) clearTimeout(timer)
  }
}

export function getJson<T>(path: string): Promise<T> {
  return request<T>(path, {
    headers: withRevision(path, { Accept: 'application/json' }),
  })
}

export function postJson<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, {
    method: 'POST',
    headers: withRevision(path, {
      'Content-Type': 'application/json',
      Accept: 'application/json',
    }),
    body: JSON.stringify(body),
  })
}

export function putJson<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, {
    method: 'PUT',
    headers: withRevision(path, {
      'Content-Type': 'application/json',
      Accept: 'application/json',
    }),
    body: JSON.stringify(body),
  })
}

export function putBlob<T>(path: string, body: Blob): Promise<T> {
  return request<T>(
    path,
    {
      method: 'PUT',
      headers: withRevision(path, {
        'Content-Type': 'application/octet-stream',
        Accept: 'application/json',
      }),
      body,
    },
    BLOB_TIMEOUT_MS,
  )
}

export function deleteJson<T>(path: string): Promise<T> {
  return request<T>(path, {
    method: 'DELETE',
    headers: withRevision(path, { Accept: 'application/json' }),
  })
}
