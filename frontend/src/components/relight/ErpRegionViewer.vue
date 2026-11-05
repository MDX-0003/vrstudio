<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import {
  ERP_FRAG_SRC,
  ERP_VERT_SRC,
  clampFov,
  clampPitch,
  densify,
  pointInPolygon,
  project,
  projectPolygon,
  unproject,
} from '@/utils/erpMath'
import type { LonLat, Pose, Vec2 } from '@/utils/erpMath'
import type { RelightRegion } from '@/types/case-info'

/**
 * 360 区域工作台的视图层：渲染 ERP 源，叠加区域轮廓顶点，把指针动作翻成事件。
 * 状态（区域、选中、草稿点）全在父组件；这里只维护视角与播放位置。
 */
const props = defineProps<{
  sourceUrl: string | null
  media: 'video' | 'image'
  regions: RelightRegion[]
  selectedId: string | null
  draft: LonLat[] | null
  mode: 'orbit' | 'poly'
}>()

const emit = defineEmits<{
  select: [id: string]
  'add-point': [lon: number, lat: number]
  'close-draft': []
  'move-vertex': [id: string, index: number, lon: number, lat: number]
  'insert-vertex': [id: string, index: number, lon: number, lat: number]
  'delete-vertex': [id: string, index: number]
  time: [seconds: number]
}>()

const host = ref<HTMLDivElement | null>(null)
const glCanvas = ref<HTMLCanvasElement | null>(null)
const overlay = ref<HTMLCanvasElement | null>(null)
const video = ref<HTMLVideoElement | null>(null)
const image = ref<HTMLImageElement | null>(null)
const playing = ref(false)
const currentTime = ref(0)
const duration = ref(0)

const pose: Pose = { yaw: 0, pitch: 0, fovH: (90 * Math.PI) / 180 }
const size = { width: 1, height: 1 }
let dpr = 1
let dirty = true
let gl: WebGLRenderingContext | null = null
let program: WebGLProgram | null = null
let texture: WebGLTexture | null = null
let uniforms: Record<string, WebGLUniformLocation | null> = {}
let ready = false
let lastFrameKey = ''
let drag: { x: number; y: number; yaw: number; pitch: number } | null = null
let vertexDrag: { id: string; index: number } | null = null
let moved = false
let cursor: Vec2 | null = null
let raf = 0
let observer: ResizeObserver | null = null

function resetView(): void {
  pose.yaw = 0
  pose.pitch = 0
  pose.fovH = (90 * Math.PI) / 180
  dirty = true
}

function seek(seconds: number): void {
  const element = video.value
  if (!element || !Number.isFinite(seconds)) return
  element.currentTime = Math.max(0, Math.min(seconds, duration.value || seconds))
  dirty = true
}

function togglePlay(): void {
  const element = video.value
  if (!element) return
  if (element.paused) void element.play()
  else element.pause()
}

function resize(): void {
  const element = host.value
  if (!element) return
  dpr = Math.min(2, window.devicePixelRatio || 1)
  const width = Math.max(1, Math.round(element.clientWidth * dpr))
  const height = Math.max(1, Math.round(element.clientHeight * dpr))
  size.width = width
  size.height = height
  for (const canvas of [glCanvas.value, overlay.value]) {
    if (canvas && (canvas.width !== width || canvas.height !== height)) {
      canvas.width = width
      canvas.height = height
    }
  }
  dirty = true
}

function initGL(): void {
  const canvas = glCanvas.value
  if (!canvas) return
  gl = canvas.getContext('webgl')
  if (!gl) return
  const compile = (type: number, source: string): WebGLShader | null => {
    const shader = gl!.createShader(type)
    if (!shader) return null
    gl!.shaderSource(shader, source)
    gl!.compileShader(shader)
    return shader
  }
  program = gl.createProgram()
  const vertex = compile(gl.VERTEX_SHADER, ERP_VERT_SRC)
  const fragment = compile(gl.FRAGMENT_SHADER, ERP_FRAG_SRC)
  if (!program || !vertex || !fragment) return
  gl.attachShader(program, vertex)
  gl.attachShader(program, fragment)
  gl.linkProgram(program)
  gl.useProgram(program)
  for (const name of ['uTex', 'uYaw', 'uPitch', 'uTanHalf', 'uAspect']) {
    uniforms[name] = gl.getUniformLocation(program, name)
  }
  const position = gl.getAttribLocation(program, 'aPos')
  gl.bindBuffer(gl.ARRAY_BUFFER, gl.createBuffer())
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW)
  gl.enableVertexAttribArray(position)
  gl.vertexAttribPointer(position, 2, gl.FLOAT, false, 0, 0)
  gl.uniform1i(uniforms.uTex, 0)
  texture = gl.createTexture()
  gl.bindTexture(gl.TEXTURE_2D, texture)
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE)
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE)
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR)
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR)
}

function uploadFrame(): void {
  if (!gl || !texture) return
  const source = props.media === 'video' ? video.value : image.value
  if (!source) return
  let width = 0
  let height = 0
  let key = ''
  if (source instanceof HTMLVideoElement) {
    if (!source.videoWidth || source.readyState < 2 || source.seeking) return
    width = source.videoWidth
    height = source.videoHeight
    key = `${source.currentSrc}:${source.currentTime}`
    currentTime.value = source.currentTime
  } else {
    if (!source.complete || !source.naturalWidth) return
    width = source.naturalWidth
    height = source.naturalHeight
    key = source.currentSrc || source.src
  }
  if (key === lastFrameKey) return
  lastFrameKey = key
  gl.bindTexture(gl.TEXTURE_2D, texture)
  const max = Number(gl.getParameter(gl.MAX_TEXTURE_SIZE)) || 0
  if (max > 0 && (width > max || height > max)) {
    // 超过 GPU 纹理上限的源先降采样（8K 图在 8192 上限上正好边界）。
    const scale = Math.min(max / width, max / height)
    const scratch = document.createElement('canvas')
    scratch.width = Math.max(1, Math.floor(width * scale))
    scratch.height = Math.max(1, Math.floor(height * scale))
    const context = scratch.getContext('2d')
    if (!context) return
    context.drawImage(source, 0, 0, scratch.width, scratch.height)
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, scratch)
  } else {
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, gl.RGBA, gl.UNSIGNED_BYTE, source)
  }
  ready = true
}

function drawGL(): void {
  if (!gl || !program) return
  gl.viewport(0, 0, size.width, size.height)
  gl.uniform1f(uniforms.uYaw, pose.yaw)
  gl.uniform1f(uniforms.uPitch, pose.pitch)
  const aspect = size.width / Math.max(1, size.height)
  gl.uniform1f(uniforms.uTanHalf, Math.tan(pose.fovH / 2) / aspect)
  gl.uniform1f(uniforms.uAspect, aspect)
  gl.clearColor(0.04, 0.06, 0.11, 1)
  gl.clear(gl.COLOR_BUFFER_BIT)
  if (!ready) return
  gl.activeTexture(gl.TEXTURE0)
  gl.bindTexture(gl.TEXTURE_2D, texture)
  gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4)
}

function rgba(hex: string, alpha: number): string {
  const value = parseInt(hex.slice(1), 16)
  return `rgba(${(value >> 16) & 255},${(value >> 8) & 255},${value & 255},${alpha})`
}

function visibleRuns(points: Array<Vec2 | null>): Vec2[][] {
  const runs: Vec2[][] = []
  let run: Vec2[] = []
  for (const point of points) {
    if (point) run.push(point)
    else if (run.length) {
      runs.push(run)
      run = []
    }
  }
  if (run.length) runs.push(run)
  return runs
}

function drawOverlay(): void {
  const canvas = overlay.value
  const context = canvas?.getContext('2d')
  if (!canvas || !context) return
  context.clearRect(0, 0, size.width, size.height)
  const scale = dpr
  for (const region of props.regions) {
    if (!region.enabled) continue
    const projected = projectPolygon(region.verts, pose, size)
    const selected = region.id === props.selectedId
    context.fillStyle = rgba(region.color, selected ? 0.26 : 0.14)
    context.strokeStyle = region.color
    context.lineWidth = (selected ? 3 : 1.6) * scale
    context.setLineDash(selected ? [] : [6 * scale, 4 * scale])
    if (projected.allFront) {
      const path = new Path2D()
      const points = projected.points as Vec2[]
      path.moveTo(points[0][0], points[0][1])
      for (let i = 1; i < points.length; i += 1) path.lineTo(points[i][0], points[i][1])
      path.closePath()
      context.fill(path)
      context.stroke(path)
    } else {
      for (const run of visibleRuns(projected.points)) {
        if (run.length < 2) continue
        context.beginPath()
        context.moveTo(run[0][0], run[0][1])
        for (let i = 1; i < run.length; i += 1) context.lineTo(run[i][0], run[i][1])
        context.stroke()
      }
    }
    context.setLineDash([])
    if (selected) {
      for (const [lon, lat] of region.verts) {
        const point = project(lon, lat, pose, size)
        if (!point) continue
        context.fillStyle = '#fff'
        context.strokeStyle = region.color
        context.lineWidth = 2 * scale
        context.beginPath()
        context.rect(point[0] - 4 * scale, point[1] - 4 * scale, 8 * scale, 8 * scale)
        context.fill()
        context.stroke()
      }
    }
    if (projected.allFront) {
      const points = projected.points as Vec2[]
      const cx = points.reduce((sum, point) => sum + point[0], 0) / points.length
      const cy = points.reduce((sum, point) => sum + point[1], 0) / points.length
      context.font = `${11 * scale}px system-ui, sans-serif`
      context.textAlign = 'center'
      context.textBaseline = 'middle'
      const label = region.name
      const width = context.measureText(label).width
      context.fillStyle = 'rgba(15,23,42,.72)'
      context.fillRect(cx - width / 2 - 5 * scale, cy - 8 * scale, width + 10 * scale, 16 * scale)
      context.fillStyle = '#e2e8f0'
      context.fillText(label, cx, cy)
    }
  }
  if (props.draft?.length) {
    const points = props.draft.map(([lon, lat]) => project(lon, lat, pose, size))
    context.strokeStyle = '#fbbf24'
    context.lineWidth = 2 * scale
    context.setLineDash([5 * scale, 4 * scale])
    context.beginPath()
    let started = false
    for (const point of points) {
      if (!point) {
        started = false
        continue
      }
      if (!started) {
        context.moveTo(point[0], point[1])
        started = true
      } else context.lineTo(point[0], point[1])
    }
    context.stroke()
    const last = points[points.length - 1]
    if (last && cursor) {
      context.beginPath()
      context.moveTo(last[0], last[1])
      context.lineTo(cursor[0], cursor[1])
      context.stroke()
    }
    context.setLineDash([])
    for (const point of points) {
      if (!point) continue
      context.fillStyle = '#fbbf24'
      context.beginPath()
      context.arc(point[0], point[1], 3.5 * scale, 0, Math.PI * 2)
      context.fill()
    }
  }
}

function frame(): void {
  if (dirty) {
    uploadFrame()
    drawGL()
    drawOverlay()
    dirty = false
  }
  raf = requestAnimationFrame(frame)
}

function localPoint(event: PointerEvent | MouseEvent): Vec2 {
  const canvas = overlay.value!
  const rect = canvas.getBoundingClientRect()
  return [(event.clientX - rect.left) * dpr, (event.clientY - rect.top) * dpr]
}

function selectedRegion(): RelightRegion | null {
  return props.regions.find((region) => region.id === props.selectedId) ?? null
}

function vertexAt(point: Vec2): { id: string; index: number } | null {
  const region = selectedRegion()
  if (!region) return null
  for (let index = 0; index < region.verts.length; index += 1) {
    const [lon, lat] = region.verts[index]
    const projected = project(lon, lat, pose, size)
    if (!projected) continue
    if (Math.hypot(projected[0] - point[0], projected[1] - point[1]) <= 10 * dpr) {
      return { id: region.id, index }
    }
  }
  return null
}

function distanceToSegment(point: Vec2, a: Vec2, b: Vec2): number {
  const dx = b[0] - a[0]
  const dy = b[1] - a[1]
  const length = dx * dx + dy * dy
  if (!length) return Math.hypot(point[0] - a[0], point[1] - a[1])
  let t = ((point[0] - a[0]) * dx + (point[1] - a[1]) * dy) / length
  t = Math.max(0, Math.min(1, t))
  return Math.hypot(point[0] - (a[0] + t * dx), point[1] - (a[1] + t * dy))
}

function edgeInsert(point: Vec2): { index: number; lon: number; lat: number } | null {
  const region = selectedRegion()
  if (!region) return null
  let best: { distance: number; index: number } | null = null
  for (let index = 0; index < region.verts.length; index += 1) {
    const from = region.verts[index]
    const to = region.verts[(index + 1) % region.verts.length]
    const dense = densify([from, to], 2).map(([lon, lat]) => project(lon, lat, pose, size))
    for (let step = 1; step < dense.length; step += 1) {
      const a = dense[step - 1]
      const b = dense[step]
      if (!a || !b) continue
      const distance = distanceToSegment(point, a, b)
      if (!best || distance < best.distance) best = { distance, index: index + 1 }
    }
  }
  if (!best || best.distance > 7 * dpr) return null
  const [lon, lat] = unproject(point[0], point[1], pose, size)
  return { index: best.index, lon, lat }
}

function onPointerDown(event: PointerEvent): void {
  const point = localPoint(event)
  if (props.mode === 'poly') {
    const [lon, lat] = unproject(point[0], point[1], pose, size)
    emit('add-point', lon, lat)
    return
  }
  const vertex = vertexAt(point)
  if (vertex) {
    vertexDrag = vertex
    overlay.value?.setPointerCapture(event.pointerId)
    return
  }
  drag = { x: event.clientX, y: event.clientY, yaw: pose.yaw, pitch: pose.pitch }
  moved = false
  overlay.value?.setPointerCapture(event.pointerId)
}

function onPointerMove(event: PointerEvent): void {
  const point = localPoint(event)
  if (props.mode === 'poly') {
    cursor = point
    dirty = true
  }
  if (vertexDrag) {
    const [lon, lat] = unproject(point[0], point[1], pose, size)
    emit('move-vertex', vertexDrag.id, vertexDrag.index, lon, lat)
    return
  }
  if (!drag) return
  const dx = event.clientX - drag.x
  const dy = event.clientY - drag.y
  if (Math.abs(dx) + Math.abs(dy) > 3) moved = true
  const perPx = pose.fovH / Math.max(1, overlay.value?.clientWidth ?? 1)
  pose.yaw = drag.yaw + dx * perPx
  pose.pitch = clampPitch(drag.pitch + dy * perPx)
  dirty = true
}

function onPointerUp(event: PointerEvent): void {
  if (vertexDrag) {
    vertexDrag = null
    return
  }
  if (drag && !moved) {
    const point = localPoint(event)
    const insert = edgeInsert(point)
    if (insert && props.selectedId) {
      emit('insert-vertex', props.selectedId, insert.index, insert.lon, insert.lat)
    } else {
      const [lon, lat] = unproject(point[0], point[1], pose, size)
      for (let index = props.regions.length - 1; index >= 0; index -= 1) {
        const region = props.regions[index]
        if (region.enabled && pointInPolygon(lon, lat, region.verts)) {
          emit('select', region.id)
          break
        }
      }
    }
  }
  drag = null
}

function onContextMenu(event: MouseEvent): void {
  const point = localPoint(event)
  const vertex = vertexAt(point)
  if (!vertex) return
  event.preventDefault()
  emit('delete-vertex', vertex.id, vertex.index)
}

function onWheel(event: WheelEvent): void {
  event.preventDefault()
  pose.fovH = clampFov(pose.fovH * Math.exp(event.deltaY * 0.001))
  dirty = true
}

function onVideoTime(): void {
  const element = video.value
  if (!element) return
  currentTime.value = element.currentTime
  duration.value = element.duration || 0
  emit('time', element.currentTime)
  dirty = true
}

function onScrub(event: Event): void {
  seek(Number((event.target as HTMLInputElement).value))
}

function onPlayState(): void {
  playing.value = video.value ? !video.value.paused : false
}

function clearCursor(): void {
  cursor = null
  dirty = true
}

function closeDraft(): void {
  if (props.mode === 'poly') emit('close-draft')
}

function applySource(url: string | null): void {
  lastFrameKey = ''
  ready = false
  const element = video.value
  if (element) {
    element.src = url ?? ''
    if (url) element.load()
  }
  dirty = true
}

watch(() => props.sourceUrl, (url) => applySource(url), {
  immediate: true,
  flush: 'post',
})

watch(
  () => [props.regions, props.selectedId, props.draft, props.mode],
  () => {
    dirty = true
  },
  { deep: true },
)

onMounted(() => {
  resize()
  initGL()
  observer = new ResizeObserver(() => resize())
  if (host.value) observer.observe(host.value)
  const element = video.value
  if (element) {
    element.addEventListener('timeupdate', onVideoTime)
    element.addEventListener('seeked', onVideoTime)
    element.addEventListener('play', onPlayState)
    element.addEventListener('pause', onPlayState)
    element.addEventListener('loadedmetadata', onVideoTime)
  }
  // immediate 的 watch 在 setup 阶段就跑了，那时 <video> 还没挂载；这里补一次。
  applySource(props.sourceUrl)
  raf = requestAnimationFrame(frame)
})

onBeforeUnmount(() => {
  cancelAnimationFrame(raf)
  observer?.disconnect()
  const element = video.value
  if (element) {
    element.removeEventListener('timeupdate', onVideoTime)
    element.removeEventListener('seeked', onVideoTime)
    element.removeEventListener('play', onPlayState)
    element.removeEventListener('pause', onPlayState)
    element.removeEventListener('loadedmetadata', onVideoTime)
  }
  for (const item of [texture]) {
    if (item && gl) gl.deleteTexture(item)
  }
  gl = null
})

defineExpose({ resetView, togglePlay, seek })
</script>

<template>
  <div class="stage">
    <div ref="host" class="viewer">
      <canvas ref="glCanvas" class="gl"></canvas>
      <canvas
        ref="overlay"
        class="ov"
        :class="{ drawing: props.mode === 'poly' }"
        @pointerdown="onPointerDown"
        @pointermove="onPointerMove"
        @pointerup="onPointerUp"
        @pointerleave="clearCursor"
        @dblclick="closeDraft"
        @contextmenu="onContextMenu"
        @wheel="onWheel"
      ></canvas>
      <video
        v-if="props.media === 'video'"
        ref="video"
        class="source"
        muted
        loop
        playsinline
      ></video>
      <img v-else ref="image" class="source" :src="props.sourceUrl ?? ''" alt="" />
      <div class="tag">
        <span class="chip">GT · {{ currentTime.toFixed(1) }}s</span>
      </div>
      <div class="hint">拖动转向 · 滚轮缩放 · 点击区域选中</div>
    </div>
    <div v-if="props.media === 'video'" class="scrub">
      <button class="play" type="button" @click="togglePlay">{{ playing ? '❚❚' : '▶' }}</button>
      <input
        type="range"
        min="0"
        :max="duration || 0"
        step="0.04"
        :value="currentTime"
        @input="onScrub"
      />
      <span class="time">{{ currentTime.toFixed(1) }} / {{ duration.toFixed(1) }}s</span>
    </div>
  </div>
</template>

<style scoped>
.stage {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  min-width: 0;
}

.viewer {
  position: relative;
  aspect-ratio: 16 / 9;
  overflow: hidden;
  background: var(--color-video-bg);
  border-radius: var(--radius-lg);
}

.viewer canvas {
  position: absolute;
  inset: 0;
  display: block;
  width: 100%;
  height: 100%;
}

.viewer canvas.ov {
  cursor: grab;
}

.viewer canvas.ov.drawing {
  cursor: crosshair;
}

.source {
  position: absolute;
  width: 1px;
  height: 1px;
  opacity: 0;
  pointer-events: none;
}

.tag {
  position: absolute;
  top: 10px;
  left: 10px;
  pointer-events: none;
}

.chip {
  padding: 1px 7px;
  border-radius: 999px;
  background: rgba(15, 23, 42, 0.62);
  color: #e2e8f0;
  font-family: var(--font-mono);
  font-size: 11px;
}

.hint {
  position: absolute;
  top: 10px;
  right: 10px;
  padding: 3px 8px;
  border-radius: 999px;
  background: rgba(15, 23, 42, 0.55);
  color: rgba(226, 232, 240, 0.72);
  font-size: 11px;
  pointer-events: none;
}

.scrub {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: 8px 12px;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}

.scrub input[type='range'] {
  flex: 1;
}

.play {
  padding: 2px 6px;
  border: 0;
  background: transparent;
  color: var(--color-text-muted);
  cursor: pointer;
}

.time {
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  color: var(--color-text-muted);
}
</style>
