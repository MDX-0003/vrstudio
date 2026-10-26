import type { CaseInfo } from '@/types/case-info'
import {
  ApiRequestError,
  forgetRevision,
  getJson,
  postJson,
  putJson,
  rememberRevision,
} from './client'
import { casePath } from './path'

export async function fetchCase(name: string): Promise<CaseInfo> {
  try {
    const data = await getJson<CaseInfo>(casePath(name))
    rememberRevision(name, data.meta?.revision ?? 0)
    return data
  } catch (err) {
    if (err instanceof ApiRequestError && err.status === 404) forgetRevision(name)
    throw err
  }
}

export function saveSubmitSelection(
  name: string,
  selected: string[],
  roles?: Record<string, string>,
): Promise<CaseInfo> {
  return putJson<CaseInfo>(`${casePath(name)}/submit`, { selected, roles })
}

export function changeAssetRole(name: string, id: string, role: string): Promise<CaseInfo> {
  return postJson<CaseInfo>(`${casePath(name)}/assets`, { op: 'role', id, role })
}

export function savePromptVar(name: string, key: string, value: string): Promise<CaseInfo> {
  return postJson<CaseInfo>(`${casePath(name)}/prompt`, { set: key, value })
}

export interface SaveParamsPayload {
  mode?: string
  aspect_ratio?: string
  resolution?: string
  scale?: string
  session_id?: number
  project_id?: number
  episode?: number
}

export function saveParams(name: string, payload: SaveParamsPayload): Promise<CaseInfo> {
  return postJson<CaseInfo>(`${casePath(name)}/params`, payload)
}

export type RunOperation =
  'dry_run' | 'submit' | 'adopt' | 'extract' | 'merge' | 'pad' | 'recompose'

export interface RunPayload {
  mode?: string
  aspect_ratio?: string
  resolution?: string
  scale?: string
  duration?: number
  round?: string
  allow_crop?: boolean
}

export function runOperation(
  name: string,
  op: RunOperation,
  payload: RunPayload = {},
): Promise<{ submitted: string; case: string }> {
  return postJson<{ submitted: string; case: string }>(`${casePath(name)}/run`, { op, ...payload })
}

export function addAssetsByUploads(
  name: string,
  uploadIds: string[],
  roles: string[],
): Promise<CaseInfo> {
  return postJson<CaseInfo>(`${casePath(name)}/assets`, {
    op: 'add',
    upload_ids: uploadIds,
    roles,
  })
}

export function removeAsset(name: string, id: string): Promise<CaseInfo> {
  return postJson<CaseInfo>(`${casePath(name)}/assets`, { op: 'remove', id })
}

export function reorderAssets(name: string, ids: string[]): Promise<CaseInfo> {
  return postJson<CaseInfo>(`${casePath(name)}/assets`, { op: 'reorder', ids })
}
