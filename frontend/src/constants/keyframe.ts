export const KEYFRAME_RESOLUTIONS = [
  { value: '1K', label: '1K' },
  { value: '2K', label: '2K' },
  { value: '4K', label: '4K' },
] as const

export const KEYFRAME_MAX_CANDIDATES = 4

/** 图片步单次最多 10 张输入图：底图 + 参考图 +（尾帧）首帧定稿。 */
export const KEYFRAME_MAX_IMAGES = 10

/** 当前步骤用户可添加的参考图上限：首帧9 张，尾帧8 张。 */
export function keyframeMaxRefs(step: 'first' | 'last'): number {
  return KEYFRAME_MAX_IMAGES - (step === 'last' ? 2 : 1)
}
