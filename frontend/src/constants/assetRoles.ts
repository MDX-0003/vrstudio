export const ASSET_ROLE_LABELS: Record<string, string> = {
  edit_master: '编辑母版',
  motion_ref: '动作参考',
  scene_ref: '场景参考',
  subject_ref: '主体参考',
  first_frame: '首帧定稿',
  last_frame: '尾帧定稿',
  mid_frame: '中间帧',
  clip: '流程母版',
  pad: '扩充画布',
}

export const ASSET_ROLE_OPTIONS = [
  { value: 'edit_master', label: '编辑母版' },
  { value: 'motion_ref', label: '动作参考' },
  { value: 'scene_ref', label: '场景参考' },
  { value: 'subject_ref', label: '主体参考' },
  { value: 'first_frame', label: '首帧定稿' },
  { value: 'last_frame', label: '尾帧定稿' },
  { value: 'mid_frame', label: '中间帧' },
]

export function defaultRole(kind: 'video' | 'image'): string {
  return kind === 'video' ? 'motion_ref' : 'subject_ref'
}
