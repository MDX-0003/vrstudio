import { getJson, postJson } from './client'
import { casePath } from './path'

export interface CaseLock {
  owner_id: string | null
  owner_name: string | null
  acquired_at: string | null
  heartbeat_at: string | null
}

export interface LockPayload {
  lock: CaseLock | null
  mine: boolean
}

function path(name: string): string {
  return `${casePath(name)}/lock`
}

export function fetchLock(name: string): Promise<LockPayload> {
  return getJson<LockPayload>(path(name))
}

export function acquireLock(name: string): Promise<LockPayload> {
  return postJson<LockPayload>(path(name), {})
}

export function releaseLock(name: string): void {
  void fetch(path(name), { method: 'DELETE', keepalive: true })
}
