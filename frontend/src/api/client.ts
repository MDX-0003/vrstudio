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

export async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(path, {
    headers: withRevision(path, { Accept: 'application/json' }),
  })
  noteRevision(path, response)
  const data = await parseBody(response)
  if (!response.ok) throw failure(data, response.status)
  return data as T
}

export async function postJson<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(path, {
    method: 'POST',
    headers: withRevision(path, {
      'Content-Type': 'application/json',
      Accept: 'application/json',
    }),
    body: JSON.stringify(body),
  })
  noteRevision(path, response)
  const data = await parseBody(response)
  if (!response.ok) throw failure(data, response.status)
  return data as T
}

export async function putJson<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(path, {
    method: 'PUT',
    headers: withRevision(path, {
      'Content-Type': 'application/json',
      Accept: 'application/json',
    }),
    body: JSON.stringify(body),
  })
  noteRevision(path, response)
  const data = await parseBody(response)
  if (!response.ok) throw failure(data, response.status)
  return data as T
}

export async function putBlob<T>(path: string, body: Blob): Promise<T> {
  const response = await fetch(path, {
    method: 'PUT',
    headers: withRevision(path, {
      'Content-Type': 'application/octet-stream',
      Accept: 'application/json',
    }),
    body,
  })
  noteRevision(path, response)
  const data = await parseBody(response)
  if (!response.ok) throw failure(data, response.status)
  return data as T
}

export async function deleteJson<T>(path: string): Promise<T> {
  const response = await fetch(path, {
    method: 'DELETE',
    headers: withRevision(path, { Accept: 'application/json' }),
  })
  noteRevision(path, response)
  const data = await parseBody(response)
  if (!response.ok) throw failure(data, response.status)
  return data as T
}
