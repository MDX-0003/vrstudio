import type { CaseInfo, PromptPayload } from '@/types/case-info'
import { getJson, postJson, putJson } from './client'

function casePath(name: string): string {
  return `/api/case/${encodeURIComponent(name)}`
}

export interface KeyframeRound {
  round: string
  key: string
  stage: string
  step: string
  model_id: number | null
  num: number | null
  gate: Record<string, unknown>
  results: { index: number; url: string }[]
}

export interface KeyframeRounds {
  rounds: KeyframeRound[]
}

export interface KeyframeRunPayload {
  step: 'first' | 'last'
  model_id?: number
  resolution?: string
  num?: number
}

export function fetchKeyframeRounds(name: string): Promise<KeyframeRounds> {
  return getJson<KeyframeRounds>(`${casePath(name)}/keyframe/rounds`)
}

export function runKeyframe(
  name: string,
  payload: KeyframeRunPayload,
): Promise<{ submitted: string }> {
  return postJson<{ submitted: string }>(`${casePath(name)}/keyframe/run`, payload)
}

export function pickKeyframe(
  name: string,
  role: 'first_frame' | 'last_frame',
  round: string,
  index: number,
): Promise<CaseInfo> {
  return postJson<CaseInfo>(`${casePath(name)}/keyframe/pick`, { role, round, index })
}

export function fetchKeyframePrompt(name: string, step: 'first' | 'last'): Promise<PromptPayload> {
  return getJson<PromptPayload>(
    `${casePath(name)}/keyframe/prompt?step=${encodeURIComponent(step)}`,
  )
}

export function saveKeyframePrompt(
  name: string,
  step: 'first' | 'last',
  vars: Record<string, string>,
): Promise<PromptPayload> {
  return putJson<PromptPayload>(`${casePath(name)}/keyframe/prompt`, { step, vars })
}

export function setKeyframeSheet(name: string, asset: string): Promise<CaseInfo> {
  return putJson<CaseInfo>(`${casePath(name)}/keyframe/sheet`, { asset })
}

export function handoffKeyframe(name: string): Promise<CaseInfo> {
  return postJson<CaseInfo>(`${casePath(name)}/keyframe/handoff`, {})
}
