export interface RoleOption {
  value: string
  label: string
  help: string
  legacy?: boolean
}

/** 图片步的参考图角色；detail_ref / lighting_ref 只存在于 keyframe.toml，不属于素材提交词表。 */
export const KEYFRAME_ROLE_OPTIONS: RoleOption[] = [
  { value: 'subject_ref', label: '人物三视图', help: '唯一外观依据；最多一张。' },
  {
    value: 'detail_ref',
    label: '细节参考',
    help: '只继承所指细节的外观，不取构图、背景与姿态。',
  },
  {
    value: 'lighting_ref',
    label: '氛围光影',
    help: '只读光源位置、色温、明暗对比与空气质感，不取构图、背景与画面内容。',
  },
]

export function defaultRole(kind: 'video' | 'image'): string {
  return kind === 'video' ? 'motion_ref' : 'subject_ref'
}
