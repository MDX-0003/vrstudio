<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Check } from '@element-plus/icons-vue'
import { fetchCase } from '@/api/case'
import { acquireLock, releaseLock } from '@/api/lock'
import type { GeometryPayload } from '@/api/workflow'
import CompareStep from '@/views/wizard/CompareStep.vue'
import ExtractStep from '@/views/wizard/ExtractStep.vue'
import GenerateStep from '@/views/wizard/GenerateStep.vue'
import KeyframeStep from '@/views/wizard/KeyframeStep.vue'
import MergeStep from '@/views/wizard/MergeStep.vue'
import PickStep from '@/views/wizard/PickStep.vue'
import { flowMeta } from '@/constants/flow'
import { stepsForFlow } from '@/constants/steps'
import { useCaseLoader } from '@/composables/useCaseLoader'
import type { SaveState } from '@/utils/saveState'
import type { Flow } from '@/types/case'
import type { CaseInfo } from '@/types/case-info'

const route = useRoute()
const router = useRouter()

const caseName = computed(() => String(route.params.name ?? ''))
const flow = ref<Flow>('patch')
const steps = computed(() => stepsForFlow(flow.value))
const activeStep = computed(() => {
  const raw = String(route.params.step ?? '')
  return raw || steps.value[0]?.key || 'generate'
})
const activeIndex = computed(() => {
  const index = steps.value.findIndex((step) => step.key === activeStep.value)
  return index < 0 ? 0 : index
})

const caseInfo = ref<CaseInfo | null>(null)
const saveState = ref<SaveState>('saved')
const lockHolder = ref<string | null>(null)
let lockTimer: ReturnType<typeof setInterval> | null = null
let caseLoadSeq = 0

function applyCaseInfo(data: CaseInfo): void {
  caseInfo.value = data
  flow.value = data.flow
}

const {
  loading: caseLoading,
  error: caseError,
  load: loadCaseDocument,
} = useCaseLoader<CaseInfo>({
  fetchCase,
  apply: applyCaseInfo,
  errorMessage: (err) => (err instanceof Error ? err.message : '读取 case 失败'),
})

async function loadCase(): Promise<void> {
  const seq = ++caseLoadSeq
  if (!caseName.value) {
    caseError.value = '缺少 case 名称'
    return
  }
  await loadCaseDocument(caseName.value)
  if (seq !== caseLoadSeq) return
}

function selectStep(key: string): void {
  router.push({ name: 'case', params: { name: caseName.value, step: key } })
}

function onStepUpdated(data: CaseInfo): void {
  applyCaseInfo(data)
}

function onGenerateSaveState(state: SaveState): void {
  saveState.value = state
}

function applyGeometry(payload: GeometryPayload): void {
  if (!caseInfo.value) return
  caseInfo.value = {
    ...caseInfo.value,
    viewport: payload.viewport,
    inner: payload.inner,
    viewport_footprint: payload.viewport_footprint,
    previews: payload.previews,
  }
}

function onHandoff(data: CaseInfo): void {
  applyCaseInfo(data)
  void router.push({ name: 'case', params: { name: caseName.value, step: 'generate' } })
}

watch(
  () => [activeStep.value, steps.value, caseInfo.value?.name],
  () => {
    if (!caseInfo.value) return
    if (!steps.value.some((step) => step.key === activeStep.value)) {
      void router.replace({
        name: 'case',
        params: { name: caseName.value, step: steps.value[0]?.key ?? 'generate' },
      })
    }
  },
  { immediate: true },
)

async function acquireCaseLock(): Promise<void> {
  if (!caseName.value) return
  try {
    const payload = await acquireLock(caseName.value)
    lockHolder.value = payload.mine ? null : (payload.lock?.owner_name ?? null)
  } catch (err) {
    const detail = (err as { payload?: { lock?: { owner_name?: string } } }).payload
    lockHolder.value = detail?.lock?.owner_name ?? null
    if (lockHolder.value) {
      ElMessage.warning(`case 正被 ${lockHolder.value} 使用；保存仍会写入，请先沟通`)
    }
  }
}

function startLockHeartbeat(): void {
  if (lockTimer) clearInterval(lockTimer)
  lockTimer = setInterval(() => void acquireCaseLock(), 30000)
}

onMounted(() => {
  void loadCase()
  void acquireCaseLock()
  startLockHeartbeat()
  window.addEventListener('case-conflict', onCaseConflict)
})

onUnmounted(() => {
  if (lockTimer) {
    clearInterval(lockTimer)
    lockTimer = null
  }
  if (caseName.value) releaseLock(caseName.value)
  window.removeEventListener('case-conflict', onCaseConflict)
})

function onCaseConflict(event: Event): void {
  const detail = (event as CustomEvent<{ error?: string }>).detail
  void ElMessageBox.confirm(
    detail?.error || 'case 已被其他人修改，当前页面的保存会覆盖对方的改动。',
    '保存冲突',
    {
      type: 'warning',
      confirmButtonText: '重新加载',
      cancelButtonText: '保留当前页面',
    },
  )
    .then(() => loadCase())
    .catch(() => undefined)
}

watch(caseName, (_next, previous) => {
  if (previous) releaseLock(previous)
  saveState.value = 'saved'
  void loadCase()
  void acquireCaseLock()
})
</script>

<template>
  <div class="page wizard-page" :class="{ 'gen-step': activeStep === 'generate' }">
    <div class="wizard-head">
      <div>
        <div class="crumbs muted">Case / {{ caseName }}</div>
        <h1>
          {{ caseName }}
          <el-tag :type="flowMeta[flow].tag" effect="light">{{ flowMeta[flow].label }}</el-tag>
        </h1>
      </div>
      <div class="save-state">
        <span
          class="status-dot"
          :class="saveState === 'saved' ? 'done' : saveState === 'saving' ? 'queued' : 'stale'"
        />
        {{ saveState === 'saved' ? '已保存' : saveState === 'saving' ? '保存中' : '未保存' }}
      </div>
    </div>

    <el-alert
      v-if="lockHolder"
      type="warning"
      :closable="false"
      :title="`case 正被 ${lockHolder} 使用；保存仍会写入，请先沟通`"
    />

    <nav class="step-nav" aria-label="流程步骤">
      <button
        v-for="(step, index) in steps"
        :key="step.key"
        type="button"
        class="step"
        :class="{ active: step.key === activeStep, done: index < activeIndex }"
        @click="selectStep(step.key)"
      >
        <span class="step-index">
          <el-icon v-if="index < activeIndex" :size="12"><Check /></el-icon>
          <template v-else>{{ index + 1 }}</template>
        </span>
        {{ step.label }}
      </button>
    </nav>

    <div v-if="caseError" class="load-error">
      <span>{{ caseError }}</span>
      <el-button link type="primary" @click="loadCase">重试</el-button>
    </div>

    <template v-else-if="caseInfo && caseInfo.name === caseName">
      <PickStep
        v-if="activeStep === 'pick'"
        :key="caseName"
        :case-name="caseName"
        :info="caseInfo"
        @updated="onStepUpdated"
        @geometry="applyGeometry"
      />
      <ExtractStep
        v-else-if="activeStep === 'extract'"
        :key="caseName"
        :case-name="caseName"
        :info="caseInfo"
        @updated="onStepUpdated"
      />
      <KeyframeStep
        v-else-if="activeStep === 'keyframe'"
        :key="caseName"
        :case-name="caseName"
        :info="caseInfo"
        @updated="onStepUpdated"
        @handoff="onHandoff"
      />
      <MergeStep
        v-else-if="['merge', 'pad', 'recompose'].includes(activeStep)"
        :key="caseName"
        :case-name="caseName"
        :info="caseInfo"
        @updated="onStepUpdated"
      />
      <CompareStep
        v-else-if="activeStep === 'compare'"
        :key="caseName"
        :case-name="caseName"
        :info="caseInfo"
      />
      <GenerateStep
        v-else
        :key="caseName"
        :case-name="caseName"
        :info="caseInfo"
        @updated="onStepUpdated"
        @save-state="onGenerateSaveState"
      />
    </template>
    <div v-else-if="caseLoading" class="panel loading-panel">
      <el-skeleton :rows="5" animated />
    </div>
  </div>
</template>

<style scoped>
.wizard-page {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.wizard-page.gen-step {
  padding-bottom: 160px;
}

.wizard-head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: var(--space-4);
}

.crumbs {
  font-size: var(--text-sm);
}

.wizard-head h1 {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  margin: var(--space-1) 0 0;
  font-size: var(--text-2xl);
  font-weight: 650;
  letter-spacing: -0.01em;
}

.save-state {
  display: inline-flex;
  align-items: center;
  color: var(--color-text-muted);
  font-size: var(--text-sm);
}

.step-nav {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-1);
  padding: var(--space-1);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
}

.step {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  height: 36px;
  padding: 0 var(--space-3);
  border: 0;
  border-radius: var(--radius-md);
  background: transparent;
  color: var(--color-text-muted);
  font-size: var(--text-sm);
  cursor: pointer;
}

.step:hover {
  background: var(--color-surface-muted);
  color: var(--color-text);
}

.step.done {
  color: var(--color-success);
}

.step.active {
  background: var(--color-primary-soft);
  color: var(--color-primary);
  font-weight: 600;
}

.step-index {
  display: grid;
  place-items: center;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: var(--color-surface-muted);
  font-size: 11px;
  font-weight: 600;
}

.step.active .step-index {
  background: var(--color-primary);
  color: #fff;
}

.step.done .step-index {
  background: var(--color-success-soft);
  color: var(--color-success);
}

.load-error {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-4);
  margin-bottom: var(--space-4);
  border: 1px solid var(--color-danger);
  border-radius: var(--radius-md);
  background: var(--color-danger-soft);
  color: var(--color-danger);
}

.loading-panel {
  padding: var(--space-5);
}
</style>
