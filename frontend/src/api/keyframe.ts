import type { CaseInfo, FileInfo, PromptPayload } from '@/types/case-info'
import { getJson, postJson, putJson } from './client'
import { casePath } from './path'

export interface KeyframeRound {
  round: string
  key: string
  stage: string
  step: string
  image_model_id: number | null
  num: number | null
  gate: Record<string, unknown>
  results: ({ index: number; url: string } & FileInfo)[]
}

export interface KeyframeRounds {
  rounds: KeyframeRound[]
}

export interface KeyframeRunPayload {
  step: 'first' | 'last'
  /** 输出比例；patch 由视口锁定为 16:9，服务端会校验。 */
  ratio?: string
  resolution?: string
  num?: number
  /** 首尾帧两步共用的图片模型；缺省时用 generation.toml 的值。 */
  image_model_id?: number
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

/** 直接用当前抽取的原始帧当定稿：不重绘、不花额度。 */
export function useRawKeyframe(name: string, step: 'first' | 'last'): Promise<CaseInfo> {
  return postJson<CaseInfo>(`${casePath(name)}/keyframe/use-raw`, { step })
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

export interface KeyframeRefInput {
  asset: string
  role: 'subject_ref' | 'detail_ref'
  note: string
}

export function setKeyframeRefs(name: string, refs: KeyframeRefInput[]): Promise<CaseInfo> {
  return putJson<CaseInfo>(`${casePath(name)}/keyframe/refs`, { refs })
}

export function handoffKeyframe(name: string): Promise<CaseInfo> {
  return postJson<CaseInfo>(`${casePath(name)}/keyframe/handoff`, {})
}
