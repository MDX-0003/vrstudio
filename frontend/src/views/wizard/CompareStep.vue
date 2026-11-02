<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { FullScreen, Refresh, VideoPause, VideoPlay } from '@element-plus/icons-vue'
import { ensureProxy, fetchProxy } from '@/api/workflow'
import { Erp360Compare } from '@/legacy/Erp360Compare'
import type { CaseInfo, FullRedrawImageReport, OutputFile } from '@/types/case-info'

const props = defineProps<{ caseName: string; info: CaseInfo }>()

/** 图片流程（patchimg / fullRedrawImg）用并排静态图对比，不走 WebGL 视频对比。 */
const isImageFlow = computed(
  () => props.info.flow === 'patchimg' || props.info.flow === 'fullRedrawImg',
)
const isRelight = computed(() => props.info.flow === 'relight')
/** relight 的媒体由底版决定：图片底版走静态并排，视频底版走 360 对比器。 */
const relightStill = computed(
  () => isRelight.value && props.info.relight?.media === 'image',
)
/** 「静态并排」这一类：图片两条流程与图片底版的 relight。 */
const isStill = computed(() => isImageFlow.value || relightStill.value)
const sourceImageUrl = computed(() =>
  relightStill.value
    ? (props.info.relight?.gt?.entry.open_url ?? '')
    : (props.info.source_file?.open_url ?? ''),
)
const outputs = computed<OutputFile[]>(() => props.info.outputs)
const selectedFile = ref<string>('')
const selected = computed(
  () => outputs.value.find((item) => item.file === selectedFile.value) ?? null,
)
/**
 * 右路成片：图片流程与视频一样选「最新一份」，下拉可切；relight 图片底版用当前采用物。
 * 旧实现直接取 outputs[0]（升序第一份，即最旧一份），跑过多轮 merge 的 case 会看错图。
 */
const imageOutput = computed(() =>
  isRelight.value ? (props.info.relight?.current ?? null) : (selected.value ?? null),
)
const outputImageUrl = computed(() => imageOutput.value?.open_url ?? '')
/** 「打开原图」新标签直开，「下载」带 download=1 走 attachment。 */
const imageOpenUrl = computed(() => outputImageUrl.value)
const imageDownloadUrl = computed(() =>
  outputImageUrl.value ? `${outputImageUrl.value}&download=1` : '',
)
/** 左路标签：relight 播/比的是底版，不是「源片」。 */
const leftLabel = computed(() => (isRelight.value ? '底版' : '源片'))
const fullReport = computed<FullRedrawImageReport | null>(() =>
  props.info.flow === 'fullRedrawImg'
    ? ((props.info.image_report as FullRedrawImageReport) ?? null)
    : null,
)

const sourceProxy = ref(props.info.proxy?.state ?? 'missing')
const sourceProxyWidth = ref<number | null>(props.info.proxy?.width ?? null)
const outputProxy = ref('missing')
const outputProxyWidth = ref<number | null>(null)
const currentTime = ref(0)
const duration = ref(0)
const playing = ref(false)
const split = ref(50)
const viewerError = ref('')
// original = 播放原始分辨率（默认）；proxy = 播放代理，代理未就绪的路自动回落。
const quality = ref<'proxy' | 'original'>('original')

const wrap = ref<HTMLElement | null>(null)
const videoA = ref<HTMLVideoElement | null>(null)
const videoB = ref<HTMLVideoElement | null>(null)
const canvas = ref<HTMLCanvasElement | null>(null)
let viewer: Erp360Compare | null = null
let proxyTimer: ReturnType<typeof setInterval> | null = null
let sourceWasReady = false
let outputWasReady = false

const sourceUrl = computed(() =>
  quality.value === 'proxy' && sourceProxy.value === 'ready'
    ? `/video/proxy/${encodeURIComponent(props.caseName)}`
    : props.info.source_url,
)
const outputUrl = computed(() => {
  const item = selected.value
  if (!item) return ''
  return quality.value === 'proxy' && outputProxy.value === 'ready'
    ? item.proxy_url
    : (item.open_url ?? '')
})
const timeText = computed(() => {
  const format = (value: number): string =>
    `${Math.floor(value / 60)}:${String(Math.floor(value % 60)).padStart(2, '0')}`
  return `${format(currentTime.value)} / ${format(duration.value)}`
})

function proxySuffix(state: string, width: number | null): string {
  if (state !== 'ready') return ''
  return width ? `（${width} 代理）` : '（代理）'
}

const sourceLabel = computed(
  () =>
    `${isRelight.value ? '底版' : '源片'}${proxySuffix(sourceProxy.value, sourceProxyWidth.value)}`,
)
/** 代理状态是内部词（ready/missing/building…），页面上不能直接露出英文。 */
function proxyWord(state: string): string {
  if (state === 'ready') return '代理'
  if (state === 'building') return '代理生成中'
  return '原始分辨率'
}

const outputLabel = computed(() =>
  outputProxy.value === 'ready'
    ? `${outputProxyWidth.value ?? ''} 代理`.trim()
    : proxyWord(outputProxy.value),
)

function selectOutput(file: string): void {
  selectedFile.value = file
  outputProxy.value = 'missing'
  outputWasReady = false
  void ensureOutputProxy()
}

function onSelect(value: string | number): void {
  selectOutput(String(value))
}

// 漂移超过这个值才追帧；追太勤会让 B 路不断从关键帧重解，反而永远追不平。
const SYNC_THRESHOLD = 0.3

function syncFromMaster(): void {
  const master = videoA.value
  const follower = videoB.value
  if (!master) return
  currentTime.value = master.currentTime
  if (follower && !follower.seeking && Math.abs(follower.currentTime - master.currentTime) > SYNC_THRESHOLD) {
    follower.currentTime = master.currentTime
  }
}

function onLoaded(): void {
  const master = videoA.value
  if (!master) return
  duration.value = Number.isFinite(master.duration) ? master.duration : 0
  currentTime.value = master.currentTime
}

// B 路必须随 A 路一起播/停：它自己没有任何播放驱动，不播就只靠 seek 挪进度，
// 每 0.3 秒重解一次关键帧，右路画面成了一跳一跳的。
function onMasterPlay(): void {
  playing.value = true
  void videoB.value?.play()
}

function onMasterPause(): void {
  playing.value = false
  videoB.value?.pause()
}

function onFollowerLoaded(): void {
  const master = videoA.value
  const follower = videoB.value
  if (!master || !follower || follower.seeking) return
  follower.currentTime = master.currentTime
}

function togglePlay(): void {
  const master = videoA.value
  if (!master) return
  if (master.paused) {
    void master.play()
  } else {
    master.pause()
  }
}

function onSeek(value: number | number[]): void {
  const target = Number(Array.isArray(value) ? value[0] : value)
  const master = videoA.value
  if (!master || !Number.isFinite(master.duration)) return
  master.currentTime = target
  if (videoB.value) videoB.value.currentTime = target
  currentTime.value = target
}

// 分割线直接在画面上拖（旧版交互）：线条自己是拖拽把手，事件不落到画布上免得带动视角。
const splitDragging = ref(false)

function onLineDown(event: PointerEvent): void {
  splitDragging.value = true
  ;(event.currentTarget as HTMLElement).setPointerCapture(event.pointerId)
  event.stopPropagation()
}

function onLineMove(event: PointerEvent): void {
  if (!splitDragging.value || !canvas.value) return
  event.stopPropagation()
  const rect = canvas.value.getBoundingClientRect()
  const fx = ((event.clientX - rect.left) / Math.max(1, rect.width)) * 100
  split.value = Math.min(98, Math.max(2, fx))
  viewer?.setSplit(split.value / 100)
}

function onLineUp(): void {
  splitDragging.value = false
}

function resetView(): void {
  viewer?.resetView()
}

async function toggleFullscreen(): Promise<void> {
  if (!wrap.value) return
  if (document.fullscreenElement) {
    await document.exitFullscreen()
  } else {
    await wrap.value.requestFullscreen()
  }
}

async function ensureSourceProxy(): Promise<void> {
  if (sourceWasReady || sourceProxy.value === 'ready' || sourceProxy.value === 'disabled') return
  try {
    const payload = await ensureProxy(props.caseName)
    sourceProxy.value = payload.state
    sourceProxyWidth.value = payload.width ?? null
    if (payload.state === 'ready') sourceWasReady = true
  } catch {
    sourceProxy.value = 'missing'
  }
}

async function ensureOutputProxy(): Promise<void> {
  const item = selected.value
  if (
    !item ||
    outputWasReady ||
    outputProxy.value === 'ready' ||
    outputProxy.value === 'disabled'
  ) {
    return
  }
  try {
    const payload = await ensureProxy(props.caseName, item.file)
    outputProxy.value = payload.state
    outputProxyWidth.value = payload.width ?? null
    if (payload.state === 'ready') outputWasReady = true
  } catch {
    outputProxy.value = 'missing'
  }
}

async function pollProxies(): Promise<void> {
  if (document.hidden) return
  try {
    const source = await fetchProxy(props.caseName)
    sourceProxy.value = source.state
    sourceProxyWidth.value = source.width ?? null
    if (source.state === 'ready') sourceWasReady = true
  } catch {
    // 源片代理状态是辅助信息，失败不打断对比。
  }
  const item = selected.value
  if (item) {
    try {
      const output = await fetchProxy(props.caseName, item.file)
      outputProxy.value = output.state
      outputProxyWidth.value = output.width ?? null
      if (output.state === 'ready') outputWasReady = true
    } catch {
      outputProxy.value = 'missing'
    }
  }
  if (
    (sourceProxy.value === 'ready' || sourceProxy.value === 'disabled') &&
    (outputProxy.value === 'ready' || outputProxy.value === 'disabled')
  ) {
    stopProxyPolling()
  }
}

function startProxyPolling(): void {
  if (proxyTimer) clearInterval(proxyTimer)
  proxyTimer = setInterval(() => void pollProxies(), 2000)
}

function stopProxyPolling(): void {
  if (proxyTimer) {
    clearInterval(proxyTimer)
    proxyTimer = null
  }
}

function initViewer(): void {
  if (!canvas.value) return
  const a = isStill.value ? imageA : videoA.value
  const b = isStill.value ? imageB : videoB.value
  if (!a || !b) return
  viewer?.destroy()
  viewer = new Erp360Compare(canvas.value, a, b)
  viewerError.value = viewer.supported ? '' : '这个浏览器不支持 WebGL，全景对比看不了'
  viewer.setSplit(split.value / 100)
}

/**
 * 静态图的两路来源不进 DOM：直接交给 WebGL 查看器当纹理，图片 `complete` 后
 * 只在第一帧上传一次。换图或换成片时重建查看器。
 */
let imageA: HTMLImageElement | null = null
let imageB: HTMLImageElement | null = null

function loadSource(src: string): HTMLImageElement | null {
  if (!src) return null
  const image = new Image()
  image.src = src
  return image
}

function initStillViewer(): void {
  imageA = loadSource(sourceImageUrl.value)
  imageB = loadSource(outputImageUrl.value)
  initViewer()
}

watch(
  () => props.info.outputs,
  (rows) => {
    if (!rows.length) {
      selectedFile.value = ''
      return
    }
    if (!rows.some((item) => item.file === selectedFile.value)) {
      selectedFile.value = rows[rows.length - 1].file
    }
  },
  { immediate: true },
)

watch(selectedFile, () => {
  if (isStill.value) {
    initStillViewer()
    return
  }
  outputProxy.value = 'missing'
  outputWasReady = false
  void ensureOutputProxy()
  startProxyPolling()
})

// case 刷新后源图或成片地址可能变化；静态图直接重建查看器。
watch([sourceImageUrl, outputImageUrl], () => {
  if (isStill.value) initStillViewer()
})

onMounted(async () => {
  await nextTick()
  if (isStill.value) {
    if (selected.value) selectedFile.value = selected.value.file
    initStillViewer()
    return
  }
  if (selected.value) {
    selectedFile.value = selected.value.file
  }
  initViewer()
  void ensureSourceProxy()
  void ensureOutputProxy()
  startProxyPolling()
})

onUnmounted(() => {
  viewer?.destroy()
  viewer = null
  stopProxyPolling()
})

function onKeydown(event: KeyboardEvent): void {
  // 空格只服务视频播放；左右方向键调分割线两条路一致。
  if (!isStill.value && event.code === 'Space') {
    const tag = (event.target as HTMLElement | null)?.tagName ?? ''
    if (['BUTTON', 'INPUT', 'SELECT', 'TEXTAREA'].includes(tag)) return
    event.preventDefault()
    togglePlay()
    return
  }
  if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return
  event.preventDefault()
  const delta = event.key === 'ArrowLeft' ? -2 : 2
  split.value = Math.min(98, Math.max(2, split.value + delta))
  viewer?.setSplit(split.value / 100)
}
</script>

<template>
  <div ref="wrap" class="compare-page" tabindex="0" @keydown="onKeydown">
    <div v-if="!outputs.length" class="panel">
      <el-empty description="还没有可对比的成片；先完成生成与合并" />
    </div>

    <template v-else>
      <div class="compare-top">
        <label v-if="isStill && !isRelight" class="ctl">
          <span>右路成片</span>
          <el-select
            :model-value="selectedFile"
            size="small"
            style="width: 260px"
            @update:model-value="onSelect"
          >
            <el-option
              v-for="item in outputs"
              :key="item.file"
              :label="item.file"
              :value="item.file"
            />
          </el-select>
        </label>
        <template v-if="!isStill">
          <label class="ctl">
            <span>播放画质</span>
            <el-select v-model="quality" size="small" style="width: 130px">
              <el-option label="代理" value="proxy" />
              <el-option label="原始分辨率" value="original" />
            </el-select>
          </label>
          <span class="muted">左路：{{ sourceLabel }}</span>
          <span class="muted">右路：{{ outputLabel }}</span>
          <el-button
            size="small"
            :icon="Refresh"
            @click="
              () => {
                void ensureSourceProxy()
                void ensureOutputProxy()
                startProxyPolling()
              }
            "
          >
            刷新代理
          </el-button>
        </template>
        <template v-else>
          <span class="muted">左路：{{ leftLabel }}</span>
          <span class="muted">右路：{{ imageOutput?.name ?? '交付图缺失' }}</span>
          <a
            v-if="imageOpenUrl"
            class="file-action"
            :href="imageOpenUrl"
            target="_blank"
            rel="noopener"
          >
            打开原图
          </a>
          <a v-if="imageDownloadUrl" class="file-action" :href="imageDownloadUrl" download>
            下载
          </a>
        </template>
      </div>

      <div v-if="isStill && !outputImageUrl" class="panel">
        <el-empty description="交付图还没有生成；先在拼接步出片" />
      </div>
      <template v-else>
        <div class="compare-stage">
          <template v-if="!isStill">
            <video
              ref="videoA"
              :src="sourceUrl"
              muted
              loop
              playsinline
              preload="auto"
              @loadedmetadata="onLoaded"
              @timeupdate="syncFromMaster"
              @seeked="syncFromMaster"
              @play="onMasterPlay"
              @pause="onMasterPause"
            />
            <video
              ref="videoB"
              :src="outputUrl"
              muted
              loop
              playsinline
              preload="auto"
              @loadedmetadata="onFollowerLoaded"
            />
          </template>
          <canvas ref="canvas" class="compare-canvas" />
          <div
            class="split-line"
            :class="{ dragging: splitDragging }"
            :style="{ left: `${split}%` }"
            title="拖动调整分割线位置"
            @pointerdown="onLineDown"
            @pointermove="onLineMove"
            @pointerup="onLineUp"
            @pointercancel="onLineUp"
          />
          <div v-if="viewerError" class="viewer-error">{{ viewerError }}</div>
        </div>

        <div class="compare-bar">
          <template v-if="!isStill">
            <el-button :icon="playing ? VideoPause : VideoPlay" size="small" @click="togglePlay">
              {{ playing ? '暂停' : '播放' }}
            </el-button>
            <el-slider
              :model-value="currentTime"
              :min="0"
              :max="duration || 0"
              :show-tooltip="false"
              class="time-slider"
              @update:model-value="onSeek"
            />
            <span class="mono muted">{{ timeText }}</span>
          </template>
          <el-button size="small" @click="resetView">视角复位</el-button>
          <el-button size="small" :icon="FullScreen" @click="toggleFullscreen">全屏</el-button>
        </div>
        <p class="muted hint">
          <template v-if="isStill">
            拖动画面转头，滚轮变焦；在画面上左右拖动分割线调整对比位置（左右方向键微调）。两侧是同一套视角，分割线左右各显示一张图。
          </template>
          <template v-else>
            拖动画面转头，滚轮变焦，空格播放/暂停；在画面上左右拖动分割线调整对比位置（左右方向键微调）。两路自动循环播放，共用同一组视角与时钟。
          </template>
        </p>
      </template>

      <div v-if="fullReport" class="seam-box">
        <div class="seam-head">
          <span>接缝结论</span>
          <el-tag size="small" effect="light" :type="fullReport.verdict === 'ok' ? 'success' : fullReport.verdict === 'bad' ? 'danger' : 'warning'">
            {{ fullReport.verdict }}
          </el-tag>
          <el-tag size="small" effect="light" :type="fullReport.gate === 'ok' ? 'success' : 'warning'">
            gate={{ fullReport.gate }}
          </el-tag>
          <span class="mono muted">宽高漂移 {{ fullReport.aspect_drift_pct.toFixed(1) }}%</span>
        </div>
        <p class="muted hint">
          {{ fullReport.reasons.length ? fullReport.reasons.join('；') : '没有接缝告警。' }}
        </p>
      </div>
    </template>
  </div>
</template>

<style scoped>
.compare-page {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  outline: none;
}

.compare-top {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-3);
}

.compare-stage {
  position: relative;
  overflow: hidden;
  border-radius: var(--radius-lg);
  background: var(--color-video-bg);
  min-height: 420px;
}

.compare-stage video {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
  /* 不能是 opacity:0 或 1px 尺寸：不可见的视频元素会失去 GPU 直拷贝快路径，
     texImage2D 退回 CPU 读回整帧导致卡顿；canvas 不透明盖在上面，低透明度不会露出来。 */
  opacity: 0.01;
  pointer-events: none;
  z-index: 0;
}

.compare-canvas {
  display: block;
  position: relative;
  z-index: 1;
  width: 100%;
  height: min(66vh, 720px);
  cursor: grab;
  touch-action: none;
}

.split-line {
  position: absolute;
  top: 0;
  bottom: 0;
  width: 2px;
  z-index: 2;
  background: rgba(255, 255, 255, 0.85);
  transform: translateX(-1px);
  pointer-events: auto;
  cursor: ew-resize;
  box-shadow: 0 0 8px rgba(0, 0, 0, 0.6);
}

/* 视觉 2px，命中区放宽到 14px，方便在视频上直接抓取。 */
.split-line::before {
  content: '';
  position: absolute;
  inset: 0 -6px;
}

.split-line.dragging {
  background: rgba(255, 255, 255, 1);
}

.viewer-error {
  position: absolute;
  inset: 0;
  display: grid;
  place-items: center;
  color: var(--color-video-text);
  font-size: var(--text-sm);
}

.compare-bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-2) var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
}

.time-slider {
  flex: 1;
  min-width: 160px;
}

.hint {
  margin: 0;
  font-size: var(--text-xs);
}

/* 「打开原图 / 下载」：与 el-button small 同尺寸，语义仍是链接（新标签/下载）。 */
.file-action {
  display: inline-flex;
  align-items: center;
  padding: 5px 12px;
  border: 1px solid var(--el-border-color);
  border-radius: var(--el-border-radius-base);
  color: var(--color-text);
  font-size: var(--text-xs);
  text-decoration: none;
}

.file-action:hover {
  border-color: var(--el-color-primary);
  color: var(--el-color-primary);
}

.seam-box {
  padding: var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
}

.seam-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--text-sm);
  font-weight: 600;
}

.seam-box .hint {
  margin-top: var(--space-2);
}

.compare-page:fullscreen {
  padding: var(--space-4);
  background: var(--color-video-bg);
}

.compare-page:fullscreen .compare-stage {
  flex: 1;
}

.compare-page:fullscreen .compare-canvas {
  height: calc(100vh - 140px);
}
</style>
