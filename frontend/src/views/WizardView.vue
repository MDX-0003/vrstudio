<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Check } from '@element-plus/icons-vue'
import { fetchCase, assignOwnerToMe } from '@/api/case'
import { ApiRequestError, retryOverwrite } from '@/api/client'
import { acquireLock, releaseLock } from '@/api/lock'
import type { GeometryPayload } from '@/api/workflow'
import CompareStep from '@/views/wizard/CompareStep.vue'
import ExtractStep from '@/views/wizard/ExtractStep.vue'
import GenerateStep from '@/views/wizard/GenerateStep.vue'
import KeyframeStep from '@/views/wizard/KeyframeStep.vue'
import MergeStep from '@/views/wizard/MergeStep.vue'
import PickStep from '@/views/wizard/PickStep.vue'
import RelightAssetsStep from '@/views/wizard/RelightAssetsStep.vue'
import RelightStep from '@/views/wizard/RelightStep.vue'
import CaseLibraryView from '@/views/case/CaseLibraryView.vue'
import { flowMeta } from '@/constants/flow'
import { stepsForFlow } from '@/constants/steps'
import { useCaseLoader } from '@/composables/useCaseLoader'
import { useSessionStore } from '@/stores/session'
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
const isLibrary = computed(() => activeStep.value === 'library')
const libraryCount = computed(
  () => (caseInfo.value?.assets ?? []).filter((asset) => asset.source !== 'flow').length,
)
const lastFlowStep = ref(steps.value[0]?.key || 'generate')
const activeIndex = computed(() => {
  const index = steps.value.findIndex((step) => step.key === activeStep.value)
  return index < 0 ? 0 : index
})

const caseInfo = ref<CaseInfo | null>(null)
const saveState = ref<SaveState>('saved')
const lockHolder = ref<string | null>(null)
const session = useSessionStore()
const takingOwner = ref(false)
const conflictActive = ref(false)
let lockTimer: ReturnType<typeof setInterval> | null = null
let caseLoadSeq = 0
let conflictDialogOpen = false

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
  lastFlowStep.value = key
  router.push({ name: 'case', params: { name: caseName.value, step: key } })
}

function goLibrary(): void {
  router.push({ name: 'case', params: { name: caseName.value, step: 'library' } })
}

function goFlow(): void {
  selectStep(lastFlowStep.value || steps.value[0]?.key || 'generate')
}

function onCaseView(value: string | number | boolean | undefined): void {
  if (String(value) === 'library') goLibrary()
  else goFlow()
}

const ownerMeta = computed(() => caseInfo.value?.meta ?? null)
const ownerLabel = computed(() => ownerMeta.value?.owner_name ?? '未定（首次打开时自动归属）')
const ownerIsMine = computed(() => {
  const uid = session.user?.id
  return Boolean(uid) && ownerMeta.value?.owner_id === uid
})

async function takeOwnership(): Promise<void> {
  if (!caseName.value || takingOwner.value) return
  takingOwner.value = true
  try {
    const { meta } = await assignOwnerToMe(caseName.value)
    if (caseInfo.value) caseInfo.value = { ...caseInfo.value, meta }
    ElMessage.success(`已归属到 ${session.displayName} 名下`)
  } catch (err) {
    // 409 由全局 case-conflict 弹窗处理（提示后可重新加载再试）。
    if (!(err instanceof ApiRequestError && err.status === 409)) {
      ElMessage.error(err instanceof Error ? err.message : '归属失败')
    }
  } finally {
    takingOwner.value = false
  }
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
    if (activeStep.value === 'library') return
    if (!caseInfo.value) return
    if (!steps.value.some((step) => step.key === activeStep.value)) {
      void router.replace({
        name: 'case',
        params: { name: caseName.value, step: steps.value[0]?.key ?? 'generate' },
      })
      return
    }
    lastFlowStep.value = activeStep.value
  },
  { immediate: true },
)

async function acquireCaseLock(): Promise<void> {
  if (!caseName.value) return
  try {
    const payload = await acquireLock(caseName.value)
    lockHolder.value = payload.mine ? null : (payload.lock?.owner_name ?? null)
  } catch (err) {
    const detail =
      err instanceof ApiRequestError
        ? (err.payload as { lock?: { owner_name?: string } } | undefined)
        : undefined
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
  void session.load()
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
  if (conflictDialogOpen) return
  conflictDialogOpen = true
  conflictActive.value = true
  const detail = (event as CustomEvent<{ error?: string }>).detail
  const ownerSuffix =
    caseInfo.value?.meta?.owner_id && caseInfo.value.meta.owner_id === session.user?.id
      ? '你是这个 case 的负责人。'
      : ''
  void ElMessageBox.confirm(
    (detail?.error || 'case 已被其他人修改，当前页面的保存会覆盖对方的改动。') +
      ' ' +
      ownerSuffix +
      '「仍要保存」会覆盖对方的改动；「重新加载」会丢弃你未保存的修改；' +
      '点右上角 × 保留当前页面，但本次会话的保存会被拒绝。',
    '保存冲突',
    {
      type: 'warning',
      distinguishCancelAndClose: true,
      confirmButtonText: '仍要保存（覆盖对方）',
      cancelButtonText: '重新加载（丢弃本地修改）',
    },
  )
    .then(async () => {
      if (await retryOverwrite()) {
        conflictActive.value = false
        ElMessage.success('已覆盖保存；对方的改动被替换')
      } else {
        ElMessage.error('覆盖保存失败，请刷新页面后重试')
      }
    })
    .catch((action) => {
      // cancel = 重新加载；close（×）= 保留当前页面（保存仍被拒绝）。
      if (action === 'cancel') {
        conflictActive.value = false
        void loadCase()
      }
    })
    .finally(() => {
      conflictDialogOpen = false
    })
}

watch(caseName, (_next, previous) => {
  if (previous) releaseLock(previous)
  saveState.value = 'saved'
  void loadCase()
  void acquireCaseLock()
})
</script>

<template>
  <div class="page wizard-page" :class="{ 'gen-step': activeStep === 'generate' && !isLibrary }">
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

    <div v-if="caseInfo" class="owner-line">
      <span class="muted">当前归属 = {{ ownerLabel }}</span>
      <template v-if="!ownerIsMine">
        <el-button size="small" type="primary" :loading="takingOwner" @click="takeOwnership">
          归属到我的名下
        </el-button>
        <span class="muted owner-hint">
          点击后负责人变为「{{ session.displayName }}」；若他人刚修改过归属会提示冲突，刷新后再试。
        </span>
      </template>
    </div>

    <el-alert
      v-if="lockHolder"
      type="warning"
      :closable="false"
      :title="`「${lockHolder}」可能正在编辑此 case；同时修改会互相覆盖，请先向对方确认`"
    />

    <div v-if="conflictActive" class="conflict-line muted">
      存在未解决的保存冲突：此页面的保存会被拒绝；重新加载或覆盖保存后解除。
    </div>

    <div class="case-switch">
      <el-radio-group
        :model-value="isLibrary ? 'library' : 'flow'"
        size="small"
        @change="onCaseView"
      >
        <el-radio-button value="flow">流程</el-radio-button>
        <el-radio-button value="library">
          素材库
          <span class="case-switch-count">{{ libraryCount }}</span>
        </el-radio-button>
      </el-radio-group>
      <span class="muted case-switch-note">
        素材库是 case 级页面；账号锁与保存状态仍作用在当前 case 上。
      </span>
    </div>

    <nav v-if="!isLibrary" class="step-nav" aria-label="流程步骤">
      <button
        v-for="(step, index) in steps"
        :key="step.key"
        type="button"
        class="step"
        :class="{ active: step.key === activeStep, done: index < activeIndex }"
        :aria-label="`${index + 1} ${step.label}`"
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
      <CaseLibraryView
        v-if="isLibrary"
        :case-name="caseName"
        :info="caseInfo"
        @updated="onStepUpdated"
      />
      <PickStep
        v-else-if="activeStep === 'pick'"
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
      <RelightAssetsStep
        v-else-if="activeStep === 'assets'"
        :key="caseName"
        :case-name="caseName"
        :info="caseInfo"
        @updated="onStepUpdated"
      />
      <RelightStep
        v-else-if="activeStep === 'relight'"
        :key="caseName"
        :case-name="caseName"
        :info="caseInfo"
        @updated="onStepUpdated"
      />
      <MergeStep
        v-else-if="['merge', 'pad', 'recompose'].includes(activeStep)"
        :key="caseName"
        :case-name="caseName"
        :info="caseInfo"
        :step="activeStep"
        @updated="onStepUpdated"
      />
      <CompareStep
        v-else-if="activeStep === 'compare'"
        :key="caseName"
        :case-name="caseName"
        :info="caseInfo"
        @updated="onStepUpdated"
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

.owner-line {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-3);
  font-size: var(--text-sm);
}

.owner-hint {
  font-size: var(--text-xs);
}

.conflict-line {
  font-size: var(--text-sm);
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

.case-switch {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
}

.case-switch-count {
  display: inline-flex;
  align-items: center;
  height: 18px;
  padding: 0 6px;
  margin-left: var(--space-1);
  border-radius: 999px;
  background: var(--color-surface-muted);
  color: var(--color-text-muted);
  font-size: 11px;
}

.case-switch-note {
  font-size: var(--text-xs);
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

.loading-panel {
  padding: var(--space-5);
}
</style>
