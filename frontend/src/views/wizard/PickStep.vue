<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import {
  CaretLeft,
  CaretRight,
  CopyDocument,
  Download,
  Refresh,
  VideoPlay,
} from '@element-plus/icons-vue'
import {
  ensureProxy,
  exportRegion,
  fetchProxy,
  importRegion,
  nudgeViewport,
  resetDraft,
  saveDraft,
  setFov,
  setInner,
  setViewport,
  setViewportSize,
} from '@/api/workflow'
import type { GeometryPayload } from '@/api/workflow'
import FileRow from '@/components/common/FileRow.vue'
import { useInnerDrag } from '@/composables/useInnerDrag'
import type {
  CaseInfo,
  Coverage,
  Footprint,
  FrameRange,
  Inner,
  RegionPayload,
  Viewport,
  ViewportOption,
} from '@/types/case-info'
import { ClipboardUnavailableError, readClipboard, writeClipboard } from '@/utils/clipboard'

const props = defineProps<{ caseName: string; info: CaseInfo }>()
const emit = defineEmits<{
  updated: [data: CaseInfo]
  geometry: [data: GeometryPayload]
}>()

const viewport = ref<Viewport | null>(props.info.viewport)
const inner = ref<Inner | null>(props.info.inner)
const footprint = ref<Footprint[]>([...props.info.viewport_footprint])
const viewportOptions = ref<ViewportOption[]>([...props.info.viewport_options])
const coverage = ref<Coverage | null>(props.info.coverage ?? null)
const fov = ref<number>(props.info.viewport?.fov_h_deg ?? 59)
const fovRange = ref<[number, number]>(props.info.fov_range ?? [20, 120])
const frames = ref<FrameRange>(
  props.info.frames ?? { start: 0, end: Math.max(0, (props.info.frame_count ?? 1) - 1) },
)
const startFrame = ref(frames.value.start)
const endFrame = ref(frames.value.end)

const erpVideo = ref<HTMLVideoElement | null>(null)
const erpImage = ref<HTMLImageElement | null>(null)
const vpImage = ref<HTMLImageElement | null>(null)
const frame = ref(frames.value.start)
const playing = ref(false)
const proxyState = ref(props.info.proxy?.state ?? 'missing')
const proxyBusy = ref(false)
const frameSaving = ref(false)
const regionExporting = ref(false)
const regionImporting = ref(false)
const manualDialog = ref(false)
const manualMode = ref<'copy' | 'paste'>('paste')
const manualText = ref('')
const vpVersion = ref(Date.now())
let proxyTimer: ReturnType<typeof setInterval> | null = null
let joystickTimer: ReturnType<typeof setInterval> | null = null
let joystickInFlight = false

/** patchimg 的源是单张 2:1 ERP 图：没有时间轴、代理由源图直出。 */
const isImage = computed(() => props.info.flow === 'patchimg')
/** 选区范围的导入导出只属于视频 patch：patchimg 没有 fov 与抽取范围。 */
const isPatch = computed(() => props.info.flow === 'patch')

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
  // 图片流程只有一帧：不带 frame，直接走服务端的第 0 帧重投影；
  // 带 frame=0 也能用，但会让缓存多走一条单帧路径。
  if (isImage.value) return `${base}${separator}t=${vpVersion.value}`
  return `${base}${separator}frame=${frame.value}&t=${vpVersion.value}`
})
const viewportReady = computed(() => Boolean(viewport.value?.width))
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
const coverageText = computed(() => {
  if (!coverage.value) return ''
  const [w, h] = coverage.value.footprint
  const d1080 = coverage.value.density['1080p'].toFixed(2)
  const d4k = coverage.value.density['4K'].toFixed(2)
  return `ERP ${w}×${h} · 1080p ${d1080}× / 4K ${d4k}× · inner ${coverage.value.inner_deg.toFixed(1)}°`
})
const fovWarning = computed(() => fov.value > 110)

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
  if (payload.coverage !== undefined) coverage.value = payload.coverage
  if (payload.fov_range) fovRange.value = payload.fov_range
  fov.value = payload.viewport.fov_h_deg
  if (payload.frames) {
    frames.value = payload.frames
    startFrame.value = payload.frames.start
    endFrame.value = payload.frames.end
  }
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
  // 图片流程与视频共用同一套点选定位：点击点在全景面上的 0..1 分数。
  const media = isImage.value ? erpImage.value : erpVideo.value
  if (!media) return
  const box = media.getBoundingClientRect()
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

async function onFovChange(value: number | number[]): Promise<void> {
  const next = Number(Array.isArray(value) ? value[0] : value)
  if (!Number.isFinite(next)) return
  try {
    applyGeometry(await setFov(props.caseName, next))
  } catch (err) {
    fov.value = viewport.value?.fov_h_deg ?? next
    ElMessage.error(err instanceof Error ? err.message : '设置视场角失败')
  }
}

const NUDGE_STORAGE_KEY = 'vr.pickNudgeDegPerSec'
const NUDGE_INTERVAL_MS = 100

function loadNudgeSpeed(): number {
  try {
    const raw = Number(localStorage.getItem(NUDGE_STORAGE_KEY))
    if (Number.isFinite(raw) && raw >= 0.2 && raw <= 5) return Math.round(raw * 10) / 10
  } catch {
    /* localStorage 不可用（隐私模式等）就用默认值 */
  }
  return 1.0
}

/** 摇杆微调速度（°/秒），页面滑块实时可调；默认 1.0 等于旧的 0.1°/100ms。 */
const nudgeSpeed = ref(loadNudgeSpeed())

watch(nudgeSpeed, (value) => {
  try {
    localStorage.setItem(NUDGE_STORAGE_KEY, String(value))
  } catch {
    /* 存不进去不影响本次会话 */
  }
})

function startJoystick(dirYaw: number, dirPitch: number): void {
  stopJoystick()
  const tick = async (): Promise<void> => {
    if (joystickInFlight) return
    joystickInFlight = true
    try {
      const step = (nudgeSpeed.value * NUDGE_INTERVAL_MS) / 1000
      applyGeometry(await nudgeViewport(props.caseName, dirYaw * step, dirPitch * step))
    } catch {
      stopJoystick()
    } finally {
      joystickInFlight = false
    }
  }
  void tick()
  joystickTimer = setInterval(() => void tick(), NUDGE_INTERVAL_MS)
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

function messageOf(err: unknown, fallback: string): string {
  return err instanceof Error && err.message ? err.message : fallback
}

/** 导出已保存的选区到剪贴板；写不进去时弹手动复制框。 */
async function exportRange(): Promise<void> {
  if (regionExporting.value) return
  regionExporting.value = true
  try {
    const payload = await exportRegion(props.caseName)
    const text = JSON.stringify(payload, null, 2)
    if (await writeClipboard(text)) {
      ElMessage.success(
        `已复制选区范围（${payload.viewport.width}×${payload.viewport.height}，` +
          `帧 ${payload.frames.start}–${payload.frames.end}）`,
      )
      return
    }
    manualMode.value = 'copy'
    manualText.value = text
    manualDialog.value = true
  } catch (err) {
    ElMessage.error(messageOf(err, '导出选区范围失败'))
  } finally {
    regionExporting.value = false
  }
}

/** 解析并写入一段剪贴板文本；成功返回 true，错误已经用消息提示过。 */
async function applyRegionText(text: string): Promise<boolean> {
  if (!text.trim()) {
    ElMessage.warning('剪贴板是空的，先复制一个选区范围')
    return false
  }
  let data: unknown
  try {
    data = JSON.parse(text)
  } catch {
    ElMessage.error('剪贴板内容不是选区范围（不是合法 JSON）')
    return false
  }
  try {
    const payload = await importRegion(props.caseName, data as RegionPayload)
    applyGeometry(payload)
    const size = `${payload.viewport.width}×${payload.viewport.height}`
    ElMessage.success(
      payload.scaled ? `已导入选区范围；inner 已按当前视口 ${size} 缩放` : '已导入选区范围',
    )
    return true
  } catch (err) {
    ElMessage.error(messageOf(err, '导入选区范围失败'))
    return false
  }
}

/** 读剪贴板并导入；读不到时转为手动粘贴对话框。 */
async function importRange(): Promise<void> {
  if (regionImporting.value) return
  regionImporting.value = true
  try {
    let text: string
    try {
      text = await readClipboard()
    } catch (err) {
      if (err instanceof ClipboardUnavailableError) {
        manualMode.value = 'paste'
        manualText.value = ''
        manualDialog.value = true
        return
      }
      throw err
    }
    await applyRegionText(text)
  } catch (err) {
    ElMessage.error(messageOf(err, '读取剪贴板失败'))
  } finally {
    regionImporting.value = false
  }
}

async function confirmManualImport(): Promise<void> {
  if (regionImporting.value) return
  regionImporting.value = true
  try {
    if (await applyRegionText(manualText.value)) manualDialog.value = false
  } finally {
    regionImporting.value = false
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
    fov.value = info.viewport?.fov_h_deg ?? fov.value
    fovRange.value = info.fov_range ?? fovRange.value
    coverage.value = info.coverage ?? null
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
          <img
            v-if="isImage"
            ref="erpImage"
            :src="info.source_file?.open_url ?? ''"
            alt="源 ERP 全景图"
            class="erp-image"
            draggable="false"
            @click="onErpClick"
          />
          <video
            v-else
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
        </div>

        <div v-if="!isImage" class="scrub">
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

        <FileRow v-if="info.source_file" :entry="info.source_file" class="source-file" />

        <div class="meta-row">
          <span>yaw {{ yawText }}°</span>
          <span>pitch {{ pitchText }}°</span>
          <span>视口 {{ viewportLabel }}</span>
          <span v-if="!isImage" class="proxy-state">
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
        <h2 class="panel-title">{{ isImage ? '选区' : '选区与抽帧范围' }}</h2>
        <el-button link type="primary" :icon="Refresh" @click="resetToExtract">重置选区</el-button>
      </div>
      <div class="panel-body">
        <div v-if="isPatch" class="region-actions">
          <el-button :icon="CopyDocument" :loading="regionExporting" @click="exportRange">
            导出范围
          </el-button>
          <el-button :icon="Download" :loading="regionImporting" @click="importRange">
            导入范围
          </el-button>
        </div>

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

        <div class="fov-row">
          <span class="fov-label">视场角</span>
          <el-slider
            v-model="fov"
            class="fov-slider"
            :min="fovRange[0]"
            :max="fovRange[1]"
            :step="0.5"
            :show-tooltip="false"
            @change="onFovChange"
          />
          <span class="mono fov-value">{{ fov.toFixed(1) }}°</span>
        </div>
        <div class="coverage-line">
          <span v-if="coverage" class="mono">{{ coverageText }}</span>
          <span v-if="fovWarning" class="warn">超过 110°，边缘拉伸明显</span>
        </div>

        <div class="vp-wrap" :class="{ dragging: drag.dragging.value }">
          <img
            v-if="viewportReady"
            ref="vpImage"
            :src="vpUrl"
            alt="视口预览"
            draggable="false"
            @pointerdown="drag.begin"
          />
          <div v-else class="vp-empty muted">
            先在左侧全景图上点一下、再选视口尺寸，这里会出现视口预览
          </div>
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
              @pointerdown="startJoystick(0, 1)"
              @pointerup="stopJoystick"
              @pointerleave="stopJoystick"
            >
              ↑
            </button>
            <span />
            <button
              type="button"
              @pointerdown="startJoystick(-1, 0)"
              @pointerup="stopJoystick"
              @pointerleave="stopJoystick"
            >
              ←
            </button>
            <span class="joystick-dot">•</span>
            <button
              type="button"
              @pointerdown="startJoystick(1, 0)"
              @pointerup="stopJoystick"
              @pointerleave="stopJoystick"
            >
              →
            </button>
            <span />
            <button
              type="button"
              @pointerdown="startJoystick(0, -1)"
              @pointerup="stopJoystick"
              @pointerleave="stopJoystick"
            >
              ↓
            </button>
            <span />
          </div>
          <div class="nudge-row">
            <span class="muted">按住持续微调</span>
            <el-slider
              v-model="nudgeSpeed"
              class="nudge-slider"
              :min="0.2"
              :max="5"
              :step="0.1"
              :show-tooltip="false"
            />
            <span class="mono muted nudge-speed-value">{{ nudgeSpeed.toFixed(1) }}°/秒</span>
          </div>
        </div>

        <div v-if="!isImage" class="frames-row">
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

    <el-dialog
      v-model="manualDialog"
      :title="manualMode === 'copy' ? '手动复制选区范围' : '手动粘贴选区范围'"
      width="560px"
    >
      <p class="manual-hint muted">
        {{
          manualMode === 'copy'
            ? '浏览器拒绝了剪贴板写入。下面是选区 JSON，按 Ctrl+C 复制。'
            : '浏览器拒绝了剪贴板读取。把选区 JSON 粘贴到下面，再点「导入」。'
        }}
      </p>
      <el-input v-model="manualText" type="textarea" :rows="12" :readonly="manualMode === 'copy'" />
      <template #footer>
        <el-button @click="manualDialog = false">
          {{ manualMode === 'copy' ? '关闭' : '取消' }}
        </el-button>
        <el-button
          v-if="manualMode === 'paste'"
          type="primary"
          :loading="regionImporting"
          @click="confirmManualImport"
        >
          导入
        </el-button>
      </template>
    </el-dialog>
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

.erp-wrap video,
.erp-wrap .erp-image {
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

.source-file {
  margin-top: var(--space-2);
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

.region-actions {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  margin-bottom: var(--space-3);
}

.manual-hint {
  margin: 0 0 var(--space-3);
  font-size: var(--text-sm);
}

.vp-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
}

.fov-row {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-top: var(--space-2);
  font-size: var(--text-xs);
}

.fov-label {
  min-width: 48px;
  color: var(--color-text-muted);
}

.fov-slider {
  flex: 1;
  min-width: 120px;
}

.fov-value {
  min-width: 52px;
  text-align: right;
}

.coverage-line {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-3);
  margin-top: var(--space-1);
  color: var(--color-text-muted);
  font-size: var(--text-xs);
}

.coverage-line .warn {
  color: var(--color-danger);
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

.vp-empty {
  display: grid;
  place-items: center;
  min-height: 160px;
  padding: var(--space-3);
  text-align: center;
  font-size: var(--text-sm);
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

.nudge-row {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-top: var(--space-2);
  font-size: var(--text-xs);
}

.nudge-slider {
  flex: 1;
  min-width: 100px;
  max-width: 180px;
}

.nudge-speed-value {
  min-width: 52px;
  text-align: right;
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
