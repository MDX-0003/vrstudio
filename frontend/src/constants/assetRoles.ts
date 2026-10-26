export const ASSET_ROLE_LABELS: Record<string, string> = {
  edit_master: '编辑母版',
  motion_ref: '动作参考',
  scene_ref: '场景参考',
  space_ref: '空间参考',
  subject_ref: '主体参考',
  first_frame: '首帧定稿',
  last_frame: '尾帧定稿',
  mid_frame: '中间帧',
  detail_ref: '细节参考',
  clip: '流程母版',
  pad: '扩充画布',
}

export interface RoleOption {
  value: string
  label: string
  help: string
  legacy?: boolean
}

/** 本轮开放的视频普通角色；scene_ref / space_ref / mid_frame 仍可由旧 case 读取。 */
export const VIDEO_ROLE_OPTIONS: RoleOption[] = [
  { value: 'edit_master', label: '编辑母版', help: '负责时间轴、构图与动作基准。' },
  { value: 'subject_ref', label: '主体参考', help: '只定义角色外观。' },
  { value: 'motion_ref', label: '动作参考', help: '只提供动作节奏与路径。' },
]

/** firstLastFrame 的模式专用角色；其它模式不显示。 */
export const FIRST_LAST_ROLE_OPTIONS: RoleOption[] = [
  {
    value: 'first_frame',
    label: '首帧定稿',
    help: '视频的第一帧，锁定起点构图与人物外观。',
  },
  {
    value: 'last_frame',
    label: '尾帧定稿',
    help: '视频的最后一帧，锁定终点位置、大小与朝向。',
  },
]

/** 图片步的参考图角色；detail_ref 只存在于 keyframe.toml。 */
export const KEYFRAME_ROLE_OPTIONS: RoleOption[] = [
  { value: 'subject_ref', label: '人物三视图', help: '唯一外观依据。' },
  {
    value: 'detail_ref',
    label: '细节参考',
    help: '只继承所指细节的外观，不取构图、背景与姿态。',
  },
]

const LEGACY_HELP: Record<string, string> = {
  scene_ref: '场景参考：只提供构图、背景与光照。',
  space_ref: '空间参考：只提供机位、构图、起点与终点位置，不提供人物动作。',
  mid_frame: '中间过渡帧：仅在首尾间距过大时补充。',
}

export function legacyRoleOption(role: string): RoleOption | null {
  if (!(role in LEGACY_HELP)) return null
  return {
    value: role,
    label: `${ASSET_ROLE_LABELS[role] ?? role}（旧角色）`,
    help: LEGACY_HELP[role],
    legacy: true,
  }
}

export function defaultRole(kind: 'video' | 'image'): string {
  return kind === 'video' ? 'motion_ref' : 'subject_ref'
}
