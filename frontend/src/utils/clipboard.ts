/**
 * 浏览器剪贴板的最小封装（决策 92）。
 *
 * `navigator.clipboard` 只在安全上下文可用（localhost 或 https）；局域网 http
 * 打开页面时它是 undefined。写入再退一步试 `document.execCommand('copy')`，
 * 读取没有可用的降级路径，由调用方弹出「手动粘贴」对话框。
 */
export class ClipboardUnavailableError extends Error {
  constructor(message: string) {
    super(message)
    this.name = 'ClipboardUnavailableError'
  }
}

function legacyCopy(text: string): boolean {
  if (typeof document === 'undefined' || !document.body) return false
  if (typeof document.execCommand !== 'function') return false
  const area = document.createElement('textarea')
  area.value = text
  area.setAttribute('readonly', '')
  area.style.position = 'fixed'
  area.style.top = '-1000px'
  area.style.opacity = '0'
  document.body.appendChild(area)
  let ok = false
  try {
    area.select()
    ok = document.execCommand('copy')
  } catch {
    ok = false
  } finally {
    document.body.removeChild(area)
  }
  return ok
}

/** 写剪贴板；返回是否成功，失败时由调用方展示手动复制对话框。 */
export async function writeClipboard(text: string): Promise<boolean> {
  try {
    if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch {
    // 权限被拒或不是安全上下文：继续走 execCommand 兜底。
  }
  return legacyCopy(text)
}

/** 读剪贴板；不可用或权限被拒时抛专用的 ClipboardUnavailableError。 */
export async function readClipboard(): Promise<string> {
  if (typeof navigator === 'undefined' || !navigator.clipboard?.readText) {
    throw new ClipboardUnavailableError(
      '当前浏览器不提供剪贴板读取；请用 localhost 或 https 打开页面，或手动粘贴',
    )
  }
  try {
    return await navigator.clipboard.readText()
  } catch {
    throw new ClipboardUnavailableError('浏览器拒绝了剪贴板读取；请手动粘贴')
  }
}
