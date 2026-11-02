import type { Flow } from '@/types/case'

export interface StepDef {
  key: string
  label: string
}

const PICK: StepDef = { key: 'pick', label: '选区' }
const EXTRACT: StepDef = { key: 'extract', label: '抽取' }
const KEYFRAME: StepDef = { key: 'keyframe', label: '首尾帧' }
const GENERATE: StepDef = { key: 'generate', label: '生成' }
const MERGE: StepDef = { key: 'merge', label: '合并' }
const COMPARE: StepDef = { key: 'compare', label: '对比' }
const PAD: StepDef = { key: 'pad', label: '扩充画面' }
const RECOMPOSE: StepDef = { key: 'recompose', label: '裁回原尺寸' }
const ASSETS: StepDef = { key: 'assets', label: '素材' }
const RELIGHT: StepDef = { key: 'relight', label: '重打光' }

export const FLOW_STEPS: Record<Flow, StepDef[]> = {
  patch: [PICK, EXTRACT, KEYFRAME, GENERATE, MERGE, COMPARE],
  fullredraw: [PAD, GENERATE, RECOMPOSE, COMPARE],
  gen: [GENERATE],
  // 图片局部重绘：没有时间轴与首尾帧，merge 在页面里叫「贴回」。
  patchimg: [PICK, EXTRACT, GENERATE, { key: 'merge', label: '贴回' }, COMPARE],
  // 图片全幅重绘：整幅 pad 之后直接生成，没有选区步；merge 叫「拼接」。
  fullRedrawImg: [EXTRACT, GENERATE, { key: 'merge', label: '拼接' }, COMPARE],
  // 重打光：素材步决定 GT 与氛围图，媒体类型由 GT 决定；不调平台、没有采用步。
  relight: [ASSETS, RELIGHT, COMPARE],
}

export const STEP_LABELS: Record<string, string> = {
  ...Object.fromEntries(
    [PICK, EXTRACT, KEYFRAME, GENERATE, MERGE, COMPARE, PAD, RECOMPOSE, ASSETS, RELIGHT].map(
      (step) => [step.key, step.label],
    ),
  ),
  library: '素材库',
}

export function stepsForFlow(flow: Flow): StepDef[] {
  return FLOW_STEPS[flow] ?? FLOW_STEPS.patch
}

export function stepLabel(key: string): string {
  return STEP_LABELS[key] ?? key
}
