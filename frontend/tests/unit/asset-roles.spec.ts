import { describe, expect, it } from 'vitest'
import {
  ASSET_ROLE_LABELS,
  IMAGE_ROLE_OPTIONS,
  VIDEO_ROLE_OPTIONS,
  defaultRole,
  roleOptionsFor,
} from '@/constants/assetRoles'

describe('asset roles', () => {
  it('offers lighting_ref for images only', () => {
    const videoValues = VIDEO_ROLE_OPTIONS.map((option) => option.value)
    expect(videoValues).not.toContain('lighting_ref')

    const lighting = IMAGE_ROLE_OPTIONS.find((option) => option.value === 'lighting_ref')
    expect(lighting?.label).toBe('光照参考')
    expect(lighting?.help).toContain('不提供构图')
    expect(ASSET_ROLE_LABELS.lighting_ref).toBe('光照参考')
  })

  it('picks the dropdown options by asset kind and mode', () => {
    const imageValues = roleOptionsFor('image', 'videoEdit').map((option) => option.value)
    const videoValues = roleOptionsFor('video', 'videoEdit').map((option) => option.value)
    expect(imageValues).toContain('lighting_ref')
    expect(videoValues).not.toContain('lighting_ref')

    const firstLast = roleOptionsFor('image', 'firstLastFrame').map((option) => option.value)
    expect(firstLast).toEqual(['first_frame', 'last_frame'])
  })

  it('keeps subject_ref as the default image role', () => {
    expect(defaultRole('image')).toBe('subject_ref')
    expect(defaultRole('video')).toBe('motion_ref')
  })
})
