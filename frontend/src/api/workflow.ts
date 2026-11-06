import type {
  CaseInfo,
  Coverage,
  FrameRange,
  Footprint,
  Inner,
  RegionPayload,
  Viewport,
} from '@/types/case-info'
import { getJson, postJson } from './client'
import { casePath } from './path'

export interface GeometryPayload {
  viewport: Viewport
  inner: Inner
  previews: { vp?: string }
  viewport_footprint: Footprint[]
  coverage?: Coverage | null
  fov_range?: [number, number]
  nudged?: { yaw: number; pitch: number }
  /** 选区范围导入后带回的帧范围；其他几何端点没有这一项。 */
  frames?: FrameRange
  /** 导入时 inner 是否因视口尺寸不同被缩放。 */
  scaled?: boolean
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

export function setFov(name: string, value: number): Promise<GeometryPayload> {
  return postJson<GeometryPayload>(`${casePath(name)}/fov`, { value })
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

/** 读已经保存的选区，返回剪贴板负载；前端负责序列化与写剪贴板。 */
export function exportRegion(name: string): Promise<RegionPayload> {
  return getJson<RegionPayload>(`${casePath(name)}/region/export`)
}

/** 把剪贴板里解析出的选区写进当前 case；应答同时带回新的预览与帧范围。 */
export function importRegion(name: string, payload: unknown): Promise<GeometryPayload> {
  return postJson<GeometryPayload>(`${casePath(name)}/region/import`, payload)
}

export interface ProxyPayload {
  state: string
  url: string
  /** 就绪/直连时代理（或直连源）的宽度；对比页标签显示“2048 代理”用。 */
  width?: number | null
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
