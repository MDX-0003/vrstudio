import type { FrameRange, Inner, RangePreset, Viewport } from '@/types/case-info'

export function formatRangeTime(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value || '—'
  const pad = (n: number): string => String(n).padStart(2, '0')
  return (
    `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ` +
    `${pad(date.getHours())}:${pad(date.getMinutes())}`
  )
}

export function geometrySummary(
  viewport: Viewport,
  inner: Inner,
  frames: FrameRange | null,
): string {
  const parts = [
    `yaw ${viewport.yaw_deg.toFixed(1)}°`,
    `pitch ${viewport.pitch_deg.toFixed(1)}°`,
    `fov ${viewport.fov_h_deg.toFixed(1)}°`,
    `inner ${inner.width}×${inner.height}`,
  ]
  if (frames) parts.push(`帧 ${frames.start}–${frames.end}`)
  return parts.join(' · ')
}

export function presetSummary(preset: RangePreset): string {
  return geometrySummary(preset.viewport, preset.inner, preset.frames)
}

export function sameGeometry(
  aViewport: Viewport,
  aInner: Inner,
  aFrames: FrameRange | null,
  bViewport: Viewport,
  bInner: Inner,
  bFrames: FrameRange | null,
): boolean {
  return (
    aViewport.yaw_deg === bViewport.yaw_deg &&
    aViewport.pitch_deg === bViewport.pitch_deg &&
    aViewport.fov_h_deg === bViewport.fov_h_deg &&
    aViewport.width === bViewport.width &&
    aViewport.height === bViewport.height &&
    aInner.x === bInner.x &&
    aInner.y === bInner.y &&
    aInner.width === bInner.width &&
    aInner.height === bInner.height &&
    (aFrames?.start ?? null) === (bFrames?.start ?? null) &&
    (aFrames?.end ?? null) === (bFrames?.end ?? null)
  )
}
