import { describe, expect, it } from 'vitest'
import {
  adoptedImageRound,
  imageRoundStatus,
  latestImageRound,
  latestResultRound,
  roundResultText,
  roundResultType,
  visibleRounds,
} from '@/utils/rounds'
import type { ImageRound, RoundSummary } from '@/types/case-info'

function round(overrides: Partial<RoundSummary> = {}): RoundSummary {
  return {
    mark: '*',
    round: 'r1',
    backend: 'mglc',
    stage: 'done',
    gate: 'ok',
    result: 'yes',
    updated: '2026-10-24T00:00:00',
    round_dir: 'cases/p1/runs/r1',
    ...overrides,
  }
}

describe('rounds', () => {
  it('keeps prepared rounds out of the result list', () => {
    const rows = [
      round({ round: 'prepared', stage: 'prepared', result: 'no' }),
      round({ round: 'usable', stage: 'done', result: 'yes' }),
    ]
    expect(visibleRounds(rows).map((item) => item.round)).toEqual(['usable'])
  })

  it('uses plain-language result labels', () => {
    expect(roundResultText(round({ result: 'yes' }))).toBe('可用')
    expect(roundResultText(round({ stage: 'failed', result: 'no' }))).toBe('生成失败')
    expect(roundResultText(round({ stage: 'prepared', result: 'no' }))).toBe('仅预演')
    expect(roundResultText(round({ stage: 'done', result: 'no' }))).toBe('未出片')
    expect(roundResultType(round({ stage: 'failed', result: 'no' }))).toBe('danger')
  })

  it('picks the newest image round and tolerates an empty list', () => {
    const rounds: ImageRound[] = [
      { round: 'img-b', adopted: false, results: [] },
      { round: 'img-a', adopted: true, results: [] },
    ]
    expect(latestImageRound(rounds)?.round).toBe('img-b')
    expect(latestImageRound([])).toBeNull()
    expect(latestImageRound(undefined)).toBeNull()
  })

  it('picks the newest round with results, ignoring an empty prepared round', () => {
    const rounds: ImageRound[] = [
      { round: 'img-empty', stage: 'prepared', adopted: false, results: [] },
      { round: 'img-a', stage: 'checked', adopted: false, results: [{ index: 1, name: 'c1.png', exists: true, url: '/u' }] },
    ]
    expect(latestResultRound(rounds)?.round).toBe('img-a')
    expect(latestResultRound([])).toBeNull()
  })

  it('reports the adopted round even when it is not the newest', () => {
    const rounds: ImageRound[] = [
      { round: 'img-b', stage: 'checked', adopted: false, results: [{ index: 1, name: 'c1.png', exists: true, url: '/u' }] },
      { round: 'img-a', stage: 'checked', adopted: true, results: [{ index: 1, name: 'c1.png', exists: true, url: '/u' }] },
    ]
    expect(adoptedImageRound(rounds)?.round).toBe('img-a')
    expect(adoptedImageRound([])).toBeNull()
  })

  it('derives the image status from the latest round', () => {
    const withResults: ImageRound[] = [
      { round: 'img-a', stage: 'checked', adopted: false, results: [{ index: 1, name: 'c1.png', exists: true, url: '/u' }] },
    ]
    expect(imageRoundStatus(withResults)).toBe('已完成')
    expect(
      imageRoundStatus([
        { round: 'img-empty', stage: 'prepared', adopted: false, results: [] },
        ...withResults,
      ]),
    ).toBe('已完成')
    expect(imageRoundStatus([{ round: 'img-x', stage: 'failed', adopted: false, results: [] }])).toBe('失败')
    expect(imageRoundStatus([{ round: 'img-y', stage: 'prepared', adopted: false, results: [] }])).toBe('仅预演')
    expect(imageRoundStatus([])).toBe('未预演')
    expect(imageRoundStatus(undefined)).toBe('未预演')
  })
})
