import { describe, expect, it } from 'vitest'
import { aggregateSaveState } from '@/utils/saveState'

describe('aggregateSaveState', () => {
  it('shows saving while any part is busy, then returns to the prompt state', () => {
    expect(aggregateSaveState('saved', true)).toBe('saving')
    expect(aggregateSaveState('unsaved', true)).toBe('saving')
    expect(aggregateSaveState('unsaved', false)).toBe('unsaved')
    expect(aggregateSaveState('saved', false)).toBe('saved')
  })
})
