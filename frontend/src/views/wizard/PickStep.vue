<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { CaretLeft, CaretRight, Refresh, VideoPlay } from '@element-plus/icons-vue'
import {
  ensureProxy,
  fetchProxy,
  nudgeViewport,
  resetDraft,
  saveDraft,
  setInner,
  setViewport,
  setViewportSize,
} from '@/api/workflow'
import type { GeometryPayload } from '@/api/workflow'
import { useInnerDrag } from '@/composables/useInnerDrag'
import type {
  CaseInfo,
  Footprint,
  FrameRange,
  Inner,
  Viewport,
  ViewportOption,
} from '@/types/case-info'

const props = defineProps<{ caseName: string; info: CaseInfo }>()
const emit = defineEmits<{
  updated: [data: CaseInfo]
  geometry: [data: GeometryPayload]
}>()

const viewport = ref<Viewport | null>(props.info.viewport)
const inner = ref<Inner | null>(props.info.inner)
const footprint = ref<Footprint[]>([...props.info.viewport_footprint])
const viewportOptions = ref<ViewportOption[]>([...props.info.viewport_options])
const frames = ref<FrameRange>(
  props.info.frames ?? { start: 0, end: Math.max(0, (props.info.frame_count ?? 1) - 1) },
)
const startFrame = ref(frames.value.start)
const endFrame = ref(frames.value.end)

const erpVideo = ref<HTMLVideoElement | null>(null)
const vpImage = ref<HTMLImageElement | null>(null)
const frame = ref(frames.value.start)
const playing = ref(false)
const proxyState = ref(props.info.proxy?.state ?? 'missing')
const proxyBusy = ref(false)
const frameSaving = ref(false)
const vpVersion = ref(Date.now())
let proxyTimer: ReturnType<typeof setInterval> | null = null
let joystickTimer: ReturnType<typeof setInterval> | null = null
let joystickInFlight = false

const frameCount = computed(() => {
  if (props.info.frame_count) return props.info.frame_count
  const duration = erpVideo.value?.duration
  const fps = props.info.fps
  if (duration && fps) return Math.max(1, Math.floor(duration * fps))
  return 1
})
const maxFrame = computed(() => Math.max(0, frameCount.value - 1))
const fps = computed(() => props.info.fps ?? 30)
const proxyReady = computed(() => proxyState.value === 'ready')
const erpSource = computed(() =>
  proxyReady.value ? `/video/proxy/${props.caseName}` : props.info.source_url,
)
const vpUrl = computed(() => {
  const base = props.info.previews.vp ?? `/img/${props.caseName}/vp.png`
  const separator = base.includes('?') ? '&' : '?'
  return `${base}${separator}frame=${frame.value}&t=${vpVersion.value}`
})
const innerStyle = computed(() => {
  if (!inner.value || !viewport.value) return null
  return {
    left: `${(inner.value.x / viewport.value.width) * 100}%`,
    top: `${(inner.value.y / viewport.value.height) * 100}%`,
    width: `${(inner.value.width / viewport.value.width) * 100}%`,
    height: `${(inner.value.height / viewport.value.height) * 100}%`,
  }
})
const yawText = computed(() => viewport.value?.yaw_deg.toFixed(2) ?? '—')
const pitchText = computed(() => viewport.value?.pitch_deg.toFixed(2) ?? '—')
const viewportLabel = computed(() =>
  viewport.value ? `${viewport.value.width}×${viewport.value.height}` : '—',
)

const drag = useInnerDrag(
  () => vpImage.value,
  async (rect) => {
    try {
      const payload = await setInner(
        props.caseName,
        rect.frac.x,
        rect.frac.y,
        rect.frac.w,
        rect.frac.h,
      )
      applyGeometry(payload)
    } catch (err) {
      ElMessage.error(err instanceof Error ? err.message : '保存选区失败')
    }
  },
)

function applyGeometry(payload: GeometryPayload): void {
  viewport.value = payload.viewport
  inner.value = payload.inner
  footprint.value = payload.viewport_footprint
  vpVersion.value = Date.now()
  emit('geometry', payload)
}

function frameAt(time: number): number {
  return Math.min(maxFrame.value, Math.max(0, Math.round(time * fps.value)))
}

function syncFromVideo(): void {
  if (!erpVideo.value) return
  frame.value = frameAt(erpVideo.value.currentTime)
}

function seekToFrame(value: number): void {
  frame.value = Math.min(maxFrame.value, Math.max(0, value))
  const video = erpVideo.value
  if (!video) return
  const target = frame.value / fps.value
  if (Math.abs(video.currentTime - target) > 0.001) video.currentTime = target
}

function onSlider(value: number | number[]): void {
  seekToFrame(Number(Array.isArray(value) ? value[0] : value))
}

async function onErpClick(event: MouseEvent): Promise<void> {
  const video = erpVideo.value
  if (!video) return
  const box = video.getBoundingClientRect()
  if (!box.width || !box.height) return
  const fx = (event.clientX - box.left) / box.width
  const fy = (event.clientY - box.top) / box.height
  try {
    applyGeometry(await setViewport(props.caseName, fx, fy))
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '设置视口失败')
  }
}

async function onChangeSize(value: unknown): Promise<void> {
  const option = viewportOptions.value.find((item) => item.label === value)
  if (!option) return
  try {
    applyGeometry(await setViewportSize(props.caseName, option.width, option.height))
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '切换视口尺寸失败')
  }
}

function startJoystick(dyaw: number, dpitch: number): void {
  stopJoystick()
  const tick = async (): Promise<void> => {
    if (joystickInFlight) return
    joystickInFlight = true
    try {
      applyGeometry(await nudgeViewport(props.caseName, dyaw, dpitch))
    } catch {
      stopJoystick()
    } finally {
      joystickInFlight = false
    }
  }
  void tick()
  joystickTimer = setInterval(() => void tick(), 100)
}

function stopJoystick(): void {
  if (joystickTimer) {
    clearInterval(joystickTimer)
    joystickTimer = null
  }
}

function togglePlay(): void {
  const video = erpVideo.value
  if (!video) return
  if (video.paused) {
    void video.play()
  } else {
    video.pause()
  }
}

function stepFrame(delta: number): void {
  seekToFrame(frame.value + delta)
}

async function saveFrames(): Promise<void> {
  const start = Math.min(startFrame.value, endFrame.value)
  const end = Math.max(startFrame.value, endFrame.value)
  if (start < 0 || end > maxFrame.value || start >= end) {
    ElMessage.warning(`帧范围要在 0–${maxFrame.value} 之间，且开始小于结束`)
    return
  }
  frameSaving.value = true
  try {
    const data = await saveDraft(props.caseName, { frames: { start, end } })
    frames.value = { start, end }
    emit('updated', data)
    ElMessage.success('抽取范围已保存')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '保存帧范围失败')
  } finally {
    frameSaving.value = false
  }
}

async function resetToExtract(): Promise<void> {
  try {
    applyGeometry(await resetDraft(props.caseName))
    ElMessage.success('已回到当前抽取版本的选区')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '重置选区失败')
  }
}

async function buildProxy(): Promise<void> {
  if (proxyBusy.value || proxyReady.value) return
  proxyBusy.value = true
  try {
    const payload = await ensureProxy(props.caseName)
    proxyState.value = payload.state
    startProxyPolling()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '启动预览代理失败')
  } finally {
    proxyBusy.value = false
  }
}

function startProxyPolling(): void {
  if (proxyTimer) clearInterval(proxyTimer)
  proxyTimer = setInterval(async () => {
    try {
      const payload = await fetchProxy(props.caseName)
      proxyState.value = payload.state
      if (payload.state === 'ready' || payload.state === 'disabled') {
        stopProxyPolling()
      }
    } catch {
      stopProxyPolling()
    }
  }, 2000)
}

function stopProxyPolling(): void {
  if (proxyTimer) {
    clearInterval(proxyTimer)
    proxyTimer = null
  }
}

watch(
  () => props.info,
  (info) => {
    viewport.value = info.viewport
    inner.value = info.inner
    footprint.value = [...info.viewport_footprint]
    viewportOptions.value = [...info.viewport_options]
    if (info.frames) {
      frames.value = info.frames
      startFrame.value = info.frames.start
      endFrame.value = info.frames.end
    }
  },
)

onUnmounted(() => {
  stopJoystick()
  stopProxyPolling()
})
</script>

<template>
  <div class="pick-layout">
    <section class="panel">
      <div class="panel-head">
        <h2 class="panel-title">全景与视口</h2>
        <span class="muted">点哪里，视口中心就在哪里</span>
      </div>
      <div class="panel-body">
        <div class="erp-wrap">
          <video
            ref="erpVideo"
            :src="erpSource"
            muted
            preload="auto"
            @loadedmetadata="syncFromVideo"
            @timeupdate="syncFromVideo"
            @seeked="syncFromVideo"
            @play="playing = true"
            @pause="playing = false"
            @click="onErpClick"
          />
          <div
            v-for="(box, index) in footprint"
            :key="index"
            class="footprint"
            :style="{
              left: `${box.x * 100}%`,
              top: `${box.y * 100}%`,
              width: `${box.w * 100}%`,
              height: `${box.h * 100}%`,
            }"
          />
          <div class="erp-cross" :style="{ left: '50%', top: '50%' }" />
        </div>

        <div class="scrub">
          <el-button :icon="playing ? undefined : VideoPlay" size="small" @click="togglePlay">
            {{ playing ? '暂停' : '播放' }}
          </el-button>
          <el-button :icon="CaretLeft" size="small" @click="stepFrame(-1)" />
          <el-button :icon="CaretRight" size="small" @click="stepFrame(1)" />
          <el-slider
            :model-value="frame"
            :min="0"
            :max="maxFrame"
            :show-tooltip="false"
            class="frame-slider"
            @update:model-value="onSlider"
          />
          <span class="mono muted">{{ frame }} / {{ maxFrame }}</span>
        </div>

        <div class="meta-row">
          <span>yaw {{ yawText }}°</span>
          <span>pitch {{ pitchText }}°</span>
          <span>视口 {{ viewportLabel }}</span>
          <span class="proxy-state">
            预览代理：{{ proxyState }}
            <el-button
              v-if="!proxyReady"
              link
              type="primary"
              :loading="proxyBusy"
              @click="buildProxy"
            >
              生成代理
            </el-button>
          </span>
        </div>
      </div>
    </section>

    <aside class="panel">
      <div class="panel-head">
        <h2 class="panel-title">选区与抽帧范围</h2>
        <el-button link type="primary" :icon="Refresh" @click="resetToExtract">重置选区</el-button>
      </div>
      <div class="panel-body">
        <div class="vp-toolbar">
          <label class="ctl">
            <span>视口尺寸</span>
            <el-select
              :model-value="viewportLabel"
              size="small"
              style="width: 150px"
              :disabled="viewportOptions.length <= 1"
              @change="onChangeSize"
            >
              <el-option
                v-for="option in viewportOptions"
                :key="option.label"
                :label="option.label"
                :value="option.label"
              />
            </el-select>
          </label>
          <span class="muted">在预览图上拖框，松手保存</span>
        </div>

        <div class="vp-wrap" :class="{ dragging: drag.dragging.value }">
          <img
            ref="vpImage"
            :src="vpUrl"
            alt="视口预览"
            draggable="false"
            @pointerdown="drag.begin"
          />
          <div v-if="innerStyle" class="inner-box" :style="innerStyle" />
          <div
            v-if="drag.rect.value"
            class="drag-box"
            :style="{
              left: `${drag.rect.value.css.x}px`,
              top: `${drag.rect.value.css.y}px`,
              width: `${drag.rect.value.css.w}px`,
              height: `${drag.rect.value.css.h}px`,
            }"
          />
        </div>

        <div class="meta-row">
          <span v-if="inner" class="mono">
            inner x{{ inner.x }} y{{ inner.y }} {{ inner.width }}×{{ inner.height }}
          </span>
          <span v-else class="muted">还没有选区</span>
        </div>

        <div class="joystick-row">
          <div class="joystick">
            <span />
            <button
              type="button"
              @pointerdown="startJoystick(0, 0.1)"
              @pointerup="stopJoystick"
              @pointerleave="stopJoystick"
            >
              ↑
            </button>
            <span />
            <button
              type="button"
              @pointerdown="startJoystick(-0.1, 0)"
              @pointerup="stopJoystick"
              @pointerleave="stopJoystick"
            >
              ←
            </button>
            <span class="joystick-dot">•</span>
            <button
              type="button"
              @pointerdown="startJoystick(0.1, 0)"
              @pointerup="stopJoystick"
              @pointerleave="stopJoystick"
            >
              →
            </button>
            <span />
            <button
              type="button"
              @pointerdown="startJoystick(0, -0.1)"
              @pointerup="stopJoystick"
              @pointerleave="stopJoystick"
            >
              ↓
            </button>
            <span />
          </div>
          <span class="muted">按住持续微调（0.1°/0.1 秒）</span>
        </div>

        <div class="frames-row">
          <label class="ctl">
            <span>抽取范围</span>
            <el-input-number
              v-model="startFrame"
              :min="0"
              :max="maxFrame"
              size="small"
              controls-position="right"
            />
            <span>–</span>
            <el-input-number
              v-model="endFrame"
              :min="0"
              :max="maxFrame"
              size="small"
              controls-position="right"
            />
          </label>
          <el-button size="small" :loading="frameSaving" @click="saveFrames">保存范围</el-button>
        </div>
      </div>
    </aside>
  </div>
</template>

<style scoped>
.pick-layout {
  display: grid;
  grid-template-columns: minmax(0, 1.15fr) minmax(380px, 1fr);
  gap: var(--space-4);
  align-items: start;
}

.erp-wrap {
  position: relative;
  overflow: hidden;
  border-radius: var(--radius-md);
  background: var(--color-video-bg);
}

.erp-wrap video {
  display: block;
  width: 100%;
  max-height: 420px;
  object-fit: contain;
  cursor: crosshair;
}

.footprint {
  position: absolute;
  border: 2px solid #22c55e;
  background: rgba(34, 197, 94, 0.12);
  pointer-events: none;
}

.erp-cross {
  position: absolute;
  width: 14px;
  height: 14px;
  transform: translate(-50%, -50%);
  border: 1px solid rgba(255, 255, 255, 0.7);
  border-radius: 50%;
  pointer-events: none;
}

.scrub {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-top: var(--space-3);
}

.frame-slider {
  flex: 1;
  min-width: 120px;
}

.meta-row {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-3);
  margin-top: var(--space-2);
  color: var(--color-text-muted);
  font-size: var(--text-xs);
}

.proxy-state {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
}

.vp-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
}

.vp-wrap {
  position: relative;
  margin-top: var(--space-2);
  border-radius: var(--radius-md);
  overflow: hidden;
  background: var(--color-video-bg);
  user-select: none;
}

.vp-wrap img {
  display: block;
  width: 100%;
  cursor: crosshair;
  touch-action: none;
}

.vp-wrap.dragging img {
  cursor: grabbing;
}

.inner-box,
.drag-box {
  position: absolute;
  border: 2px solid var(--color-danger);
  background: rgba(220, 38, 38, 0.08);
  pointer-events: none;
}

.drag-box {
  border-style: dashed;
}

.joystick-row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  margin-top: var(--space-3);
}

.joystick {
  display: grid;
  grid-template-columns: repeat(3, 30px);
  grid-template-rows: repeat(3, 26px);
  gap: 2px;
  place-items: center;
}

.joystick button {
  width: 28px;
  height: 24px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-surface);
  cursor: pointer;
}

.joystick button:hover {
  background: var(--color-surface-muted);
}

.joystick-dot {
  color: var(--color-text-faint);
}

.frames-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-3);
  margin-top: var(--space-3);
}

@media (max-width: 1280px) {
  .pick-layout {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
