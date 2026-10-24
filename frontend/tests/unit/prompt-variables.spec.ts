import { describe, expect, it } from 'vitest'
import { buildPromptVariables, promptVariablesToRecord, shouldExpirePreview } from '@/utils/prompt'
import type { PromptPayload } from '@/types/case-info'
import type { PromptVariable } from '@/types/case'

function payload(overrides: Partial<PromptPayload> = {}): PromptPayload {
  return {
    template: 'character-repaint',
    available: [],
    template_text: '',
    placeholders: ['target', 'subject', 'plot', 'canvas_w'],
    geometry_keys: ['canvas_w'],
    computed_keys: [],
    labels: { target: '重绘结果', subject: '原主体', plot: '情节' },
    help: { subject: '写画面里要被替换的主体' },
    vars: { plot: 'P', subject: 'S', extra: 'E' },
    rendered: '',
    render_error: null,
    ...overrides,
  }
}

describe('buildPromptVariables', () => {
  it('orders editable variables by template placeholders, not by object insertion order', () => {
    const rows = buildPromptVariables(payload())

    expect(rows.map((row) => row.key)).toEqual(['subject', 'plot', 'extra'])
  })

  it('uses template aliases and help without a frontend key map', () => {
    const rows = buildPromptVariables(payload())

    expect(rows[0]).toMatchObject({
      key: 'subject',
      label: '原主体',
      help: '写画面里要被替换的主体',
      value: 'S',
    })
    expect(rows[1]).toMatchObject({ key: 'plot', label: '情节' })
  })

  it('falls back to the key only when the template has no alias', () => {
    const rows = buildPromptVariables(payload())
    expect(rows[2]).toMatchObject({ key: 'extra', label: 'extra' })
  })

  it('returns an empty list without a prompt payload', () => {
    expect(buildPromptVariables(null)).toEqual([])
  })
})

describe('shouldExpirePreview', () => {
  it('only invalidates a passed preview when something was actually saved', () => {
    expect(shouldExpirePreview(false, '预演通过')).toBe(false)
    expect(shouldExpirePreview(true, '预演通过')).toBe(true)
    expect(shouldExpirePreview(true, '未预演')).toBe(false)
  })
})

describe('promptVariablesToRecord', () => {
  it('submits every editable variable instead of a hardcoded key allowlist', () => {
    const variables: PromptVariable[] = [
      { key: 'figure', label: '人物外观', help: '', value: 'a' },
      { key: 'new_pose', label: '新姿态', help: '', value: 'b' },
    ]
    expect(promptVariablesToRecord(variables)).toEqual({ figure: 'a', new_pose: 'b' })
  })
})
