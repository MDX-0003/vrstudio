import type {
  CaseInfo,
  Coverage,
  FrameRange,
  Footprint,
  Inner,
  RegionPayload,
  Viewport,
} from '@/types/case-info'
import { deleteJson, getJson, postJson } from './client'
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
  /** 导入时帧范围是否因目标更短而被截到最后一帧。 */
  frames_clamped?: boolean
  /** 当前 draft 与 active preset 是否不同。 */
  dirty?: boolean
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

/** 写工作草稿 derived/pick/draft.toml；不写 case.toml。 */
export function savePickDraft(
  name: string,
  sections: { viewport?: Viewport; inner?: Inner; frames?: FrameRange },
): Promise<GeometryPayload> {
  return postJson<GeometryPayload>(`${casePath(name)}/pick-draft`, sections)
}

/** 从当前 draft 另存为一条 preset。 */
export function createRangePreset(name: string): Promise<CaseInfo> {
  return postJson<CaseInfo>(`${casePath(name)}/range-presets`, {})
}

/** 用当前 draft 覆盖指定 preset。 */
export function saveRangePreset(name: string, presetId: string): Promise<CaseInfo> {
  return postJson<CaseInfo>(`${casePath(name)}/range-presets/${presetId}/save`, {})
}

/** 把 preset 复制进当前 draft 并设为 active。 */
export function loadRangePreset(name: string, presetId: string): Promise<GeometryPayload> {
  return postJson<GeometryPayload>(`${casePath(name)}/range-presets/${presetId}/load`, {})
}

export function deleteRangePreset(name: string, presetId: string): Promise<CaseInfo> {
  return deleteJson<CaseInfo>(`${casePath(name)}/range-presets/${presetId}`)
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
