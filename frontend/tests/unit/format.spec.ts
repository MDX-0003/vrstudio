import { describe, expect, it } from 'vitest'
import { formatSize } from '@/utils/format'

describe('formatSize', () => {
  it('formats bytes and larger units', () => {
    expect(formatSize(null)).toBe('—')
    expect(formatSize(undefined)).toBe('—')
    expect(formatSize(0)).toBe('0 B')
    expect(formatSize(1024)).toBe('1.0 KB')
    expect(formatSize(1024 * 1024)).toBe('1.0 MB')
    expect(formatSize(1024 * 1024 * 1024)).toBe('1.0 GB')
  })
})
