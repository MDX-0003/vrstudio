/**
 * 可覆盖参数的唯一描述表（决策 88）。
 *
 * 默认参数卡与区域参数卡都从这里渲染；新增一个可覆盖参数 = 往 PARAMS 加一项，
 * 再在引擎里加一个通道，两侧控件自动跟上。任何一侧都不许手写控件清单——
 * 原型第一版就出现过「默认卡把软裁切写成只读文本」的分叉。
 */

export type NumericParamId = 'strength' | 'local_shading'

export interface NumericParamDesc {
  id: NumericParamId
  label: string
  kind: 'number'
  min: number
  max: number
  step: number
  /** 默认值的来源说明，只用于提示文案。 */
  defaultFrom: string
}

export interface ClipParamDesc {
  id: 'clip'
  label: string
  kind: 'clip'
  knee: { min: number; max: number; step: number; fallback: number }
  max: { min: number; max: number; step: number; fallback: number }
}

export type ParamDesc = NumericParamDesc | ClipParamDesc

export interface RegionClip {
  knee: number
  max: number
}

/** 区域参数：`null` 表示沿用默认值，该通道不参与叠加。 */
export interface RegionParams {
  strength: number | null
  local_shading: number | null
  clip: RegionClip | null
}

export interface DefaultParams {
  strength: number
  local_shading: number
  clip: RegionClip | null
}

export const PARAMS: ParamDesc[] = [
  {
    id: 'strength',
    label: '整体施加量',
    kind: 'number',
    min: 0,
    max: 1,
    step: 0.05,
    defaultFrom: '[relight] strength 或场景预设',
  },
  {
    id: 'local_shading',
    label: '局部明暗',
    kind: 'number',
    min: 0,
    max: 1,
    step: 0.05,
    defaultFrom: '[relight] local_shading 或场景预设',
  },
  {
    id: 'clip',
    label: '高光软裁切',
    kind: 'clip',
    knee: { min: 0.01, max: 0.99, step: 0.01, fallback: 0.72 },
    max: { min: 0.02, max: 1, step: 0.01, fallback: 0.98 },
  },
]

export const CLIP_DESC = PARAMS.find((item) => item.id === 'clip') as ClipParamDesc

export function emptyRegionParams(): RegionParams {
  return { strength: null, local_shading: null, clip: null }
}

export function defaultsFromDraft(draft: {
  strength?: number
  local_shading?: number
  highlight_clip?: boolean
  clip_knee?: number
  clip_max?: number
}): DefaultParams {
  return {
    strength: draft.strength ?? 1,
    local_shading: draft.local_shading ?? 0.35,
    clip: draft.highlight_clip
      ? { knee: draft.clip_knee ?? 0.72, max: draft.clip_max ?? 0.98 }
      : null,
  }
}

export function isOverridden(params: RegionParams, id: ParamDesc['id']): boolean {
  return params[id] !== null
}

/** 每个参数在两种卡片里都应出现的控件 id（覆盖开关不计）。 */
export function controlIds(desc: ParamDesc): string[] {
  return desc.kind === 'clip' ? ['enabled', 'knee', 'max'] : ['range', 'value']
}

export function clipOrDefault(value: RegionClip | null): RegionClip {
  return value ?? { knee: CLIP_DESC.knee.fallback, max: CLIP_DESC.max.fallback }
}
