import type { CaseInfo } from '@/types/case-info'
import { getJson, postJson, putJson } from './client'

function casePath(name: string): string {
  return `/api/case/${encodeURIComponent(name)}`
}

export function fetchCase(name: string): Promise<CaseInfo> {
  return getJson<CaseInfo>(casePath(name))
}

export function saveSubmitSelection(name: string, selected: string[]): Promise<CaseInfo> {
  return putJson<CaseInfo>(`${casePath(name)}/submit`, { selected })
}

export function changeAssetRole(name: string, id: string, role: string): Promise<CaseInfo> {
  return postJson<CaseInfo>(`${casePath(name)}/assets`, { op: 'role', id, role })
}

export function savePromptVar(name: string, key: string, value: string): Promise<CaseInfo> {
  return postJson<CaseInfo>(`${casePath(name)}/prompt`, { set: key, value })
}
