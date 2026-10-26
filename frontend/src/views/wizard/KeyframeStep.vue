<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import { fetchCase } from '@/api/case'
import {
  fetchKeyframePrompt,
  fetchKeyframeRounds,
  handoffKeyframe,
  pickKeyframe,
  runKeyframe,
  saveKeyframePrompt,
  setKeyframeSheet,
} from '@/api/keyframe'
import type { KeyframeRound } from '@/api/keyframe'
import { useCaseTasks } from '@/composables/useCaseTasks'
import VariableCard from '@/components/prompt/VariableCard.vue'
import FileOpenLink from '@/components/common/FileOpenLink.vue'
import AssetUploadDialog from '@/components/upload/AssetUploadDialog.vue'
import { KEYFRAME_MAX_CANDIDATES, KEYFRAME_RESOLUTIONS } from '@/constants/keyframe'
import { taskStatusMeta } from '@/constants/taskStatus'
import { buildPromptVariables, promptVariablesToRecord } from '@/utils/prompt'
import type { CaseInfo, PromptPayload } from '@/types/case-info'
import type { PromptVariable } from '@/types/case'

const props = defineProps<{ caseName: string; info: CaseInfo }>()
const emit = defineEmits<{
  updated: [data: CaseInfo]
  handoff: [data: CaseInfo]
}>()

type Step = 'first' | 'last'
const step = ref<Step>('first')
const prompt = ref<PromptPayload | null>(null)
const promptVars = ref<PromptVariable[]>([])
const promptSaving = ref(false)
const promptError = ref('')
const rounds = ref<KeyframeRound[]>([])
const roundsLoading = ref(false)
const running = ref(false)
const picking = ref<string | null>(null)
const handoffBusy = ref(false)
const sheetSaving = ref(false)
const sheetOpen = ref(false)
const uploadOpen = ref(false)
const taskOpen = ref(false)
const modelId = ref<number | null>(null)
const resolution = ref('2K')
const num = ref(4)
let startedAt = 0
let promptTimer: ReturnType<typeof setTimeout> | null = null
let roundsTimer: ReturnType<typeof setInterval> | null = null

const { latest, start: startTasks } = useCaseTasks(
  computed(() => props.caseName),
  ['keyframe'],
)

const keyframe = computed(() => props.info.keyframe)
const ready = computed(() => props.info.gates.keyframe)
const rawFirst = computed(() =>
  props.info.raw_frames.find((item) => item.role === 'first_frame_raw'),
)
const rawLast = computed(() => props.info.raw_frames.find((item) => item.role === 'last_frame_raw'))
const firstPick = computed(() => keyframe.value.first)
const lastPick = computed(() => keyframe.value.last)
const stepPick = computed(() => (step.value === 'first' ? firstPick.value : lastPick.value))
const stepRounds = computed(() =>
  rounds.value.filter((round) => round.step === step.value).slice(0, 8),
)
const canRun = computed(() => {
  if (!ready.value?.ready) return false
  if (step.value === 'last' && !firstPick.value) return false
  return true
})
const runReason = computed(() => {
  if (!ready.value?.ready) return ready.value?.reason || '先完成抽取并准备两张原始帧'
  if (step.value === 'last' && !firstPick.value) return '先生成并挑选首帧定稿'
  return ''
})
const handoffReason = computed(() => {
  if (keyframe.value.stale) return '抽取版本已更新，需要重新定稿'
  if (!firstPick.value) return '首帧未定稿'
  if (!lastPick.value) return '尾帧未定稿'
  return ''
})

const rawFrame = computed(() => (step.value === 'first' ? rawFirst.value : rawLast.value))
const rawLabel = computed(() => (step.value === 'first' ? '原始首帧' : '原始尾帧'))
const targetLabel = computed(() => (step.value === 'first' ? '首帧定稿' : '尾帧定稿'))
const sheetCandidate = computed(
  () => keyframe.value.sheet_candidates.find((item) => item.id === keyframe.value.sheet) ?? null,
)
// sheet_candidates 只带 label/path/rel/open_url，FileOpenLink 需要 name 与 exists。
const sheetFile = computed(() => {
  const item = sheetCandidate.value
  if (!item) return null
  return {
    name: item.label,
    rel: item.rel ?? null,
    path: item.path,
    exists: true,
    mtime: null,
    size: null,
    open_url: item.open_url ?? null,
  }
})

function assetUrl(assetId: string | null | undefined): string {
  if (!assetId) return ''
  return `/img/asset/${encodeURIComponent(props.caseName)}/${encodeURIComponent(assetId)}`
}

function pickedUrl(pick: { asset: string } | null): string {
  return assetUrl(pick?.asset)
}

function isPicked(round: KeyframeRound, index: number): boolean {
  const pick = stepPick.value
  return Boolean(pick && pick.round === round.round && pick.index === index)
}

function fileName(path: string): string {
  return path.split(/[\\/]/).pop() || path
}

function applyPrompt(data: PromptPayload): void {
  prompt.value = data
  promptError.value = data.error || ''
  promptVars.value = buildPromptVariables(data)
}

async function loadPrompt(current: Step = step.value): Promise<void> {
  try {
    applyPrompt(await fetchKeyframePrompt(props.caseName, current))
  } catch (err) {
    promptError.value = err instanceof Error ? err.message : '读取首尾帧提示词失败'
  }
}

function schedulePromptSave(key: string, value: string): void {
  const variable = promptVars.value.find((item) => item.key === key)
  if (variable) variable.value = value
  if (promptTimer) clearTimeout(promptTimer)
  promptTimer = setTimeout(() => void flushPrompt(), 400)
}

async function flushPrompt(): Promise<boolean> {
  if (promptTimer) {
    clearTimeout(promptTimer)
    promptTimer = null
  }
  const vars = promptVariablesToRecord(promptVars.value)
  promptSaving.value = true
  try {
    applyPrompt(await saveKeyframePrompt(props.caseName, step.value, vars))
    return true
  } catch (err) {
    promptError.value = err instanceof Error ? err.message : '保存首尾帧提示词失败'
    ElMessage.error(promptError.value)
    return false
  } finally {
    promptSaving.value = false
  }
}

async function loadRounds(): Promise<void> {
  roundsLoading.value = true
  try {
    const data = await fetchKeyframeRounds(props.caseName)
    rounds.value = data.rounds
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '读取首尾帧轮次失败')
  } finally {
    roundsLoading.value = false
  }
}

function startRoundsPolling(): void {
  if (roundsTimer) clearInterval(roundsTimer)
  roundsTimer = setInterval(() => {
    if (running.value) void loadRounds()
  }, 3000)
}

async function run(): Promise<void> {
  if (running.value) return
  if (!canRun.value) {
    ElMessage.warning(runReason.value)
    return
  }
  if (!(await flushPrompt())) return
  running.value = true
  startedAt = Date.now()
  try {
    const payload: { step: Step; model_id?: number; resolution?: string; num?: number } = {
      step: step.value,
      resolution: resolution.value,
      num: num.value,
    }
    if (modelId.value) payload.model_id = modelId.value
    await runKeyframe(props.caseName, payload)
    startTasks()
    startRoundsPolling()
  } catch (err) {
    running.value = false
    ElMessage.error(err instanceof Error ? err.message : '启动首尾帧生成失败')
  }
}

async function pick(round: KeyframeRound, index: number): Promise<void> {
  const key = `${round.round}:${index}`
  if (picking.value) return
  picking.value = key
  try {
    const data = await pickKeyframe(
      props.caseName,
      step.value === 'first' ? 'first_frame' : 'last_frame',
      round.round,
      index,
    )
    emit('updated', data)
    await loadRounds()
    if (step.value === 'first') {
      if (data.keyframe_queued) {
        ElMessage.success(data.keyframe_hint || '首帧已定稿；尾帧已自动开始生成')
      } else {
        ElMessage.info(data.keyframe_hint || '首帧已定稿；补齐尾帧提示词后生成尾帧')
      }
      step.value = 'last'
      await loadPrompt('last')
    } else {
      ElMessage.success('尾帧已定稿')
    }
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '挑选候选失败')
  } finally {
    picking.value = null
  }
}

async function changeSheet(asset: string): Promise<void> {
  if (sheetSaving.value) return
  sheetSaving.value = true
  try {
    const data = await setKeyframeSheet(props.caseName, asset)
    emit('updated', data)
    ElMessage.success('已绑定人物三视图')
    sheetOpen.value = false
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '绑定三视图失败')
  } finally {
    sheetSaving.value = false
  }
}

function onAssetsAdded(data: CaseInfo): void {
  emit('updated', data)
  uploadOpen.value = false
  ElMessage.success('已加入素材库；在抽屉里点选要用的三视图')
}

async function handoff(): Promise<void> {
  if (handoffBusy.value) return
  handoffBusy.value = true
  try {
    const data = await handoffKeyframe(props.caseName)
    emit('updated', data)
    emit('handoff', data)
    ElMessage.success('已交接给视频生成')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '交接失败')
  } finally {
    handoffBusy.value = false
  }
}

watch(step, (value) => {
  void loadPrompt(value)
})

watch(latest, async (task) => {
  if (!running.value || !task || task.kind !== 'keyframe') return
  const created = new Date(task.created_at).getTime()
  if (Number.isNaN(created) || created < startedAt - 5000) return
  if (task.status === 'done') {
    running.value = false
    await loadRounds()
    emit('updated', await fetchCase(props.caseName))
    ElMessage.success('首尾帧批次完成')
  } else if (task.status === 'failed') {
    running.value = false
    taskOpen.value = true
    ElMessage.error(task.error || '首尾帧生成失败')
  }
})

onMounted(() => {
  void loadPrompt()
  void loadRounds()
})

onUnmounted(() => {
  if (roundsTimer) clearInterval(roundsTimer)
  if (promptTimer) clearTimeout(promptTimer)
})
</script>

<template>
  <div class="kf-page">
    <div class="kf-topbar">
      <el-radio-group v-model="step" size="small">
        <el-radio-button value="first">首帧</el-radio-button>
        <el-radio-button value="last">尾帧</el-radio-button>
      </el-radio-group>
      <span class="spacer" />
      <el-button
        type="primary"
        :disabled="!keyframe.complete || handoffBusy"
        :loading="handoffBusy"
        @click="handoff"
      >
        交接给视频步
      </el-button>
      <span v-if="handoffReason" class="kf-reason">{{ handoffReason }}</span>
    </div>

    <section class="panel">
      <div class="panel-head">
        <h2 class="panel-title">图片步输入</h2>
        <span class="muted">
          {{
            step === 'first'
              ? '图1 / 图2 / 图3 与提示词里的参考图一一对应；首帧步骤只用图1与图2'
              : '图1 / 图2 / 图3 与提示词里的参考图一一对应；尾帧步骤再加图3（首帧定稿）'
          }}
        </span>
      </div>
      <div class="panel-body">
        <div class="kf-slots">
          <figure class="kf-slot">
            <figcaption class="kf-slot-cap">
              <span class="kf-slot-no">图1</span>
              <span>{{ rawLabel }}</span>
              <span class="kf-slot-state" :class="rawFrame?.exists ? 'ok' : 'bad'">
                {{ rawFrame?.exists ? '已就绪' : '缺图' }}
              </span>
            </figcaption>
            <div class="kf-shot">
              <img v-if="rawFrame?.exists" :src="rawFrame.url" :alt="rawLabel" />
              <div v-else class="kf-shot-empty"><span>缺{{ rawLabel }}</span></div>
              <FileOpenLink :file="rawFrame" :label="`打开${rawLabel}`" class="card-open" />
            </div>
            <div class="kf-slot-file mono">{{ rawFrame?.name || '—' }}</div>
          </figure>

          <figure class="kf-slot">
            <figcaption class="kf-slot-cap">
              <span class="kf-slot-no">图2</span>
              <span>人物三视图</span>
              <el-button
                link
                type="primary"
                size="small"
                class="kf-slot-action"
                @click="sheetOpen = true"
              >
                查看 / 更换
              </el-button>
            </figcaption>
            <div class="kf-shot contain">
              <img v-if="keyframe.sheet" :src="assetUrl(keyframe.sheet)" alt="人物三视图" />
              <div v-else class="kf-shot-empty"><span>还没有绑定三视图</span></div>
              <FileOpenLink :file="sheetFile" label="打开三视图" class="card-open" />
            </div>
            <div class="kf-slot-file mono">{{
              sheetCandidate ? fileName(sheetCandidate.path) : '—'
            }}</div>
          </figure>

          <figure class="kf-slot">
            <figcaption class="kf-slot-cap">
              <span class="kf-slot-no">图3</span>
              <span>首帧定稿</span>
              <span class="kf-slot-note">{{
                step === 'last' ? '来自首帧定稿' : '尾帧步骤才用'
              }}</span>
            </figcaption>
            <div v-if="step === 'last' && firstPick" class="kf-shot">
              <img :src="pickedUrl(firstPick)" alt="首帧定稿" />
              <FileOpenLink :file="firstPick.file" label="打开首帧定稿" class="card-open" />
            </div>
            <div v-else class="kf-shot kf-shot-empty">
              <span>生成并定稿首帧后填入</span>
            </div>
            <div class="kf-slot-file mono">
              {{ step === 'last' && firstPick?.file ? firstPick.file.name : '—' }}
            </div>
          </figure>

          <figure class="kf-slot">
            <figcaption class="kf-slot-cap">
              <span class="kf-slot-no">目标</span>
              <span>{{ targetLabel }}</span>
              <span class="kf-slot-note">点候选即定稿</span>
            </figcaption>
            <div v-if="stepPick" class="kf-shot">
              <img :src="pickedUrl(stepPick)" :alt="targetLabel" />
              <FileOpenLink :file="stepPick.file" :label="`打开${targetLabel}`" class="card-open" />
            </div>
            <div v-else class="kf-shot kf-shot-target"><span>从下方候选里挑一张</span></div>
            <div class="kf-slot-file mono">{{ stepPick?.file?.name || '待定稿' }}</div>
          </figure>
        </div>
      </div>
    </section>

    <section class="panel">
      <div class="panel-head">
        <h2 class="panel-title">{{ step === 'first' ? '首帧候选' : '尾帧候选' }}</h2>
        <span class="spacer" />
        <div class="kf-head-actions">
          <el-select v-model="resolution" size="small" style="width: 104px">
            <el-option
              v-for="item in KEYFRAME_RESOLUTIONS"
              :key="item.value"
              :label="item.label"
              :value="item.value"
            />
          </el-select>
          <el-input-number v-model="num" :min="1" :max="KEYFRAME_MAX_CANDIDATES" size="small" />
          <el-button link type="primary" :icon="Refresh" :loading="roundsLoading" @click="loadRounds">
            刷新
          </el-button>
          <el-button
            type="primary"
            size="small"
            :loading="running"
            :disabled="!canRun"
            @click="run"
          >
            {{ step === 'first' ? '生成首帧候选' : '生成尾帧候选' }}
          </el-button>
        </div>
      </div>
      <div class="panel-body">
        <p v-if="runReason" class="kf-reason">{{ runReason }}</p>
        <div v-if="!stepRounds.length" class="empty">
          <el-empty
            :description="step === 'first' ? '还没有首帧批次' : '还没有尾帧批次'"
            :image-size="72"
          />
        </div>
        <div v-for="round in stepRounds" :key="round.round" class="round-block">
          <div class="round-head">
            <span class="mono">{{ round.round }}</span>
            <el-tag size="small" effect="light">{{ round.stage || '未知阶段' }}</el-tag>
          </div>
          <div class="candidate-grid">
            <div v-for="item in round.results" :key="item.index" class="candidate-wrap">
              <button
                type="button"
                class="candidate-card"
                :class="{ picked: isPicked(round, item.index) }"
                :disabled="Boolean(picking)"
                @click="pick(round, item.index)"
              >
                <img :src="item.url" :alt="`候选 ${item.index}`" />
                <span v-if="isPicked(round, item.index)" class="picked-mark">已定稿</span>
              </button>
              <FileOpenLink :file="item" label="打开候选原图" class="card-open" />
            </div>
          </div>
        </div>

        <template v-if="latest">
          <div class="kf-task">
            <div class="kf-task-head">
              <el-tag size="small" effect="light" :type="taskStatusMeta(latest.status).type">
                {{ taskStatusMeta(latest.status).text }}
              </el-tag>
              <span class="mono">{{ latest.id }}</span>
              <span v-if="latest.queue_position" class="muted"
                >排队第 {{ latest.queue_position }} 位</span
              >
              <span class="spacer" />
              <el-button
                v-if="latest.tail.length"
                link
                type="primary"
                @click="taskOpen = !taskOpen"
              >
                {{ taskOpen ? '收起日志' : '技术详情' }}
              </el-button>
            </div>
            <el-alert
              v-if="latest.error"
              type="error"
              :closable="false"
              :title="latest.error"
              class="kf-task-error"
            />
            <pre v-if="taskOpen && latest.tail.length" class="task-tail">{{
              latest.tail.join('\n')
            }}</pre>
          </div>
        </template>
        <p v-else class="muted kf-task-empty">还没有生成任务。</p>
      </div>
    </section>

    <section class="panel">
      <div class="panel-head">
        <h2 class="panel-title">图片步提示词</h2>
        <el-tag v-if="promptSaving" size="small" type="warning" effect="light">保存中</el-tag>
        <span class="spacer" />
        <span class="muted">{{ step === 'first' ? '只生成首帧' : '用首帧定稿作 图3' }}</span>
      </div>
      <div class="panel-body kf-vars">
        <el-alert v-if="promptError" type="warning" :closable="false" :title="promptError" />
        <VariableCard
          v-for="item in promptVars"
          :key="item.key"
          :variable="item"
          @update:value="(value) => schedulePromptSave(item.key, value)"
        />
      </div>
    </section>

    <el-drawer
      v-model="sheetOpen"
      title="人物三视图"
      direction="rtl"
      size="min(380px, 92vw)"
      :close-on-click-modal="true"
    >
      <p class="muted kf-drawer-note">
        图片步固定用 图1 原始帧 + 这张三视图（尾帧步骤再加 图3 首帧定稿）。
      </p>
      <div v-if="keyframe.sheet_candidates.length" class="kf-sheet-grid">
        <button
          v-for="item in keyframe.sheet_candidates"
          :key="item.id"
          type="button"
          class="kf-sheet-card"
          :class="{ picked: item.id === keyframe.sheet }"
          :disabled="sheetSaving"
          @click="changeSheet(item.id)"
        >
          <img :src="assetUrl(item.id)" :alt="item.label" />
          <span class="kf-sheet-cap">
            <el-tag v-if="item.id === keyframe.sheet" size="small" type="success" effect="light"
              >已绑定</el-tag
            >
            <span class="mono">{{ fileName(item.path) }}</span>
          </span>
        </button>
      </div>
      <el-empty v-else description="素材库里还没有 subject_ref 图片" :image-size="64" />
      <template #footer>
        <div class="kf-sheet-foot">
          <el-button size="small" @click="uploadOpen = true">上传三视图</el-button>
          <span class="muted kf-drawer-note">
            上传按 subject_ref 入库；已绑定的三视图要换一张后才能在素材页删除。
          </span>
        </div>
      </template>
    </el-drawer>

    <AssetUploadDialog v-model="uploadOpen" :case-name="caseName" @added="onAssetsAdded" />
  </div>
</template>

<style scoped>
.kf-page {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.kf-topbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
}

.kf-reason {
  color: var(--color-warning);
  font-size: var(--text-xs);
}

.kf-slots {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--space-3);
}

.kf-slot {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  min-width: 0;
  margin: 0;
}

.kf-slot-cap {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
  font-size: var(--text-xs);
}

.kf-slot-no {
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  height: 18px;
  padding: 0 6px;
  border-radius: var(--radius-sm);
  background: var(--color-primary-soft);
  color: var(--color-primary);
  font-size: 11px;
  font-weight: 600;
}

.kf-slot-state {
  margin-left: auto;
  font-size: 11px;
}

.kf-slot-state.ok {
  color: var(--color-success);
}

.kf-slot-state.bad {
  color: var(--color-danger);
}

.kf-slot-note {
  margin-left: auto;
  color: var(--color-text-faint);
  font-size: 11px;
  white-space: nowrap;
}

.kf-slot-action {
  margin-left: auto;
}

.kf-shot {
  position: relative;
  overflow: hidden;
  border-radius: var(--radius-md);
  background: var(--color-video-bg);
  aspect-ratio: 16 / 9;
}

.kf-shot img {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.kf-shot.contain {
  background: var(--color-surface-muted);
}

.kf-shot.contain img {
  object-fit: contain;
}

.kf-shot-empty,
.kf-shot-target {
  display: grid;
  place-items: center;
  padding: var(--space-2);
  text-align: center;
  font-size: 11px;
  line-height: 1.5;
}

.kf-shot-empty {
  background: var(--color-surface-muted);
  border: 1px dashed var(--color-border-strong);
  color: var(--color-text-faint);
}

.kf-shot-target {
  background: var(--color-primary-soft);
  border: 1px dashed var(--color-primary);
  color: var(--color-primary);
}

.kf-slot-file {
  overflow: hidden;
  color: var(--color-text-muted);
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.card-open {
  position: absolute;
  top: 6px;
  right: 6px;
  z-index: 1;
  padding: 2px 6px;
  border-radius: 999px;
  background: rgba(15, 23, 42, 0.7);
  color: #fff;
}

.kf-head-actions {
  display: inline-flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
}

.round-block + .round-block {
  margin-top: var(--space-3);
}

.round-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  margin-bottom: var(--space-2);
  font-size: var(--text-sm);
}

.candidate-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--space-2);
}

.candidate-wrap {
  position: relative;
}

.candidate-card {
  position: relative;
  width: 100%;
  padding: 0;
  overflow: hidden;
  border: 2px solid transparent;
  border-radius: var(--radius-md);
  background: var(--color-video-bg);
  cursor: pointer;
}

.candidate-card img {
  display: block;
  width: 100%;
  aspect-ratio: 16 / 9;
  object-fit: cover;
}

.candidate-card:hover {
  border-color: var(--color-primary);
}

.candidate-card.picked {
  border-color: var(--color-success);
}

.picked-mark {
  position: absolute;
  right: 6px;
  bottom: 6px;
  padding: 2px 8px;
  border-radius: 999px;
  background: var(--color-success);
  color: #fff;
  font-size: 11px;
}

.kf-task {
  margin-top: var(--space-4);
  padding-top: var(--space-3);
  border-top: 1px solid var(--color-border);
}

.kf-task-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--text-sm);
}

.kf-task-error {
  margin-top: var(--space-2);
}

.kf-task-empty {
  margin: var(--space-3) 0 0;
  font-size: var(--text-xs);
}

.kf-vars {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: var(--space-3);
}

.kf-vars > .el-alert {
  grid-column: 1 / -1;
}

.kf-drawer-note {
  margin: 0 0 var(--space-3);
  font-size: var(--text-xs);
}

.kf-sheet-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
  gap: var(--space-2);
}

.kf-sheet-card {
  padding: 0;
  overflow: hidden;
  border: 2px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
  text-align: left;
  cursor: pointer;
}

.kf-sheet-card.picked {
  border-color: var(--color-success);
}

.kf-sheet-card img {
  display: block;
  width: 100%;
  aspect-ratio: 4 / 3;
  object-fit: contain;
  background: var(--color-surface-muted);
}

.kf-sheet-cap {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-1);
  min-width: 0;
  padding: var(--space-1) var(--space-2);
  font-size: 11px;
}

.kf-sheet-cap .mono {
  flex: 1 0 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.kf-sheet-foot {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: var(--space-1);
}

.kf-sheet-foot .kf-drawer-note {
  margin: 0;
  text-align: left;
}

@media (max-width: 1280px) {
  .kf-slots {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .candidate-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .kf-vars {
    grid-template-columns: minmax(0, 1fr);
  }
}

@media (max-width: 900px) {
  .kf-slots {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
