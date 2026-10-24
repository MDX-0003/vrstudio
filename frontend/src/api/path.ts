export function casePath(name: string): string {
  return `/api/case/${encodeURIComponent(name)}`
}
