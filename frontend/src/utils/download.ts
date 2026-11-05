/**
 * 把 `/file?root=…&p=…` 这类内联打开地址变成附件下载地址：追加 `download=1`，
 * 由后端以 `Content-Disposition: attachment` 下发。没有查询串时用 `?`，已有用 `&`。
 */
export function downloadHref(openUrl: string | null | undefined): string {
  if (!openUrl) return ''
  return `${openUrl}${openUrl.includes('?') ? '&' : '?'}download=1`
}
