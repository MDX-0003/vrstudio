import { afterEach, describe, expect, it, vi } from 'vitest'
import { ClipboardUnavailableError, readClipboard, writeClipboard } from '@/utils/clipboard'

function setClipboard(value: unknown): void {
  Object.defineProperty(navigator, 'clipboard', {
    value,
    configurable: true,
  })
}

afterEach(() => {
  setClipboard(undefined)
  delete (document as unknown as { execCommand?: unknown }).execCommand
  vi.restoreAllMocks()
})

describe('writeClipboard', () => {
  it('writes through navigator.clipboard when it is available', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    setClipboard({ writeText })

    await expect(writeClipboard('{"a":1}')).resolves.toBe(true)
    expect(writeText).toHaveBeenCalledWith('{"a":1}')
  })

  it('falls back to execCommand when clipboard.writeText rejects', async () => {
    setClipboard({ writeText: vi.fn().mockRejectedValue(new Error('denied')) })
    const execCommand = vi.fn().mockReturnValue(true)
    ;(document as unknown as { execCommand: unknown }).execCommand = execCommand

    await expect(writeClipboard('{}')).resolves.toBe(true)
    expect(execCommand).toHaveBeenCalledWith('copy')
  })

  it('returns false when neither path is available', async () => {
    setClipboard(undefined)
    await expect(writeClipboard('{}')).resolves.toBe(false)
  })
})

describe('readClipboard', () => {
  it('returns the clipboard text', async () => {
    setClipboard({ readText: vi.fn().mockResolvedValue('hello') })
    await expect(readClipboard()).resolves.toBe('hello')
  })

  it('throws ClipboardUnavailableError when the API is missing', async () => {
    setClipboard(undefined)
    await expect(readClipboard()).rejects.toBeInstanceOf(ClipboardUnavailableError)
  })

  it('throws ClipboardUnavailableError when permission is denied', async () => {
    setClipboard({ readText: vi.fn().mockRejectedValue(new Error('denied')) })
    await expect(readClipboard()).rejects.toBeInstanceOf(ClipboardUnavailableError)
  })
})
