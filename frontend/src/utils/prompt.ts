import type { PromptVariable } from '@/types/case'
import type { PromptPayload } from '@/types/case-info'

/**
 * 可编辑变量按模板占位符顺序排列；不在模板里的历史变量排在最后。
 * 前端不维护 key 到中文的映射，显示名与说明都来自 prompt.labels / prompt.help。
 */
export function buildPromptVariables(prompt?: PromptPayload | null): PromptVariable[] {
  if (!prompt) return []
  const values = prompt.vars ?? {}
  const keys = Object.keys(values)
  const ordered = [
    ...(prompt.placeholders ?? []).filter((key) => key in values),
    ...keys.filter((key) => !(prompt.placeholders ?? []).includes(key)),
  ]
  return ordered.map((key) => ({
    key,
    label: prompt.labels?.[key] || key,
    help: prompt.help?.[key] || '',
    value: values[key] ?? '',
  }))
}

export function promptVariableFilled(variable: PromptVariable): boolean {
  return variable.value.trim().length > 0
}

/** 只有真正保存了改动，才需要让上一次预演失效。 */
export function shouldExpirePreview(hadPending: boolean, status: string): boolean {
  return hadPending && status === '预演通过'
}
