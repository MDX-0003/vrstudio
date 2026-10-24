<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { FullScreen, Refresh, VideoPause, VideoPlay } from '@element-plus/icons-vue'
import { ensureProxy, fetchProxy } from '@/api/workflow'
import { Erp360Compare } from '@/legacy/Erp360Compare'
import type { CaseInfo, OutputFile } from '@/types/case-info'

const props = defineProps<{ caseName: string; info: CaseInfo }>()

const selectedFile = ref<string>('')
const sourceProxy = ref(props.info.proxy?.state ?? 'missing')
const outputProxy = ref('missing')
const currentTime = ref(0)
const duration = ref(0)
const playing = ref(false)
const split = ref(50)
const viewerError = ref('')

const wrap = ref<HTMLElement | null>(null)
const videoA = ref<HTMLVideoElement | null>(null)
const videoB = ref<HTMLVideoElement | null>(null)
const canvas = ref<HTMLCanvasElement | null>(null)
let viewer: Erp360Compare | null = null
let proxyTimer: ReturnType<typeof setInterval> | null = null
let sourceWasReady = false
let outputWasReady = false

const outputs = computed<OutputFile[]>(() => props.info.outputs)
const selected = computed(
  () => outputs.value.find((item) => item.file === selectedFile.value) ?? null,
)
const sourceUrl = computed(() =>
  sourceProxy.value === 'ready'
    ? `/video/proxy/${encodeURIComponent(props.caseName)}`
    : props.info.source_url,
)
const outputUrl = computed(() => {
  const item = selected.value
  if (!item) return ''
  return outputProxy.value === 'ready' ? item.proxy_url : item.url
})
const timeText = computed(() => {
  const format = (value: number): string =>
    `${Math.floor(value / 60)}:${String(Math.floor(value % 60)).padStart(2, '0')}`
  return `${format(currentTime.value)} / ${format(duration.value)}`
})

function selectOutput(file: string): void {
  selectedFile.value = file
  outputProxy.value = 'missing'
  outputWasReady = false
  void ensureOutputProxy()
}

function onSelect(value: string | number): void {
  selectOutput(String(value))
}

function syncFromMaster(): void {
  const master = videoA.value
  const follower = videoB.value
  if (!master) return
  currentTime.value = master.currentTime
  if (follower && Math.abs(follower.currentTime - master.currentTime) > 0.05) {
    follower.currentTime = master.currentTime
  }
}

function onLoaded(): void {
  const master = videoA.value
  if (!master) return
  duration.value = Number.isFinite(master.duration) ? master.duration : 0
  currentTime.value = master.currentTime
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

function onSplit(value: number | number[]): void {
  split.value = Number(Array.isArray(value) ? value[0] : value)
  viewer?.setSplit(split.value / 100)
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
    if (source.state === 'ready') sourceWasReady = true
  } catch {
    // 源片代理状态是辅助信息，失败不打断对比。
  }
  const item = selected.value
  if (item) {
    try {
      const output = await fetchProxy(props.caseName, item.file)
      outputProxy.value = output.state
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
  if (!canvas.value || !videoA.value || !videoB.value) return
  viewer?.destroy()
  viewer = new Erp360Compare(canvas.value, videoA.value, videoB.value)
  viewerError.value = viewer.supported ? '' : '这个浏览器不支持 WebGL，全景对比看不了'
  viewer.setSplit(split.value / 100)
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
  outputProxy.value = 'missing'
  outputWasReady = false
  void ensureOutputProxy()
  startProxyPolling()
})

onMounted(async () => {
  await nextTick()
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
        <label class="ctl">
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
        <span class="muted">左路：源片{{ sourceProxy === 'ready' ? '（4K 代理）' : '' }}</span>
        <span class="muted">右路：{{ outputProxy === 'ready' ? '4K 代理' : outputProxy }}</span>
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
      </div>

      <div class="compare-stage">
        <video
          ref="videoA"
          :src="sourceUrl"
          muted
          playsinline
          preload="auto"
          @loadedmetadata="onLoaded"
          @timeupdate="syncFromMaster"
          @seeked="syncFromMaster"
          @play="playing = true"
          @pause="playing = false"
        />
        <video
          ref="videoB"
          :src="outputUrl"
          muted
          playsinline
          preload="auto"
          @loadedmetadata="onLoaded"
        />
        <canvas ref="canvas" class="compare-canvas" />
        <div class="split-line" :style="{ left: `${split}%` }" />
        <div v-if="viewerError" class="viewer-error">{{ viewerError }}</div>
      </div>

      <div class="compare-bar">
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
        <span class="split-label">分割线 {{ split }}%</span>
        <el-slider
          :model-value="split"
          :min="2"
          :max="98"
          :show-tooltip="false"
          class="split-slider"
          @update:model-value="onSplit"
        />
        <el-button size="small" @click="resetView">视角复位</el-button>
        <el-button size="small" :icon="FullScreen" @click="toggleFullscreen">全屏</el-button>
      </div>
      <p class="muted hint">
        拖动画面转头，滚轮变焦；左右方向键微调分割线。两路共用同一组视角与时钟，拖动分割线不改变视角。
      </p>
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
  width: 1px;
  height: 1px;
  opacity: 0;
  pointer-events: none;
}

.compare-canvas {
  display: block;
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
  background: rgba(255, 255, 255, 0.85);
  transform: translateX(-1px);
  pointer-events: none;
  box-shadow: 0 0 8px rgba(0, 0, 0, 0.6);
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

.split-slider {
  width: 140px;
}

.split-label {
  color: var(--color-text-muted);
  font-size: var(--text-xs);
  white-space: nowrap;
}

.hint {
  margin: 0;
  font-size: var(--text-xs);
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
