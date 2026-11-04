import type { CaseInfo, CaseMeta } from '@/types/case-info'
import {
  ApiRequestError,
  forgetRevision,
  getJson,
  postJson,
  putJson,
  rememberRevision,
} from './client'
import { casePath } from './path'

export async function fetchCase(name: string): Promise<CaseInfo> {
  try {
    const data = await getJson<CaseInfo>(casePath(name))
    rememberRevision(name, data.meta?.revision ?? 0)
    return data
  } catch (err) {
    if (err instanceof ApiRequestError && err.status === 404) forgetRevision(name)
    throw err
  }
}

export function assignOwnerToMe(name: string): Promise<{ meta: CaseMeta }> {
  return postJson(`${casePath(name)}/owner`, {})
}

export function saveSubmitSelection(
  name: string,
  selected: string[],
  roles?: Record<string, string>,
): Promise<CaseInfo> {
  return putJson<CaseInfo>(`${casePath(name)}/submit`, { selected, roles })
}

export function changeAssetRole(name: string, id: string, role: string): Promise<CaseInfo> {
  return postJson<CaseInfo>(`${casePath(name)}/assets`, { op: 'role', id, role })
}

export function savePromptVar(name: string, key: string, value: string): Promise<CaseInfo> {
  return postJson<CaseInfo>(`${casePath(name)}/prompt`, { set: key, value })
}

export interface SaveParamsPayload {
  video_model_id?: number
  image_model_id?: number
  mode?: string
  aspect_ratio?: string
  resolution?: string
  scale?: string
  session_id?: number
  project_id?: number
  episode?: number
}

export function saveParams(name: string, payload: SaveParamsPayload): Promise<CaseInfo> {
  return postJson<CaseInfo>(`${casePath(name)}/params`, payload)
}

export type RunOperation =
  'dry_run' | 'submit' | 'adopt' | 'extract' | 'merge' | 'pad' | 'recompose' | 'relight'

export interface RunPayload {
  mode?: string
  aspect_ratio?: string
  resolution?: string
  scale?: string
  duration?: number
  round?: string
  allow_crop?: boolean
  /** 图片流程：候选张数 1..4。 */
  num?: number
  /** 图片流程：图片模型 id。 */
  image_model_id?: number
  /** 图片流程：项目生成会话 id（计费对象）。 */
  session_id?: number
  /** 图片流程 adopt：候选序号（从 1 开始）。 */
  index?: number
  /** relight：打光参数（与 CLI 同名）。 */
  scene?: 'night' | 'day'
  strength?: number
  local_shading?: number
  highlight_clip?: boolean
  clip_knee?: number
  clip_max?: number
  /** relight 视频：对齐与估光场用的帧（0 起）。 */
  ref_frame?: number
  /** relight 视频：编码质量。 */
  crf?: number
  /** relight 视频：只出 N 帧小样，不编码整段。 */
  sample?: number
  /** 视频生成：keep 保留结果音轨，none 下载后去音轨。 */
  audio?: 'keep' | 'none'
  /** 提交生成：放弃复用已有轮次，同提示词另开一轮（重新计费）。 */
  resubmit?: boolean
}

export function runOperation(
  name: string,
  op: RunOperation,
  payload: RunPayload = {},
): Promise<{ submitted: string; case: string }> {
  return postJson<{ submitted: string; case: string }>(`${casePath(name)}/run`, { op, ...payload })
}

export function addAssetsByUploads(
  name: string,
  uploadIds: string[],
  roles: string[],
): Promise<CaseInfo> {
  return postJson<CaseInfo>(`${casePath(name)}/assets`, {
    op: 'add',
    upload_ids: uploadIds,
    roles,
  })
}

export function removeAsset(name: string, id: string): Promise<CaseInfo> {
  return postJson<CaseInfo>(`${casePath(name)}/assets`, { op: 'remove', id })
}

/** relight 参数面板的持久值：写进 case.toml 的 [relight] 段。 */
export function saveRelightDraft(
  name: string,
  values: Record<string, string | number | boolean>,
): Promise<CaseInfo> {
  return postJson<CaseInfo>(`${casePath(name)}/relight`, values)
}

/** relight 的槽位：上传或指定一份文件，加一条候选并选中它。 */
export function setRelightSlot(
  name: string,
  role: 'relight_gt' | 'relight_atmo',
  target: { uploadId?: string; path?: string },
): Promise<CaseInfo> {
  return postJson<CaseInfo>(`${casePath(name)}/assets`, {
    op: 'slot',
    role,
    ...(target.uploadId ? { upload_ids: [target.uploadId] } : { path: target.path }),
  })
}

/** relight 的槽位：只改指针，选中库里已有的一条候选。 */
export function pickRelightSlot(
  name: string,
  role: 'relight_gt' | 'relight_atmo',
  assetId: string,
): Promise<CaseInfo> {
  return postJson<CaseInfo>(`${casePath(name)}/assets`, {
    op: 'slot',
    role,
    asset_id: assetId,
  })
}

/** 上传一个文件，加入素材库（角色 merge_input）并选作拼接输入。 */
export function uploadMergeInput(name: string, uploadId: string): Promise<CaseInfo> {
  return postJson<CaseInfo>(`${casePath(name)}/assets`, {
    op: 'merge-input',
    upload_ids: [uploadId],
  })
}

/** fullRedrawImg 的拼接输入：assetId 指向库里一张图；null 回退生成采用的结果。 */
export function setMergeInput(name: string, assetId: string | null): Promise<CaseInfo> {
  return postJson<CaseInfo>(`${casePath(name)}/assets`, {
    op: 'merge-input',
    asset_id: assetId ?? '',
  })
}

export function reorderAssets(name: string, ids: string[]): Promise<CaseInfo> {
  return postJson<CaseInfo>(`${casePath(name)}/assets`, { op: 'reorder', ids })
}
