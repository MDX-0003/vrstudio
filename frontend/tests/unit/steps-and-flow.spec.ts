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

  it('labels the case library view without adding it to flow steps', () => {
    expect(stepLabel('library')).toBe('素材库')
    expect(stepsForFlow('patch').map((step) => step.key)).not.toContain('library')
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

  it('uses the image step chains with their own merge labels', () => {
    expect(stepsForFlow('patchimg').map((step) => [step.key, step.label])).toEqual([
      ['pick', '选区'],
      ['extract', '抽取'],
      ['generate', '生成'],
      ['merge', '贴回'],
      ['compare', '对比'],
    ])
    expect(stepsForFlow('fullRedrawImg').map((step) => [step.key, step.label])).toEqual([
      ['extract', '抽取'],
      ['generate', '生成'],
      ['merge', '拼接'],
      ['compare', '对比'],
    ])
    // 全局标签保持「合并」，不被图片链改写。
    expect(stepLabel('merge')).toBe('合并')
  })

  it('returns the next image step with its own wording', () => {
    expect(nextStepForFlow('patchimg', {})).toEqual({
      key: 'pick',
      label: '等待选区',
      actionable: true,
    })
    expect(
      nextStepForFlow('patchimg', { pick: { ok: true } }),
    ).toEqual({ key: 'extract', label: '可以抽取', actionable: true })
    expect(
      nextStepForFlow('patchimg', {
        pick: { ok: true },
        extract: { ok: true },
        generate: { ok: false, ready: true },
      }),
    ).toEqual({ key: 'generate', label: '可以生成', actionable: true })
    expect(
      nextStepForFlow('patchimg', {
        pick: { ok: true },
        extract: { ok: true },
        generate: { ok: true, ready: true },
        merge: { ok: false, ready: false },
      }),
    ).toEqual({ key: 'generate', label: '等待选用', actionable: true })
    expect(
      nextStepForFlow('patchimg', {
        pick: { ok: true },
        extract: { ok: true },
        generate: { ok: true, ready: true },
        merge: { ok: false, ready: true },
      }),
    ).toEqual({ key: 'merge', label: '可以贴回', actionable: true })
    expect(
      nextStepForFlow('patchimg', {
        pick: { ok: true },
        extract: { ok: true },
        generate: { ok: true, ready: true },
        merge: { ok: true, ready: true },
      }),
    ).toEqual({ key: 'compare', label: '可以对比', actionable: true })

    expect(nextStepForFlow('fullRedrawImg', {})).toEqual({
      key: 'extract',
      label: '可以抽取',
      actionable: true,
    })
    expect(
      nextStepForFlow('fullRedrawImg', {
        extract: { ok: true },
        generate: { ok: true, ready: true },
        merge: { ok: false, ready: true },
      }),
    ).toEqual({ key: 'merge', label: '可以拼接', actionable: true })
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

  it('uses the relight chain: assets → relight → compare', () => {
    expect(stepsForFlow('relight').map((step) => [step.key, step.label])).toEqual([
      ['assets', '素材'],
      ['relight', '重打光'],
      ['compare', '对比'],
    ])
    expect(nextStepForFlow('relight', {})).toEqual({
      key: 'assets',
      label: '等待选定底版与氛围依据',
      actionable: true,
    })
    expect(nextStepForFlow('relight', { assets: { ok: true } })).toEqual({
      key: 'relight',
      label: '可以重打光',
      actionable: true,
    })
    expect(
      nextStepForFlow('relight', { assets: { ok: true }, relight: { ok: true } }),
    ).toEqual({ key: 'compare', label: '可以对比', actionable: true })
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
