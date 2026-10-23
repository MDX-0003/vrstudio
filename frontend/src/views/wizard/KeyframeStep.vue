<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { ArrowRight, Check, Refresh, VideoPlay } from '@element-plus/icons-vue'
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
const modelId = ref<number | null>(null)
const resolution = ref('1080p')
const num = ref(4)
let startedAt = 0
let promptTimer: ReturnType<typeof setTimeout> | null = null
let roundsTimer: ReturnType<typeof setInterval> | null = null

const { latest, start: startTasks } = useCaseTasks(props.caseName, ['keyframe'])

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

const KEYFRAME_LABELS: Record<string, string> = {
  figure: '人物外观',
  pose_first: '首帧姿态',
  pose_last: '尾帧姿态',
  preserve: '必须保持',
}

function applyPrompt(data: PromptPayload): void {
  prompt.value = data
  promptError.value = data.error || ''
  promptVars.value = Object.entries(data.vars ?? {}).map(([key, value]) => ({
    key,
    label: data.labels?.[key] || KEYFRAME_LABELS[key] || key,
    help: data.help?.[key] || '',
    value,
  }))
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
  const vars: Record<string, string> = {}
  const allowed = new Set(['figure', 'pose_first', 'pose_last', 'preserve'])
  for (const item of promptVars.value) {
    if (allowed.has(item.key)) vars[item.key] = item.value
  }
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
      ElMessage.success('首帧已定稿；提示词齐全会自动排队尾帧')
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

async function changeSheet(asset: unknown): Promise<void> {
  if (sheetSaving.value) return
  sheetSaving.value = true
  try {
    const data = await setKeyframeSheet(props.caseName, String(asset))
    emit('updated', data)
    ElMessage.success('已绑定人物三视图')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '绑定三视图失败')
  } finally {
    sheetSaving.value = false
  }
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
  <div class="keyframe-layout">
    <section class="panel frame-panel">
      <div class="panel-head">
        <h2 class="panel-title">首尾帧</h2>
        <span class="muted">{{
          keyframe.stale ? '抽取版本已更新，需要重新定稿' : '两张原始帧来自当前抽取版本'
        }}</span>
      </div>
      <div class="panel-body">
        <div class="frame-chain">
          <div class="frame-card">
            <div class="frame-label">首帧原始图</div>
            <div class="frame-media">
              <img v-if="rawFirst?.exists" :src="rawFirst.url" alt="首帧原始图" />
              <el-empty v-else description="缺首帧原始图" :image-size="60" />
              <span v-if="firstPick" class="pick-badge"
                ><el-icon><Check /></el-icon>已定稿</span
              >
            </div>
            <div v-if="firstPick" class="picked-preview">
              <img :src="pickedUrl(firstPick)" alt="首帧定稿" />
            </div>
          </div>
          <el-icon class="chain-arrow" :size="22"><ArrowRight /></el-icon>
          <div class="frame-card">
            <div class="frame-label">尾帧原始图</div>
            <div class="frame-media">
              <img v-if="rawLast?.exists" :src="rawLast.url" alt="尾帧原始图" />
              <el-empty v-else description="缺尾帧原始图" :image-size="60" />
              <span v-if="lastPick" class="pick-badge"
                ><el-icon><Check /></el-icon>已定稿</span
              >
            </div>
            <div v-if="lastPick" class="picked-preview">
              <img :src="pickedUrl(lastPick)" alt="尾帧定稿" />
            </div>
          </div>
          <el-icon class="chain-arrow" :size="22"><ArrowRight /></el-icon>
          <div class="video-node">
            <el-icon :size="28"><VideoPlay /></el-icon>
            <span>视频</span>
          </div>
        </div>
      </div>
    </section>

    <section class="panel">
      <div class="panel-head">
        <h2 class="panel-title">人物三视图</h2>
        <span class="muted">{{ keyframe.sheet_candidates.length }} 张候选</span>
      </div>
      <div class="panel-body">
        <el-select
          :model-value="keyframe.sheet ?? ''"
          size="small"
          style="width: 100%"
          :loading="sheetSaving"
          placeholder="选择一张 subject_ref 图片"
          @change="changeSheet"
        >
          <el-option
            v-for="item in keyframe.sheet_candidates"
            :key="item.id"
            :label="item.label"
            :value="item.id"
          />
        </el-select>
        <p class="muted note">图片步固定用两张原始帧加这张三视图；视频步的提交集不受影响。</p>
      </div>
    </section>

    <section class="panel">
      <div class="panel-head">
        <h2 class="panel-title">图片步提示词</h2>
        <el-tag v-if="promptSaving" size="small" type="warning" effect="light">保存中</el-tag>
      </div>
      <div class="panel-body prompt-panel">
        <el-alert v-if="promptError" type="warning" :closable="false" :title="promptError" />
        <div class="step-tabs">
          <el-radio-group v-model="step" size="small">
            <el-radio-button value="first">首帧</el-radio-button>
            <el-radio-button value="last">尾帧</el-radio-button>
          </el-radio-group>
          <span class="muted">{{ step === 'first' ? '只生成首帧' : '用首帧定稿作 图3' }}</span>
        </div>
        <VariableCard
          v-for="item in promptVars"
          :key="item.key"
          :variable="item"
          @update:value="(value) => schedulePromptSave(item.key, value)"
        />
        <div class="run-row">
          <el-select v-model="resolution" size="small" style="width: 110px">
            <el-option label="480p" value="480p" />
            <el-option label="720p" value="720p" />
            <el-option label="1080p" value="1080p" />
          </el-select>
          <el-input-number v-model="num" :min="1" :max="4" size="small" />
          <el-button
            type="primary"
            size="small"
            :loading="running"
            :disabled="!canRun"
            @click="run"
          >
            {{ step === 'first' ? '生成首帧候选' : '生成尾帧候选' }}
          </el-button>
          <span v-if="runReason" class="reason">{{ runReason }}</span>
        </div>
      </div>
    </section>

    <section class="panel">
      <div class="panel-head">
        <h2 class="panel-title">{{ step === 'first' ? '首帧候选' : '尾帧候选' }}</h2>
        <el-button link type="primary" :icon="Refresh" :loading="roundsLoading" @click="loadRounds">
          刷新
        </el-button>
      </div>
      <div class="panel-body">
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
            <button
              v-for="item in round.results"
              :key="item.index"
              type="button"
              class="candidate-card"
              :class="{ picked: isPicked(round, item.index) }"
              :disabled="Boolean(picking)"
              @click="pick(round, item.index)"
            >
              <img :src="item.url" :alt="`候选 ${item.index}`" />
              <span v-if="isPicked(round, item.index)" class="picked-mark">已定稿</span>
            </button>
          </div>
        </div>
      </div>
    </section>

    <section class="panel">
      <div class="panel-head">
        <h2 class="panel-title">交接</h2>
      </div>
      <div class="panel-body">
        <div class="handoff-row">
          <el-button
            type="primary"
            :disabled="!keyframe.complete || handoffBusy"
            :loading="handoffBusy"
            @click="handoff"
          >
            用这两帧生成视频
          </el-button>
          <span class="muted">
            {{
              keyframe.complete
                ? '会写入 firstLastFrame 模式与一对提交集，并跳到生成步骤'
                : '首帧与尾帧都定稿后才能交接'
            }}
          </span>
        </div>
        <div v-if="latest" class="task-summary">
          <el-tag
            size="small"
            effect="light"
            :type="
              latest.status === 'failed'
                ? 'danger'
                : latest.status === 'done'
                  ? 'success'
                  : 'primary'
            "
          >
            {{ latest.status }}
          </el-tag>
          <span class="mono">{{ latest.id }}</span>
          <span v-if="latest.queue_position" class="muted"
            >排队第 {{ latest.queue_position }} 位</span
          >
        </div>
        <el-alert
          v-if="latest?.error"
          type="error"
          :closable="false"
          :title="latest.error"
          class="task-error"
        />
        <pre v-if="latest?.tail.length" class="task-tail">{{ latest.tail.join('\n') }}</pre>
      </div>
    </section>
  </div>
</template>

<style scoped>
.keyframe-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: var(--space-4);
  align-items: start;
}

.frame-panel {
  grid-column: 1 / -1;
}

.frame-chain {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  flex-wrap: wrap;
}

.frame-card {
  flex: 1;
  min-width: 220px;
  max-width: 360px;
}

.frame-label {
  margin-bottom: var(--space-1);
  color: var(--color-text-muted);
  font-size: var(--text-xs);
}

.frame-media {
  position: relative;
  overflow: hidden;
  border-radius: var(--radius-md);
  background: #0f172a;
  aspect-ratio: 16 / 9;
}

.frame-media img {
  width: 100%;
  height: 100%;
  object-fit: contain;
}

.pick-badge {
  position: absolute;
  top: var(--space-2);
  left: var(--space-2);
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px;
  border-radius: 999px;
  background: var(--color-success);
  color: #fff;
  font-size: var(--text-xs);
}

.picked-preview {
  margin-top: var(--space-2);
  overflow: hidden;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
}

.picked-preview img {
  display: block;
  width: 100%;
}

.chain-arrow {
  color: var(--color-text-faint);
}

.video-node {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-1);
  color: var(--color-text-muted);
  font-size: var(--text-xs);
}

.note {
  margin: var(--space-2) 0 0;
  font-size: var(--text-xs);
}

.prompt-panel {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.step-tabs {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
}

.run-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
}

.reason {
  color: var(--color-warning);
  font-size: var(--text-xs);
}

.round-block {
  margin-bottom: var(--space-3);
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
  grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
  gap: var(--space-2);
}

.candidate-card {
  position: relative;
  padding: 0;
  overflow: hidden;
  border: 2px solid transparent;
  border-radius: var(--radius-md);
  background: #0f172a;
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

.handoff-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
}

.task-summary {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
  margin-top: var(--space-3);
  font-size: var(--text-sm);
}

.task-error {
  margin-top: var(--space-2);
}

.task-tail {
  max-height: 200px;
  margin: var(--space-2) 0 0;
  padding: var(--space-2);
  overflow: auto;
  border-radius: var(--radius-sm);
  background: #0f172a;
  color: #e2e8f0;
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  line-height: 1.55;
  white-space: pre-wrap;
}

.empty {
  padding: var(--space-2);
}

@media (max-width: 1280px) {
  .keyframe-layout {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
