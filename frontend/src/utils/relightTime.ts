import type { TimeRange } from '@/types/case-info'

/**
 * 时间轴纯函数：吸附、摘要与拖动夹取（决策 91）。
 *
 * 这里只保证「同一条区域轨道上不重叠、至少一帧」这条界面不变量；区间并集在解析端
 * 完成，前端不复制那段算法。相接的两段在这里合并成一段，与引擎的并集规则一致。
 */

const EPS = 1e-9

/** 与解析端 ``MAX_RANGES_PER_REGION`` 对齐：一条区域最多 8 段。 */
export const MAX_RANGES = 8

export function cleanSeconds(seconds: number): number {
  return Number(seconds.toFixed(6))
}

export function snapToFrame(seconds: number, fps: number | null | undefined): number {
  if (!fps || fps <= 0) return cleanSeconds(seconds)
  return cleanSeconds(Math.round(seconds * fps) / fps)
}

export function minRangeSeconds(fps: number | null | undefined): number {
  return fps && fps > 0 ? 1 / fps : 1e-3
}

export function formatClock(seconds: number): string {
  const safe = Math.max(0, seconds)
  const minutes = Math.floor(safe / 60)
  return `${minutes}:${(safe - minutes * 60).toFixed(1).padStart(4, '0')}`
}

export function rangeSummary(ranges: TimeRange[] | null): string {
  if (!ranges || !ranges.length) return '整段'
  if (ranges.length === 1) {
    return `${formatClock(ranges[0].start)}–${formatClock(ranges[0].end)}`
  }
  return `${ranges.length} 段 · ${formatClock(ranges[0].start)}–${formatClock(ranges[0].end)} 等`
}

/** 排序并合并相接或重叠的区间；只在提交一次拖动后调用。 */
export function mergeAdjacent(ranges: TimeRange[]): TimeRange[] {
  const ordered = [...ranges].sort((a, b) => a.start - b.start)
  const out: TimeRange[] = []
  for (const item of ordered) {
    const last = out[out.length - 1]
    if (last && item.start <= last.end + EPS) {
      last.end = Math.max(last.end, item.end)
    } else {
      out.push({ start: item.start, end: item.end })
    }
  }
  return out
}

function lowerBound(ranges: TimeRange[], index: number): number {
  return index > 0 ? ranges[index - 1].end : 0
}

function upperBound(ranges: TimeRange[], index: number, duration: number): number {
  return index < ranges.length - 1 ? ranges[index + 1].start : duration
}

function replace(ranges: TimeRange[], index: number, item: TimeRange): TimeRange[] {
  return ranges.map((current, position) => (position === index ? item : current))
}

/** 平移一条区间，保持长度，夹在相邻区间与片长之间。 */
export function moveRange(ranges: TimeRange[], index: number, delta: number,
                          duration: number, fps: number | null | undefined): TimeRange[] {
  const item = ranges[index]
  if (!item) return ranges
  const length = item.end - item.start
  const lower = lowerBound(ranges, index)
  const upper = Math.max(lower, upperBound(ranges, index, duration) - length)
  const start = clamp(snapToFrame(item.start + delta, fps), lower, upper)
  return replace(ranges, index, {
    start: cleanSeconds(start),
    end: cleanSeconds(Math.min(start + length, duration)),
  })
}

/** 拖动一端改起止；最短一帧，不越过相邻区间。 */
export function resizeRange(ranges: TimeRange[], index: number,
                            edge: 'start' | 'end', delta: number,
                            duration: number,
                            fps: number | null | undefined): TimeRange[] {
  const item = ranges[index]
  if (!item) return ranges
  const shortest = minRangeSeconds(fps)
  if (edge === 'start') {
    const start = clamp(snapToFrame(item.start + delta, fps),
                        lowerBound(ranges, index), item.end - shortest)
    return replace(ranges, index, { start: cleanSeconds(start), end: item.end })
  }
  const end = clamp(snapToFrame(item.end + delta, fps),
                    item.start + shortest,
                    upperBound(ranges, index, duration))
  return replace(ranges, index, { start: item.start, end: cleanSeconds(end) })
}

/** 在空白位置新建一段（默认 1 秒）；放不下时原样返回。 */
export function insertRange(ranges: TimeRange[], at: number, duration: number,
                            fps: number | null | undefined): TimeRange[] {
  if (duration <= 0 || ranges.length >= MAX_RANGES) return ranges
  if (ranges.some((item) => at > item.start && at < item.end)) return ranges

  const shortest = minRangeSeconds(fps)
  let start = clamp(snapToFrame(at, fps), 0, Math.max(0, duration - shortest))
  let end = Math.min(cleanSeconds(start + 1), duration)
  if (end - start < shortest) end = Math.min(duration, start + shortest)

  let index = ranges.findIndex((item) => item.start > start)
  if (index < 0) index = ranges.length
  start = Math.max(start, lowerBound(ranges, index))
  end = Math.min(end, upperBound(ranges, index, duration))
  if (end - start < shortest - EPS) return ranges

  const item = { start: cleanSeconds(start), end: cleanSeconds(end) }
  return [...ranges.slice(0, index), item, ...ranges.slice(index)]
}

/** 删除一条区间；删空由调用方换成 ``null``（整段）。 */
export function removeRange(ranges: TimeRange[], index: number): TimeRange[] {
  return ranges.filter((_, position) => position !== index)
}

export function clamp(value: number, low: number, high: number): number {
  return Math.max(low, Math.min(high, value))
}
