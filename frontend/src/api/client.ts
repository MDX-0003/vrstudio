export class ApiRequestError extends Error {
  status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiRequestError'
    this.status = status
  }
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
  const response = await fetch(path, { headers: { Accept: 'application/json' } })
  const data = await parseBody(response)
  if (!response.ok) {
    throw new ApiRequestError(describeError(data, response.status), response.status)
  }
  return data as T
}

export async function postJson<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify(body),
  })
  const data = await parseBody(response)
  if (!response.ok) {
    throw new ApiRequestError(describeError(data, response.status), response.status)
  }
  return data as T
}

export async function putJson<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(path, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify(body),
  })
  const data = await parseBody(response)
  if (!response.ok) {
    throw new ApiRequestError(describeError(data, response.status), response.status)
  }
  return data as T
}
