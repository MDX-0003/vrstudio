import type { Flow } from '@/types/case'

export const flowMeta: Record<Flow, { label: string; tag: 'primary' | 'success' | 'warning' }> = {
  patch: { label: '局部重绘', tag: 'primary' },
  fullredraw: { label: '全流程重绘', tag: 'success' },
  gen: { label: '简单生成', tag: 'warning' },
}
