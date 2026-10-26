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

export const FLOW_STEPS: Record<Flow, StepDef[]> = {
  patch: [PICK, EXTRACT, KEYFRAME, GENERATE, MERGE, COMPARE],
  fullredraw: [PAD, GENERATE, RECOMPOSE, COMPARE],
  gen: [GENERATE],
}

export const STEP_LABELS: Record<string, string> = {
  ...Object.fromEntries(
    [PICK, EXTRACT, KEYFRAME, GENERATE, MERGE, COMPARE, PAD, RECOMPOSE].map((step) => [
      step.key,
      step.label,
    ]),
  ),
  library: '素材库',
}

export function stepsForFlow(flow: Flow): StepDef[] {
  return FLOW_STEPS[flow] ?? FLOW_STEPS.patch
}

export function stepLabel(key: string): string {
  return STEP_LABELS[key] ?? key
}
