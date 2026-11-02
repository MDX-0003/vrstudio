import type { Flow } from '@/types/case'
import type { Gates } from '@/types/case'

export interface NextStep {
  key: string
  label: string
  actionable: boolean
}

/**
 * 列表页只拿到 gates，没有完整 case。这里按流程回答“下一步该去哪”。
 * 生成步用 ready 判断能不能做，用 ok 判断有没有结果；不要混用。
 */
export function nextStepForFlow(flow: Flow, gates: Gates = {}): NextStep {
  if (flow === 'gen') {
    const generate = gates.generate
    if (generate?.ok) return { key: 'generate', label: '已完成', actionable: false }
    if (generate?.ready === false) return { key: 'generate', label: '等待素材', actionable: false }
    return { key: 'generate', label: '可以生成', actionable: true }
  }

  if (flow === 'fullredraw') {
    if (!gates.pad?.ok) return { key: 'pad', label: '可以扩充画面', actionable: true }
    if (!gates.generate?.ok) {
      return gates.generate?.ready
        ? { key: 'generate', label: '可以生成', actionable: true }
        : { key: 'generate', label: '等待生成', actionable: false }
    }
    if (!gates.recompose?.ok) {
      return gates.recompose?.ready
        ? { key: 'recompose', label: '可以裁回尺寸', actionable: true }
        : { key: 'recompose', label: '等待裁回尺寸', actionable: false }
    }
    return { key: 'compare', label: '可以对比', actionable: true }
  }

  if (flow === 'relight') {
    if (!gates.assets?.ok) return { key: 'assets', label: '等待选定底版与氛围图', actionable: true }
    if (!gates.relight?.ok) return { key: 'relight', label: '可以重打光', actionable: true }
    return { key: 'compare', label: '可以对比', actionable: true }
  }

  if (flow === 'patchimg' || flow === 'fullRedrawImg') {
    const patchimg = flow === 'patchimg'
    if (patchimg && !gates.pick?.ok) return { key: 'pick', label: '等待选区', actionable: true }
    if (!gates.extract?.ok) return { key: 'extract', label: '可以抽取', actionable: true }
    // 图片流程生成结果的完成标志是「采用了一张候选」，不是任务跑完。
    if (!gates.generate?.ok) {
      return gates.generate?.ready
        ? { key: 'generate', label: '可以生成', actionable: true }
        : { key: 'generate', label: '等待选用', actionable: false }
    }
    if (!gates.merge?.ready) {
      return { key: 'generate', label: '等待选用', actionable: true }
    }
    if (!gates.merge?.ok) {
      return { key: 'merge', label: patchimg ? '可以贴回' : '可以拼接', actionable: true }
    }
    return { key: 'compare', label: '可以对比', actionable: true }
  }

  if (!gates.pick?.ok) return { key: 'pick', label: '等待选区', actionable: true }
  if (!gates.extract?.ok) return { key: 'extract', label: '可以抽取', actionable: true }

  if (!gates.generate?.ok) {
    return gates.generate?.ready
      ? { key: 'generate', label: '可以生成', actionable: true }
      : { key: 'generate', label: '等待生成', actionable: false }
  }

  if (!gates.merge?.ready) {
    return { key: 'generate', label: '等待选用成片', actionable: true }
  }
  if (!gates.merge?.ok) return { key: 'merge', label: '可以合并', actionable: true }
  return { key: 'compare', label: '可以对比', actionable: true }
}
