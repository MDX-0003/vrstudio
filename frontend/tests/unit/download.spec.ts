import { describe, expect, it } from 'vitest'
import { downloadHref } from '@/utils/download'

describe('downloadHref', () => {
  it('appends download=1 to an open_url that already has a query string', () => {
    expect(downloadHref('/file?root=cases&p=a%2Fout%2Fx.mp4')).toBe(
      '/file?root=cases&p=a%2Fout%2Fx.mp4&download=1',
    )
  })

  it('starts the query string when the url has none', () => {
    expect(downloadHref('/file')).toBe('/file?download=1')
  })

  it('returns an empty string when there is no url', () => {
    expect(downloadHref('')).toBe('')
    expect(downloadHref(null)).toBe('')
    expect(downloadHref(undefined)).toBe('')
  })
})
