import { onUnmounted, ref } from 'vue'

export interface DragRect {
  css: { x: number; y: number; w: number; h: number }
  img: { x: number; y: number; w: number; h: number }
  frac: { x: number; y: number; w: number; h: number }
}

interface Point {
  css: { x: number; y: number }
  img: { x: number; y: number }
  frac: { x: number; y: number }
}

function norm(a: Point, b: Point): DragRect {
  const x = Math.min(a.css.x, b.css.x)
  const y = Math.min(a.css.y, b.css.y)
  return {
    css: { x, y, w: Math.abs(a.css.x - b.css.x), h: Math.abs(a.css.y - b.css.y) },
    img: {
      x: Math.min(a.img.x, b.img.x),
      y: Math.min(a.img.y, b.img.y),
      w: Math.abs(a.img.x - b.img.x),
      h: Math.abs(a.img.y - b.img.y),
    },
    frac: {
      x: Math.min(a.frac.x, b.frac.x),
      y: Math.min(a.frac.y, b.frac.y),
      w: Math.abs(a.frac.x - b.frac.x),
      h: Math.abs(a.frac.y - b.frac.y),
    },
  }
}

/**
 * 拖框坐标的唯一换算：事件 → getBoundingClientRect → CSS/图像/0..1 三套坐标。
 * 提交只使用 frac，避免预览分辨率与页面缩放影响结果。
 */
export function useInnerDrag(
  imageEl: () => HTMLImageElement | null,
  onCommit: (rect: DragRect) => void,
  onMove?: (rect: DragRect | null) => void,
) {
  const rect = ref<DragRect | null>(null)
  const dragging = ref(false)
  let start: Point | null = null

  function point(event: PointerEvent): Point | null {
    const img = imageEl()
    if (!img || !img.complete || !img.naturalWidth) return null
    const box = img.getBoundingClientRect()
    if (!box.width || !box.height) return null
    const sx = img.naturalWidth / box.width
    const sy = img.naturalHeight / box.height
    const cx = event.clientX - box.left
    const cy = event.clientY - box.top
    return {
      css: { x: cx, y: cy },
      img: { x: cx * sx, y: cy * sy },
      frac: { x: cx / box.width, y: cy / box.height },
    }
  }

  function move(event: PointerEvent): void {
    if (!dragging.value || !start) return
    const current = point(event)
    if (!current) return
    rect.value = norm(start, current)
    onMove?.(rect.value)
  }

  function up(event: PointerEvent): void {
    if (!dragging.value) return
    const origin = start
    const current = point(event)
    dragging.value = false
    start = null
    window.removeEventListener('pointermove', move)
    window.removeEventListener('pointerup', up)
    if (!current || !origin) {
      rect.value = null
      onMove?.(null)
      return
    }
    const result = norm(origin, current)
    if (result.img.w > 2 && result.img.h > 2) {
      rect.value = result
      onCommit(result)
    } else {
      rect.value = null
      onMove?.(null)
    }
  }

  function begin(event: PointerEvent): void {
    const first = point(event)
    if (!first) return
    event.preventDefault()
    start = first
    dragging.value = true
    rect.value = null
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
  }

  onUnmounted(() => {
    window.removeEventListener('pointermove', move)
    window.removeEventListener('pointerup', up)
  })

  return { begin, rect, dragging }
}
