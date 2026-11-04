import type { PromptVariable } from '@/types/case'
import type { PromptPayload } from '@/types/case-info'

export interface PromptVariableOptions {
  /**
   * 同时列出模板引用、草稿里还没有值的可编辑变量（生成步用）。
   * 新 case 没有 prompt-vars 草稿时，靠这些空位让用户有地方填写，
   * 而不是整块只剩一条「展不开」。几何键与自动键不在这里出现。
   */
  includeUndefined?: boolean
}

/**
 * 可编辑变量按模板占位符顺序排列；不在模板里的历史变量排在最后。
 * 前端不维护 key 到中文的映射，显示名与说明都来自 prompt.labels / prompt.help。
 */
export function buildPromptVariables(
  prompt?: PromptPayload | null,
  options: PromptVariableOptions = {},
): PromptVariable[] {
  if (!prompt) return []
  const values = prompt.vars ?? {}
  const placeholders = prompt.placeholders ?? []
  const structural = new Set([...(prompt.geometry_keys ?? []), ...(prompt.computed_keys ?? [])])
  const keys = [
    ...placeholders,
    ...Object.keys(values).filter((key) => !placeholders.includes(key)),
  ]
  const rows: PromptVariable[] = []
  for (const key of keys) {
    const defined = key in values
    if (!defined && (!options.includeUndefined || structural.has(key))) continue
    rows.push({
      key,
      label: prompt.labels?.[key] || key,
      help: prompt.help?.[key] || '',
      value: defined ? (values[key] ?? '') : '',
      defined,
    })
  }
  return rows
}

export function promptVariableFilled(variable: PromptVariable): boolean {
  return variable.value.trim().length > 0
}

/** 保存时按页面当前变量原样提交，不再维护第二份 key 白名单。 */
export function promptVariablesToRecord(variables: PromptVariable[]): Record<string, string> {
  return Object.fromEntries(variables.map((item) => [item.key, item.value]))
}

/** 只有真正保存了改动，才需要让上一次预演失效。 */
export function shouldExpirePreview(hadPending: boolean, status: string): boolean {
  return hadPending && status === '预演通过'
}
