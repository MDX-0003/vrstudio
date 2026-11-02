import type { Flow } from '@/types/case'

export const flowMeta: Record<Flow, { label: string; tag: 'primary' | 'success' | 'warning' }> = {
  patch: { label: '视频·局部重绘', tag: 'primary' },
  fullredraw: { label: '视频·全画幅重绘', tag: 'success' },
  gen: { label: '简单生成', tag: 'warning' },
  patchimg: { label: '图片·局部重绘', tag: 'primary' },
  fullRedrawImg: { label: '图片·全幅重绘', tag: 'success' },
  relight: { label: '重打光', tag: 'warning' },
}

/** 流程筛选与表格 tag 共用同一份词表：加流程只改 flowMeta。 */
export const flowOptions: { value: Flow; label: string }[] = (
  Object.keys(flowMeta) as Flow[]
).map((value) => ({ value, label: flowMeta[value].label }))
