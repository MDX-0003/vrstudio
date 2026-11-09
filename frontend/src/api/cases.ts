import type {
  CaseBrief,
  CreateCasePayload,
  CreatedSource,
  ModesResponse,
  SourcesResponse,
} from '@/types/case'
import { deleteJson, getJson, postJson } from './client'

export function fetchCases(): Promise<CaseBrief[]> {
  return getJson<CaseBrief[]>('/api/cases')
}

export function fetchSources(): Promise<SourcesResponse> {
  return getJson<SourcesResponse>('/api/sources')
}

export function fetchModes(): Promise<ModesResponse> {
  return getJson<ModesResponse>('/api/modes')
}

export function createCaseRequest(
  payload: CreateCasePayload,
): Promise<{ name: string; created?: boolean; source?: CreatedSource | null }> {
  return postJson<{ name: string; created?: boolean; source?: CreatedSource | null }>(
    '/api/cases',
    payload,
  )
}

/** 删除 case：服务端清掉 cases/<name>/ 与 vrgen_runs/<name>/。不可恢复。 */
export function deleteCaseRequest(
  name: string,
): Promise<{ deleted: string; source_reclaimed?: string | null }> {
  return deleteJson<{ deleted: string; source_reclaimed?: string | null }>(
    `/api/case/${encodeURIComponent(name)}`,
  )
}
