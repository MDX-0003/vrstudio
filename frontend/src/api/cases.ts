import type {
  CaseBrief,
  CreateCasePayload,
  ModesResponse,
  SourcesResponse,
} from '@/types/case'
import { getJson, postJson } from './client'

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
): Promise<{ name: string; created?: boolean }> {
  return postJson<{ name: string; created?: boolean }>('/api/cases', payload)
}
