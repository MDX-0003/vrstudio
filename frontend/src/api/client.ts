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
const MAX_REVISIONS = 100
const DEFAULT_TIMEOUT_MS = 30_000
const BLOB_TIMEOUT_MS = 120_000

/** 发生过未解决 409 的 case：期间重复 409 不再弹窗，重新加载或覆盖成功后解除。 */
const conflicted = new Set<string>()
/** 最近一次 409 的请求，供「仍要保存（覆盖对方改动）」重发。 */
let pendingOverwrite: { path: string; init: RequestInit } | null = null

function setRevision(caseName: string, revision: number): void {
  revisions.delete(caseName)
  revisions.set(caseName, revision)
  while (revisions.size > MAX_REVISIONS) {
    const oldest = revisions.keys().next().value
    if (oldest === undefined) break
    revisions.delete(oldest)
  }
}

export function rememberRevision(caseName: string, revision: number): void {
  if (caseName && Number.isFinite(revision)) setRevision(caseName, revision)
}

export function forgetRevision(caseName: string): void {
  revisions.delete(caseName)
  conflicted.delete(caseName)
}

function caseNameOf(path: string): string | null {
  const match = path.match(/^\/api\/case\/([^/?#]+)/)
  return match ? decodeURIComponent(match[1]) : null
}

/** 几何草稿写 derived/pick/draft.toml：高频、最后写入者胜出，不带 case revision。 */
function isGeometryPath(path: string): boolean {
  return /^\/api\/case\/[^/]+\/(?:viewport|inner|nudge|viewport-size|pick-draft|region\/import)(?:\?|$)/.test(
    path,
  )
}

function withRevision(path: string, base: Record<string, string>): Record<string, string> {
  const name = caseNameOf(path)
  if (name && revisions.has(name) && !isGeometryPath(path)) {
    return { ...base, 'X-Case-Revision': String(revisions.get(name)) }
  }
  return base
}

function noteRevision(path: string, response: Response): void {
  const name = caseNameOf(path)
  const raw = response.headers.get('x-case-revision')
  if (!name || raw === null) return
  const value = Number(raw)
  if (Number.isFinite(value)) setRevision(name, value)
}

function noteConflict(data: unknown, status: number, path = '', init?: RequestInit): void {
  if (status !== 409 || !data || typeof data !== 'object') return
  if ((data as { code?: string }).code !== 'revision_conflict') return
  const name = caseNameOf(path)
  // 同一轮冲突只弹一次：未解决期间重复 409 静默抛错。
  if (name && conflicted.has(name)) return
  if (name) conflicted.add(name)
  if (init) pendingOverwrite = { path, init }
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new CustomEvent('case-conflict', { detail: data }))
  }
}

function failure(data: unknown, status: number, path = '', init?: RequestInit): ApiRequestError {
  noteConflict(data, status, path, init)
  return new ApiRequestError(describeError(data, status), status, data)
}

/**
 * 「仍要保存（覆盖对方改动）」：去掉 revision 头重发最近一次 409 的请求。
 * 成功后响应头带回新 revision，noteRevision 自动恢复后续的常规保护。
 */
export async function retryOverwrite(): Promise<boolean> {
  const pending = pendingOverwrite
  pendingOverwrite = null
  if (!pending) return false
  const headers = new Headers(pending.init.headers)
  headers.delete('X-Case-Revision')
  try {
    await request(pending.path, { ...pending.init, headers })
    const name = caseNameOf(pending.path)
    if (name) conflicted.delete(name)
    return true
  } catch {
    return false
  }
}

export function isCaseConflicted(name: string): boolean {
  return conflicted.has(name)
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
    if (!response.ok) throw failure(data, response.status, path, init)
    // 重新加载成功 = 冲突解除（revision 已刷新）。
    const name = caseNameOf(path)
    if (name && !init.method) conflicted.delete(name)
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
