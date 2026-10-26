import { describe, expect, it } from 'vitest'
import { nextStepForFlow } from '@/utils/caseFlow'
import { gateBlocked } from '@/utils/gates'
import { stepLabel, stepsForFlow } from '@/constants/steps'

describe('flow steps', () => {
  it('includes pad and recompose in the full redraw flow', () => {
    expect(stepsForFlow('fullredraw').map((step) => step.key)).toEqual([
      'pad',
      'generate',
      'recompose',
      'compare',
    ])
    expect(stepLabel('pad')).toBe('扩充画面')
    expect(stepLabel('recompose')).toBe('裁回原尺寸')
  })

  it('uses ready for actionability and ok for completion', () => {
    expect(
      nextStepForFlow('patch', {
        pick: { ok: true },
        extract: { ok: true },
        generate: { ok: false, ready: true },
      }),
    ).toEqual({ key: 'generate', label: '可以生成', actionable: true })

    expect(
      nextStepForFlow('patch', {
        pick: { ok: true },
        extract: { ok: true },
        generate: { ok: true, ready: true },
        merge: { ok: false, ready: false },
      }),
    ).toEqual({ key: 'generate', label: '等待选用成片', actionable: true })

    expect(
      nextStepForFlow('patch', {
        pick: { ok: true },
        extract: { ok: true },
        generate: { ok: true, ready: true },
        merge: { ok: false, ready: true },
      }),
    ).toEqual({ key: 'merge', label: '可以合并', actionable: true })
  })

  it('returns the next full redraw step with Chinese labels', () => {
    expect(nextStepForFlow('fullredraw', { pad: { ok: false } })).toEqual({
      key: 'pad',
      label: '可以扩充画面',
      actionable: true,
    })
    expect(
      nextStepForFlow('fullredraw', {
        pad: { ok: true },
        generate: { ok: false, ready: true },
      }),
    ).toEqual({ key: 'generate', label: '可以生成', actionable: true })
    expect(
      nextStepForFlow('fullredraw', {
        pad: { ok: true },
        generate: { ok: true, ready: true },
        recompose: { ok: false, ready: true },
      }),
    ).toEqual({ key: 'recompose', label: '可以裁回尺寸', actionable: true })
  })

  it('handles gen flow without an assets step', () => {
    expect(nextStepForFlow('gen', { generate: { ok: false, ready: false } })).toEqual({
      key: 'generate',
      label: '等待素材',
      actionable: false,
    })
    expect(nextStepForFlow('gen', { generate: { ok: false, ready: true } })).toEqual({
      key: 'generate',
      label: '可以生成',
      actionable: true,
    })
    expect(nextStepForFlow('gen', { generate: { ok: true, ready: true } })).toEqual({
      key: 'generate',
      label: '已完成',
      actionable: false,
    })
  })
})

describe('gateBlocked', () => {
  it('prefers ready over ok when both exist', () => {
    expect(gateBlocked(undefined)).toBe(false)
    expect(gateBlocked({ ok: true })).toBe(false)
    expect(gateBlocked({ ok: false, reason: '缺素材' })).toBe(true)
    expect(gateBlocked({ ok: false, ready: true })).toBe(false)
    expect(gateBlocked({ ok: true, ready: false })).toBe(true)
  })
})
