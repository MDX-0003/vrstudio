import type { CaseInfo, RelightRegionDoc } from '@/types/case-info'
import { getJson, postJson } from './client'
import { casePath } from './path'

export interface RelightRegionsResponse {
  regions: RelightRegionDoc | null
  error: string | null
}

export function fetchRelightRegions(name: string): Promise<RelightRegionsResponse> {
  return getJson(`${casePath(name)}/relight/regions`)
}

export function saveRelightRegions(
  name: string,
  doc: RelightRegionDoc,
): Promise<CaseInfo> {
  return postJson<CaseInfo>(`${casePath(name)}/relight/regions`, doc)
}
