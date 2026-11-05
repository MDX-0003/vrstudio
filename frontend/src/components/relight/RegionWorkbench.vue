<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import ErpRegionViewer from './ErpRegionViewer.vue'
import ParamRow from './ParamRow.vue'
import { PARAMS, emptyRegionParams } from '@/constants/relightParams'
import type { DefaultParams, ParamDesc } from '@/constants/relightParams'
import { saveRelightRegions } from '@/api/relight'
import { saveRelightDraft } from '@/api/case'
import type { RelightPayload, RelightRegion } from '@/types/case-info'
import type { LonLat } from '@/utils/erpMath'
import { regionShapeIssue } from '@/utils/erpMath'
import type { RegionShapeIssue } from '@/utils/erpMath'

/**
 * 区域工作台（决策 88，方案 A）：视图占主位，右侧是区域列表与选中区域的参数组，
 * 默认参数折叠在下面。时间轴是第二阶段，这里只放整段占位。
 */
const props = defineProps<{
  caseName: string
  payload: RelightPayload
  sourceUrl: string | null
  defaults: DefaultParams
}>()

const emit = defineEmits<{
  'update:defaults': [value: DefaultParams]
  busy: [value: boolean]
}>()

const COLORS = ['#f59e0b', '#38bdf8', '#a3e635', '#f472b6', '#c084fc']
const MAX_REGIONS = 16
const MAX_VERTICES = 64

const regions = ref<RelightRegion[]>(props.payload.regions?.regions.slice() ?? [])
const selectedId = ref<string | null>(regions.value[0]?.id ?? null)
const mode = ref<'orbit' | 'poly'>('orbit')
const draft = ref<LonLat[] | null>(null)
const renamingId = ref<string | null>(null)
/** 高亮选区：勾选时保持默认的醒目叠加，取消后只留轻量的几何参考。 */
const highlightSelection = ref(true)
const saving = ref(false)
const saveState = ref<'idle' | 'pending' | 'saved' | 'error'>('idle')
const error = ref<string | null>(props.payload.regions_error)
let regionTimer: number | null = null
let draftTimer: number | null = null
/** 本地改动序号：响应回来时用它判断期间是否又改过，避免旧快照盖掉新值。 */
let changeSeq = 0
let draftChain: Promise<unknown> = Promise.resolve()

const selected = computed(
  () => regions.value.find((region) => region.id === selectedId.value) ?? null,
)

/** 有未落盘的区域改动时禁止跑任务，否则可能用上一次的 regions.json。 */
const busy = computed(() => saveState.value === 'pending' || saving.value)
watch(busy, (value) => emit('busy', value))

function fallbackFor(desc: ParamDesc): number {
  if (desc.id === 'strength') return props.defaults.strength
  if (desc.id === 'local_shading') return props.defaults.local_shading
  return 0
}

function defaultOf(desc: ParamDesc): number | { knee: number; max: number } | null {
  if (desc.id === 'strength') return props.defaults.strength
  if (desc.id === 'local_shading') return props.defaults.local_shading
  return props.defaults.clip
}

function regionValue(region: RelightRegion, desc: ParamDesc): number | { knee: number; max: number } | null {
  if (desc.id === 'strength') return region.params.strength
  if (desc.id === 'local_shading') return region.params.local_shading
  return region.params.clip
}

function setRegionValue(
  region: RelightRegion,
  desc: ParamDesc,
  value: number | { knee: number; max: number } | null,
): void {
  if (desc.id === 'clip') {
    region.params.clip = value as { knee: number; max: number } | null
  } else {
    region.params[desc.id] = value as number | null
  }
  scheduleRegionSave()
}

function setDefault(desc: ParamDesc, value: number | { knee: number; max: number } | null): void {
  const next: DefaultParams = { ...props.defaults }
  if (desc.id === 'clip') {
    next.clip = value as { knee: number; max: number } | null
  } else {
    next[desc.id] = value as number
  }
  emit('update:defaults', next)
  scheduleDraftSave(next)
}

function scheduleRegionSave(): void {
  changeSeq += 1
  saveState.value = 'pending'
  if (regionTimer) window.clearTimeout(regionTimer)
  regionTimer = window.setTimeout(() => void saveRegions(), 700)
}

async function saveRegions(): Promise<void> {
  if (saving.value) return          // 在 finally 里补排一次
  const seq = changeSeq
  const snapshot = regions.value.map((region) => ({ ...region, ranges: null }))
  saving.value = true
  try {
    await saveRelightRegions(props.caseName, { version: 1, regions: snapshot })
    error.value = null
  } catch (err) {
    saveState.value = 'error'
    error.value = err instanceof Error ? err.message : '区域保存失败'
    ElMessage.error(error.value)
    saving.value = false
    return
  }
  saving.value = false
  if (changeSeq !== seq) scheduleRegionSave()
  else saveState.value = 'saved'
}

function scheduleDraftSave(next: DefaultParams): void {
  if (draftTimer) window.clearTimeout(draftTimer)
  draftTimer = window.setTimeout(() => {
    // 串行化：后一次一定排在前一次之后，最后一次的值才是落盘值。
    draftChain = draftChain.then(() =>
      saveRelightDraft(props.caseName, {
        strength: next.strength,
        local_shading: next.local_shading,
        highlight_clip: next.clip !== null,
        clip_knee: next.clip?.knee ?? 0.72,
        clip_max: next.clip?.max ?? 0.98,
      }).catch(() => undefined),
    )
  }, 700)
}

function select(id: string): void {
  selectedId.value = id
}

/** 已存在的区域也可能不合法（旧数据或手改）：列表里标出来，避免静默画错。 */
function regionInvalid(region: RelightRegion): boolean {
  return regionShapeIssue(region.verts) !== null
}

/** 把形状问题翻成用户能据此改形的说明。 */
function shapeIssueMessage(issue: RegionShapeIssue): string {
  if (issue.kind === 'winding') {
    const turns = Math.abs(issue.winding)
    return issue.hasPole
      ? `这个区域含极点、边界又绕极点转了 ${turns} 圈（经度跨度 ${Math.round(issue.span)}°），`
        + '单个闭合多边形表示不了；请拆成东西两块。'
      : `这个区域绕着极点转了 ${turns} 圈（经度跨度 ${Math.round(issue.span)}°），`
        + '单个闭合多边形表示不了；请拆成两块。'
  }
  if (issue.kind === 'span') {
    return `这个区域的经度跨度 ${Math.round(issue.span)}°，绕出去再折返的带在切平面上会自重叠，`
      + '单个闭合多边形表示不了；请沿经线拆成两块。'
  }
  return '多边形的边与边交叉，请调整顶点后再闭合。'
}

function startDraw(): void {
  if (regions.value.length >= MAX_REGIONS) {
    ElMessage.warning(`区域最多 ${MAX_REGIONS} 个`)
    return
  }
  mode.value = 'poly'
  draft.value = []
}

function addPoint(lon: number, lat: number): void {
  if (!draft.value) draft.value = []
  if (draft.value.length >= MAX_VERTICES) return
  draft.value = [...draft.value, [lon, lat]]
}

function closeDraft(): void {
  const raw = draft.value
  // 双击闭合时浏览器会先补两次单击，去掉落在同一个点上的重复顶点。
  const points: LonLat[] = []
  for (const point of raw ?? []) {
    const last = points[points.length - 1]
    if (!last || Math.hypot(point[0] - last[0], point[1] - last[1]) > 0.05) {
      points.push(point)
    }
  }
  if (points.length < 3) {
    draft.value = null
    mode.value = 'orbit'
    return
  }
  // 切平面上的单个闭合多边形装不下两类形状：绕极点转圈，以及绕出去再折返、
  // 跨度超过一整圈的带。自交单独拒绝。
  const issue = regionShapeIssue(points)
  if (issue) {
    ElMessage.error(shapeIssueMessage(issue))
    return
  }
  const index = regions.value.length + 1
  const region: RelightRegion = {
    id: `r${Date.now().toString(36)}`,
    name: `区域 ${index}`,
    color: COLORS[(index - 1) % COLORS.length],
    enabled: true,
    verts: points,
    feather_deg: 3,
    params: emptyRegionParams(),
    ranges: null,
  }
  regions.value = [...regions.value, region]
  selectedId.value = region.id
  draft.value = null
  mode.value = 'orbit'
  scheduleRegionSave()
}

function moveVertex(id: string, index: number, lon: number, lat: number): void {
  const region = regions.value.find((item) => item.id === id)
  if (!region) return
  region.verts[index] = [lon, lat]
  scheduleRegionSave()
}

function insertVertex(id: string, index: number, lon: number, lat: number): void {
  const region = regions.value.find((item) => item.id === id)
  if (!region || region.verts.length >= MAX_VERTICES) return
  region.verts.splice(index, 0, [lon, lat])
  scheduleRegionSave()
}

function deleteVertex(id: string, index: number): void {
  const region = regions.value.find((item) => item.id === id)
  if (!region || region.verts.length <= 3) return
  region.verts.splice(index, 1)
  scheduleRegionSave()
}

function deleteRegion(id: string): void {
  regions.value = regions.value.filter((region) => region.id !== id)
  if (selectedId.value === id) selectedId.value = regions.value[0]?.id ?? null
  scheduleRegionSave()
}

function startRename(id: string): void {
  renamingId.value = id
}

function finishRename(id: string, event: Event): void {
  const region = regions.value.find((item) => item.id === id)
  const value = (event.target as HTMLInputElement).value.trim()
  if (region && value) region.name = value
  renamingId.value = null
  scheduleRegionSave()
}

function cycleColor(id: string): void {
  const region = regions.value.find((item) => item.id === id)
  if (!region) return
  const index = COLORS.indexOf(region.color)
  region.color = COLORS[(index + 1) % COLORS.length]
  scheduleRegionSave()
}

function toggleEnabled(id: string): void {
  const region = regions.value.find((item) => item.id === id)
  if (!region) return
  region.enabled = !region.enabled
  scheduleRegionSave()
}

watch(
  () => props.payload.regions,
  (doc) => {
    // 本地有未落盘的改动时，服务端返回的旧快照不能覆盖正在编辑的值。
    if (busy.value) return
    const next = doc?.regions ?? []
    if (JSON.stringify(next) === JSON.stringify(regions.value)) return
    regions.value = next.slice()
    if (!regions.value.some((region) => region.id === selectedId.value)) {
      selectedId.value = regions.value[0]?.id ?? null
    }
  },
)

watch(() => props.payload.regions_error, (value) => {
  error.value = value
})

function onKey(event: KeyboardEvent): void {
  if (mode.value !== 'poly') return
  if (event.key === 'Enter') {
    event.preventDefault()
    closeDraft()
  } else if (event.key === 'Escape') {
    draft.value = null
    mode.value = 'orbit'
  } else if (event.key === 'Backspace') {
    // 落错点时回退上一个顶点，不必整个重画。
    event.preventDefault()
    if (draft.value?.length) draft.value = draft.value.slice(0, -1)
  }
}

onMounted(() => window.addEventListener('keydown', onKey))

onBeforeUnmount(() => {
  window.removeEventListener('keydown', onKey)
  if (regionTimer) window.clearTimeout(regionTimer)
  if (draftTimer) window.clearTimeout(draftTimer)
})
</script>

<template>
  <div class="workbench">
    <div class="stage-column">
      <div class="toolbar">
        <button
          class="btn"
          :class="{ on: mode === 'orbit' }"
          type="button"
          @click="mode = 'orbit'; draft = null"
        >选择</button>
        <button
          class="btn"
          :class="{ on: mode === 'poly' }"
          type="button"
          @click="startDraw"
        >多边形</button>
        <button class="btn" type="button" disabled>套索</button>
        <span class="spacer"></span>
        <label class="toggle">
          <input v-model="highlightSelection" type="checkbox" />
          高亮选区
        </label>
        <span class="muted hint">绘制时拖动画面转向、单击加点 · 选中后拖顶点改形、边上单击加点、右键删点</span>
      </div>

      <ErpRegionViewer
        :source-url="sourceUrl"
        :media="payload.media ?? 'image'"
        :regions="regions"
        :selected-id="selectedId"
        :draft="draft"
        :mode="mode"
        :highlight="highlightSelection"
        @select="select"
        @add-point="addPoint"
        @close-draft="closeDraft"
        @move-vertex="moveVertex"
        @insert-vertex="insertVertex"
        @delete-vertex="deleteVertex"
      />

      <section class="panel timeline">
        <div class="tl-head">
          <b>时间轴</b>
          <span class="chip plan">第二阶段</span>
          <span class="muted">首版所有区域整段生效；这一块先占位，不接线。</span>
        </div>
        <div class="tl-rows">
          <div v-for="region in regions" :key="region.id" class="tl-row">
            <span class="tl-label">{{ region.name }}</span>
            <div class="tl-track"><div class="tl-bar" :style="{ background: region.color }"></div></div>
          </div>
          <p v-if="!regions.length" class="muted empty">还没有区域。</p>
        </div>
      </section>
    </div>

    <div class="sidebar">
      <section class="panel">
        <div class="panel-head">
          <h2>区域（{{ regions.length }}）</h2>
          <span class="spacer"></span>
          <button class="btn" type="button" @click="startDraw">＋ 新建</button>
        </div>
        <div class="panel-body list">
          <div
            v-for="region in regions"
            :key="region.id"
            class="region-row"
            :class="{ on: region.id === selectedId }"
            @click="select(region.id)"
          >
            <span class="dot" :style="{ background: region.color }" title="点击换颜色" @click.stop="cycleColor(region.id)"></span>
            <input
              v-if="renamingId === region.id"
              class="rename"
              :value="region.name"
              autofocus
              @keydown.enter.prevent="finishRename(region.id, $event)"
              @blur="finishRename(region.id, $event)"
            />
            <span v-else class="name" @dblclick.stop="startRename(region.id)">{{ region.name }}</span>
            <span v-if="regionInvalid(region)" class="chip warn" title="这个区域绕极点转圈、经度跨度超过一整圈或边自交，单个闭合多边形表示不了">⚠</span>
            <span class="chip small">整段</span>
            <button class="icon" type="button" :title="region.enabled ? '停用（不参与合成）' : '启用'" @click.stop="toggleEnabled(region.id)">
              {{ region.enabled ? '◉' : '○' }}
            </button>
            <button class="icon" type="button" title="删除区域" @click.stop="deleteRegion(region.id)">✕</button>
          </div>
          <p v-if="!regions.length" class="muted empty">
            点「多边形」后在视图里逐点围出区域，双击或回车闭合。
          </p>
          <p class="muted tip">
            拖顶点改形，边上单击加点，右键删点；双击名称改名，点色块换色。
          </p>
        </div>
      </section>

      <section class="panel">
        <div class="panel-head">
          <h2>选中区域</h2>
          <span class="spacer"></span>
          <span class="chip small">{{ selected?.name ?? '未选中' }}</span>
        </div>
        <div class="panel-body">
          <template v-if="selected">
            <div class="param-group">
              <ParamRow
                v-for="desc in PARAMS"
                :key="desc.id"
                :desc="desc"
                :value="regionValue(selected, desc)"
                scope="region"
                :fallback="fallbackFor(desc)"
                @update:value="setRegionValue(selected, desc, $event)"
              />
              <div class="hr"></div>
              <label class="feather">
                <span>羽化</span>
                <input v-model.number="selected.feather_deg" type="range" min="0" max="12" step="0.5" @input="scheduleRegionSave" />
                <span class="mono">{{ selected.feather_deg.toFixed(1) }}°</span>
              </label>
              <div class="hr"></div>
              <div class="row-meta">
                <span>时间</span>
                <span class="chip small">整段</span>
                <span class="chip plan small">区间 · 第二阶段</span>
              </div>
              <p class="muted tip">＋ 参数组可扩展：白平衡 / 色温 / 局部压缩……</p>
            </div>
          </template>
          <p v-else class="muted empty">在视图里点一个区域，或从上面的列表选一个。</p>
        </div>
      </section>

      <section class="panel">
        <div class="panel-head">
          <h2>默认参数</h2>
          <span class="spacer"></span>
          <span class="muted small">{{ saveState === 'pending' ? '保存中…' : saveState === 'saved' ? '已保存' : saveState === 'error' ? '保存失败' : '' }}</span>
        </div>
        <div class="panel-body">
          <div class="param-group">
            <ParamRow
              v-for="desc in PARAMS"
              :key="desc.id"
              :desc="desc"
              :value="defaultOf(desc)"
              scope="default"
              :fallback="fallbackFor(desc)"
              @update:value="setDefault(desc, $event)"
            />
          </div>
          <p class="muted tip">未覆盖的区域与时间沿用这一组。</p>
        </div>
      </section>

      <el-alert v-if="error" type="warning" :closable="false" :title="error" />
    </div>
  </div>
</template>

<style scoped>
.workbench {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 372px;
  gap: var(--space-4);
  align-items: start;
}

.stage-column {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  min-width: 0;
}

.toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
}

.toolbar .spacer {
  flex: 1;
}

.hint {
  font-size: 11px;
}

.toggle {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 11px;
  color: var(--color-text-muted);
  cursor: pointer;
  white-space: nowrap;
}

.btn {
  padding: 4px 10px;
  border: 1px solid var(--color-border-strong);
  border-radius: var(--radius-md);
  background: var(--color-surface);
  font-size: var(--text-xs);
  cursor: pointer;
}

.btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.btn.on {
  border-color: #bfdbfe;
  background: var(--color-primary-soft);
  color: #1d4ed8;
}

.panel {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-1);
}

.panel-head {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-4);
  border-bottom: 1px solid var(--color-border);
}

.panel-head h2 {
  margin: 0;
  font-size: var(--text-sm);
}

.panel-head .spacer {
  flex: 1;
}

.panel-body {
  padding: var(--space-4);
}

.sidebar {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.list {
  padding: 8px;
}

.region-row {
  display: grid;
  grid-template-columns: 10px 1fr auto auto auto;
  align-items: center;
  gap: var(--space-2);
  padding: 6px 8px;
  border-radius: var(--radius-md);
  cursor: pointer;
}

.region-row:hover {
  background: var(--color-surface-muted);
}

.region-row.on {
  background: var(--color-primary-soft);
}

.dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  cursor: pointer;
}

.name {
  overflow: hidden;
  font-size: var(--text-sm);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.rename {
  width: 100%;
  min-width: 0;
  padding: 1px 4px;
  border: 1px solid var(--color-primary);
  border-radius: var(--radius-sm);
  font-size: var(--text-sm);
}

.chip {
  padding: 1px 6px;
  border: 1px solid var(--color-border);
  border-radius: 999px;
  background: var(--color-surface-muted);
  color: var(--color-text-muted);
  font-size: 10px;
}

.chip.plan {
  border-style: dashed;
  color: var(--color-text-faint);
}

.chip.warn {
  border-color: #fde68a;
  background: var(--color-warning-soft);
  color: #92400e;
}

.chip.small {
  font-size: 10px;
}

.icon {
  padding: 2px 4px;
  border: 0;
  border-radius: 3px;
  background: transparent;
  color: var(--color-text-faint);
  font-size: 11px;
  cursor: pointer;
}

.icon:hover {
  color: var(--color-text);
  background: var(--color-surface-muted);
}

.param-group {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.hr {
  height: 1px;
  background: var(--color-border);
}

.feather {
  display: grid;
  grid-template-columns: 76px 1fr auto;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--text-xs);
  color: var(--color-text-muted);
}

.feather input {
  width: 100%;
}

.row-meta {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--text-xs);
  color: var(--color-text-muted);
}

.tip {
  margin: 6px 0 0;
  font-size: 11px;
  line-height: 1.6;
}

.empty {
  margin: 8px;
  font-size: var(--text-xs);
}

.timeline {
  padding: var(--space-3) var(--space-4);
}

.tl-head {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--text-xs);
  color: var(--color-text-muted);
}

.tl-rows {
  margin-top: var(--space-2);
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.tl-row {
  display: grid;
  grid-template-columns: 70px 1fr;
  align-items: center;
  gap: var(--space-2);
}

.tl-label {
  overflow: hidden;
  font-size: 11px;
  color: var(--color-text-muted);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tl-track {
  height: 14px;
  overflow: hidden;
  border-radius: 4px;
  background: #eef2f7;
}

.tl-bar {
  height: 100%;
  opacity: 0.55;
}

.mono {
  font-family: var(--font-mono);
}

.small {
  font-size: 11px;
}

.muted {
  color: var(--color-text-muted);
}

@media (max-width: 1200px) {
  .workbench {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
