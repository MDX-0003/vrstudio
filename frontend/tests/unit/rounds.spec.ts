import { describe, expect, it } from 'vitest'
import { latestImageRound, roundResultText, roundResultType, visibleRounds } from '@/utils/rounds'
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
})
