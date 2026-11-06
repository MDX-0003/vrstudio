import { describe, expect, it } from 'vitest'
import {
  MAX_RANGES,
  formatClock,
  insertRange,
  mergeAdjacent,
  moveRange,
  rangeSummary,
  removeRange,
  resizeRange,
  snapToFrame,
} from '@/utils/relightTime'

describe('relightTime', () => {
  it('formats clock and summaries', () => {
    expect(formatClock(8)).toBe('0:08.0')
    expect(formatClock(75.5)).toBe('1:15.5')
    expect(rangeSummary(null)).toBe('整段')
    expect(rangeSummary([])).toBe('整段')
    expect(rangeSummary([{ start: 8, end: 15 }])).toBe('0:08.0–0:15.0')
    expect(rangeSummary([{ start: 8, end: 15 }, { start: 22.5, end: 30 }]))
      .toBe('2 段 · 0:08.0–0:15.0 等')
  })

  it('snaps to frame boundaries with six decimals', () => {
    expect(snapToFrame(2.367, 30)).toBe(2.366667)
    expect(snapToFrame(2.34, 10)).toBe(2.3)
    expect(snapToFrame(2.34, null)).toBe(2.34)
  })

  it('merges touching or overlapping ranges', () => {
    expect(mergeAdjacent([{ start: 3, end: 4 }, { start: 0, end: 1 },
                          { start: 1, end: 2 }])).toEqual([
      { start: 0, end: 2 }, { start: 3, end: 4 }])
  })

  it('moves a range within its neighbours and the clip', () => {
    const ranges = [{ start: 2, end: 4 }, { start: 6, end: 8 }]
    expect(moveRange(ranges, 0, 3, 10, 10)).toEqual([
      { start: 4, end: 6 }, { start: 6, end: 8 }])
    expect(moveRange(ranges, 0, -5, 10, 10)).toEqual([
      { start: 0, end: 2 }, { start: 6, end: 8 }])
    expect(moveRange([{ start: 8, end: 10 }], 0, 5, 10, 10)).toEqual([
      { start: 8, end: 10 }])
  })

  it('resizes with a one-frame minimum', () => {
    expect(resizeRange([{ start: 2, end: 4 }], 0, 'start', 3, 10, 10)).toEqual([
      { start: 3.9, end: 4 }])
    expect(resizeRange([{ start: 2, end: 4 }], 0, 'end', -3, 10, 10)).toEqual([
      { start: 2, end: 2.1 }])
    expect(resizeRange([{ start: 0, end: 2 }, { start: 4, end: 6 }],
                       0, 'end', 5, 10, 10)).toEqual([
      { start: 0, end: 4 }, { start: 4, end: 6 }])
  })

  it('inserts a range in empty space and refuses when there is no room', () => {
    expect(insertRange([], 5, 10, 10)).toEqual([{ start: 5, end: 6 }])
    expect(insertRange([{ start: 0, end: 1 }, { start: 6, end: 10 }],
                       5, 10, 10)).toEqual([
      { start: 0, end: 1 }, { start: 5, end: 6 }, { start: 6, end: 10 }])

    const tight = [{ start: 0, end: 4.9 }, { start: 5, end: 10 }]
    expect(insertRange(tight, 4.95, 10, 10)).toBe(tight)
    const inside = [{ start: 0, end: 4 }]
    expect(insertRange(inside, 2, 10, 10)).toBe(inside)
  })

  it('removes one range', () => {
    expect(removeRange([{ start: 0, end: 1 }, { start: 2, end: 3 }], 0))
      .toEqual([{ start: 2, end: 3 }])
    expect(removeRange([{ start: 0, end: 1 }], 0)).toEqual([])
  })

  it('exposes the same per-region limit as the parser', () => {
    expect(MAX_RANGES).toBe(8)
    const full = Array.from({ length: MAX_RANGES },
      (_, index) => ({ start: index * 2, end: index * 2 + 1 }))
    expect(insertRange(full, 20, 30, 10)).toBe(full)
  })
})
