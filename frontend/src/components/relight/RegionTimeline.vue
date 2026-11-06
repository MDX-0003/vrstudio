<script setup lang="ts">
import { computed, onBeforeUnmount, ref } from 'vue'
import { ElMessage } from 'element-plus'
import type { RelightRegion, TimeRange } from '@/types/case-info'
import {
  MAX_RANGES,
  clamp,
  formatClock,
  insertRange,
  mergeAdjacent,
  moveRange,
  removeRange,
  resizeRange,
} from '@/utils/relightTime'

/**
 * 区域时间轴（决策 91）：一行一个区域，条是生效区间。
 *
 * 本组件只做绘制与指针交互，不读文件、不做并集：区间列表已经是解析端给的规范形式。
 * 同一区域的条不允许重叠；拖到相接时在提交前合并成一段。不同区域各有自己的行，
 * 时间上可以同时生效。
 */
const props = defineProps<{
  regions: RelightRegion[]
  selectedId: string | null
  duration: number
  fps: number | null
  playhead: number
  readonly: boolean
}>()

const emit = defineEmits<{
  update: [id: string, ranges: TimeRange[] | null]
  select: [id: string]
  seek: [seconds: number]
}>()

type DragKind = 'move' | 'start' | 'end'

const drag = ref<{
  id: string
  index: number
  kind: DragKind
  baseRanges: TimeRange[]
  originX: number
  width: number
  moved: boolean
} | null>(null)
const preview = ref<{ id: string; ranges: TimeRange[] } | null>(null)
let scrubbing = false
let scrubElement: HTMLElement | null = null

const ticks = computed(() => {
  if (!props.duration) return []
  const target = props.duration / 8
  const steps = [0.1, 0.2, 0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600]
  const step = steps.find((value) => value >= target) ?? target
  const out: number[] = []
  for (let time = 0; time < props.duration - 1e-9; time += step) {
    out.push(Number(time.toFixed(6)))
  }
  return out
})

function displayRanges(region: RelightRegion): TimeRange[] {
  if (preview.value?.id === region.id) return preview.value.ranges
  return region.ranges ?? []
}

function pct(seconds: number): string {
  if (!props.duration) return '0%'
  return `${clamp((seconds / props.duration) * 100, 0, 100)}%`
}

function widthPct(range: TimeRange): string {
  if (!props.duration) return '0%'
  return `${Math.max(0.2, ((range.end - range.start) / props.duration) * 100)}%`
}

function timeAt(event: PointerEvent | MouseEvent, element: HTMLElement): number {
  const rect = element.getBoundingClientRect()
  if (!rect.width) return 0
  return clamp((event.clientX - rect.left) / rect.width, 0, 1) * props.duration
}

function trackOf(target: EventTarget | null): HTMLElement | null {
  return target instanceof Element
    ? target.closest('.tl-track') as HTMLElement | null
    : null
}

function onTrackDown(event: PointerEvent, region: RelightRegion): void {
  emit('select', region.id)
  if (props.readonly || event.button !== 0) return
  emit('seek', timeAt(event, event.currentTarget as HTMLElement))
}

function onTrackDouble(event: MouseEvent, region: RelightRegion): void {
  if (props.readonly) return
  const track = trackOf(event.target)
  if (!track) return
  const current = displayRanges(region)
  if (current.length >= MAX_RANGES) {
    ElMessage.warning(`单个区域最多 ${MAX_RANGES} 段时间区间，先删掉一段`)
    return
  }
  const next = insertRange(current, timeAt(event, track), props.duration, props.fps)
  if (next === current) return
  const merged = mergeAdjacent(next)
  emit('update', region.id, merged.length ? merged : null)
}

function startDrag(event: PointerEvent, region: RelightRegion, index: number,
                   kind: DragKind): void {
  if (props.readonly || event.button !== 0) return
  const track = trackOf(event.target)
  if (!track) return
  event.preventDefault()
  event.stopPropagation()
  emit('select', region.id)
  drag.value = {
    id: region.id,
    index,
    kind,
    baseRanges: (region.ranges ?? []).map((item) => ({ ...item })),
    originX: event.clientX,
    width: track.getBoundingClientRect().width || 1,
    moved: false,
  }
  window.addEventListener('pointermove', onDragMove)
  window.addEventListener('pointerup', onDragEnd)
}

function onDragMove(event: PointerEvent): void {
  const state = drag.value
  if (!state) return
  const delta = ((event.clientX - state.originX) / state.width) * props.duration
  if (Math.abs(delta) > 1e-6) state.moved = true
  const next = state.kind === 'move'
    ? moveRange(state.baseRanges, state.index, delta, props.duration, props.fps)
    : resizeRange(state.baseRanges, state.index, state.kind, delta,
                  props.duration, props.fps)
  preview.value = { id: state.id, ranges: next }
}

function onDragEnd(): void {
  const state = drag.value
  if (state?.moved) {
    const ranges = preview.value?.id === state.id
      ? preview.value.ranges
      : state.baseRanges
    const merged = mergeAdjacent(ranges)
    emit('update', state.id, merged.length ? merged : null)
  }
  drag.value = null
  preview.value = null
  window.removeEventListener('pointermove', onDragMove)
  window.removeEventListener('pointerup', onDragEnd)
}

function deleteRange(region: RelightRegion, index: number): void {
  if (props.readonly) return
  const next = removeRange(displayRanges(region), index)
  emit('update', region.id, next.length ? next : null)
}

function seekFromRuler(event: PointerEvent): void {
  scrubbing = true
  scrubElement = event.currentTarget as HTMLElement
  emit('seek', timeAt(event, scrubElement))
  window.addEventListener('pointermove', onScrub)
  window.addEventListener('pointerup', onScrubEnd)
}

function onScrub(event: PointerEvent): void {
  if (!scrubbing || !scrubElement) return
  emit('seek', timeAt(event, scrubElement))
}

function onScrubEnd(): void {
  scrubbing = false
  scrubElement = null
  window.removeEventListener('pointermove', onScrub)
  window.removeEventListener('pointerup', onScrubEnd)
}

onBeforeUnmount(() => {
  window.removeEventListener('pointermove', onDragMove)
  window.removeEventListener('pointerup', onDragEnd)
  window.removeEventListener('pointermove', onScrub)
  window.removeEventListener('pointerup', onScrubEnd)
})
</script>

<template>
  <section class="panel timeline">
    <div class="tl-head">
      <b>时间轴</b>
      <span class="muted mono">{{ formatClock(duration) }}</span>
      <span class="spacer"></span>
      <span class="muted hint">
        {{ readonly ? '只读：读不到片长或帧率' : '双击空白新建 · 拖动平移 · 拖两端改起止 · 右键删除' }}
      </span>
    </div>
    <div class="tl-body">
      <div class="tl-ruler" @pointerdown="seekFromRuler">
        <span v-for="tick in ticks" :key="tick" class="tl-tick" :style="{ left: pct(tick) }">
          {{ formatClock(tick) }}
        </span>
        <div class="tl-playhead" :style="{ left: pct(playhead) }"></div>
      </div>
      <div class="tl-rows">
        <div
          v-for="region in regions"
          :key="region.id"
          class="tl-row"
          :class="{ on: region.id === selectedId }"
        >
          <span class="tl-label" :title="region.name" @click="emit('select', region.id)">
            {{ region.name }}
          </span>
          <div
            class="tl-track"
            @pointerdown="onTrackDown($event, region)"
            @dblclick="onTrackDouble($event, region)"
            @contextmenu.prevent
          >
            <span v-if="!displayRanges(region).length" class="tl-whole">整段</span>
            <div
              v-for="(range, index) in displayRanges(region)"
              :key="`${region.id}-${index}-${range.start}`"
              class="tl-bar"
              :class="{ dim: !region.enabled }"
              :style="{ left: pct(range.start), width: widthPct(range), background: region.color }"
              @pointerdown="startDrag($event, region, index, 'move')"
              @contextmenu.prevent.stop="deleteRange(region, index)"
            >
              <span class="tl-handle start" @pointerdown.stop="startDrag($event, region, index, 'start')"></span>
              <span class="tl-handle end" @pointerdown.stop="startDrag($event, region, index, 'end')"></span>
            </div>
            <div class="tl-playhead soft" :style="{ left: pct(playhead) }"></div>
          </div>
        </div>
        <p v-if="!regions.length" class="muted empty">还没有区域。</p>
      </div>
    </div>
  </section>
</template>

<style scoped>
.timeline {
  padding: var(--space-3) var(--space-4);
}

.tl-head,
.tl-ruler,
.tl-row {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.tl-head {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
}

.tl-head .spacer {
  flex: 1;
}

.hint {
  font-size: 11px;
}

.tl-body {
  margin-top: var(--space-2);
}

.tl-ruler {
  position: relative;
  height: 18px;
  margin-left: 70px;
  cursor: pointer;
}

.tl-tick {
  position: absolute;
  top: 0;
  font-size: 10px;
  color: var(--color-text-muted);
  transform: translateX(-50%);
  white-space: nowrap;
}

.tl-rows {
  margin-top: 4px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.tl-row {
  gap: var(--space-2);
}

.tl-row.on .tl-label {
  color: var(--color-text);
  font-weight: 600;
}

.tl-label {
  width: 70px;
  overflow: hidden;
  font-size: 11px;
  color: var(--color-text-muted);
  text-overflow: ellipsis;
  white-space: nowrap;
  cursor: pointer;
}

.tl-track {
  position: relative;
  flex: 1;
  height: 18px;
  overflow: hidden;
  border-radius: 4px;
  background: #eef2f7;
  cursor: crosshair;
}

.tl-whole {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 10px;
  color: var(--color-text-muted);
  pointer-events: none;
}

.tl-bar {
  position: absolute;
  top: 0;
  height: 100%;
  opacity: 0.55;
  cursor: grab;
}

.tl-bar.dim {
  opacity: 0.2;
}

.tl-handle {
  position: absolute;
  top: 0;
  width: 6px;
  height: 100%;
  cursor: ew-resize;
}

.tl-handle.start {
  left: 0;
}

.tl-handle.end {
  right: 0;
}

.tl-playhead {
  position: absolute;
  top: 0;
  width: 1px;
  height: 100%;
  background: var(--color-accent, #2563eb);
  pointer-events: none;
}

.tl-playhead.soft {
  opacity: 0.35;
}

.mono {
  font-family: var(--font-mono);
}

.muted {
  color: var(--color-text-muted);
}

.empty {
  margin: 4px 0;
  font-size: var(--text-xs);
}
</style>
