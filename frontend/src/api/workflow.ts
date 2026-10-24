import type { CaseInfo, FrameRange, Footprint, Inner, Viewport } from '@/types/case-info'
import { getJson, postJson } from './client'
import { casePath } from './path'

export interface GeometryPayload {
  viewport: Viewport
  inner: Inner
  previews: { vp?: string }
  viewport_footprint: Footprint[]
  nudged?: { yaw: number; pitch: number }
}

export function setViewport(name: string, fx: number, fy: number): Promise<GeometryPayload> {
  return postJson<GeometryPayload>(`${casePath(name)}/viewport`, { fx, fy })
}

export function setViewportSize(
  name: string,
  width: number,
  height: number,
): Promise<GeometryPayload> {
  return postJson<GeometryPayload>(`${casePath(name)}/viewport-size`, { width, height })
}

export function setInner(
  name: string,
  fx: number,
  fy: number,
  fw: number,
  fh: number,
): Promise<GeometryPayload> {
  return postJson<GeometryPayload>(`${casePath(name)}/inner`, { fx, fy, fw, fh })
}

export function nudgeViewport(
  name: string,
  dyaw: number,
  dpitch: number,
): Promise<GeometryPayload> {
  return postJson<GeometryPayload>(`${casePath(name)}/nudge`, { dyaw, dpitch })
}

export function saveDraft(
  name: string,
  sections: { viewport?: Viewport; inner?: Inner; frames?: FrameRange },
): Promise<CaseInfo> {
  return postJson<CaseInfo>(`${casePath(name)}/draft`, sections)
}

export function resetDraft(name: string, version?: string): Promise<GeometryPayload> {
  return postJson<GeometryPayload>(`${casePath(name)}/reset-draft`, version ? { version } : {})
}

export interface ProxyPayload {
  state: string
  url: string
}

export function fetchProxy(name: string, file?: string): Promise<ProxyPayload> {
  const query = file ? `?file=${encodeURIComponent(file)}` : ''
  return getJson<ProxyPayload>(`${casePath(name)}/proxy${query}`)
}

export function ensureProxy(name: string, file?: string): Promise<ProxyPayload> {
  return postJson<ProxyPayload>(
    `${casePath(name)}/proxy${file ? `?file=${encodeURIComponent(file)}` : ''}`,
    file ? { file } : {},
  )
}
