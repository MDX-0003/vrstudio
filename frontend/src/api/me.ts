import { getJson, postJson } from './client'

export interface Me {
  id: string
  display_name: string
  authenticated: boolean
}

export function fetchMe(): Promise<Me> {
  return getJson<Me>('/api/me')
}

export function saveDisplayName(displayName: string): Promise<Me> {
  return postJson<Me>('/api/me', { display_name: displayName })
}
