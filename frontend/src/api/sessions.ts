import type { SessionsResponse } from '@/types/session'
import { getJson } from './client'

export function fetchSessions(): Promise<SessionsResponse> {
  return getJson<SessionsResponse>('/api/sessions')
}
