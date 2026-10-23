<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Bottom,
  Check,
  Delete,
  Picture,
  Plus,
  Refresh,
  Top,
  VideoCamera,
  VideoPlay,
} from '@element-plus/icons-vue'
import {
  changeAssetRole,
  fetchCase,
  removeAsset,
  reorderAssets,
  runOperation,
  saveParams,
  savePromptVar,
  saveSubmitSelection,
} from '@/api/case'
import { fetchSessions } from '@/api/sessions'
import { fetchTasks } from '@/api/tasks'
import { acquireLock, releaseLock } from '@/api/lock'
import type { GeometryPayload } from '@/api/workflow'
import AssetUploadDialog from '@/components/upload/AssetUploadDialog.vue'
import CompareStep from '@/views/wizard/CompareStep.vue'
import ExtractStep from '@/views/wizard/ExtractStep.vue'
import KeyframeStep from '@/views/wizard/KeyframeStep.vue'
import MergeStep from '@/views/wizard/MergeStep.vue'
import PickStep from '@/views/wizard/PickStep.vue'
import { ASSET_ROLE_LABELS, ASSET_ROLE_OPTIONS } from '@/constants/assetRoles'
import { flowMeta } from '@/constants/flow'
import type { AssetRow, Flow, PromptVariable } from '@/types/case'
import type {
  AssetDetail,
  CaseInfo,
  GenerationParams,
  InputOption,
  ModeInfo,
  OutputOption,
  RoundSummary,
} from '@/types/case-info'
import type { EpisodeOption, ProjectOption, SessionOption } from '@/types/session'
import type { TaskView } from '@/types/task'

const route = useRoute()
const router = useRouter()

const caseName = computed(() => String(route.params.name ?? ''))
const flow = ref<Flow>('patch')

interface StepDef {
  key: string
  label: string
}

const steps = computed<StepDef[]>(() => {
  if (flow.value === 'fullredraw') {
    return [
      { key: 'pad', label: '扩充画面' },
      { key: 'generate', label: '生成' },
      { key: 'recompose', label: '裁回原尺寸' },
      { key: 'compare', label: '对比' },
    ]
  }
  if (flow.value === 'gen') return [{ key: 'generate', label: '生成' }]
  return [
    { key: 'pick', label: '选区' },
    { key: 'extract', label: '抽取' },
    { key: 'keyframe', label: '首尾帧' },
    { key: 'generate', label: '生成' },
    { key: 'merge', label: '合并' },
    { key: 'compare', label: '对比' },
  ]
})

const activeStep = computed(() => {
  const raw = String(route.params.step ?? '')
  return raw || steps.value[0]?.key || 'generate'
})

const activeIndex = computed(() => {
  const index = steps.value.findIndex((step) => step.key === activeStep.value)
  return index < 0 ? 0 : index
})

const assets = ref<AssetRow[]>([])
const promptVars = ref<PromptVariable[]>([])
const promptRendered = ref('')
const promptRenderError = ref('')
const promptSaving = ref(false)
const pendingPromptSaves = new Map<string, string>()
let promptTimer: ReturnType<typeof setTimeout> | null = null
const caseInfo = ref<CaseInfo | null>(null)
const caseLoading = ref(false)
const caseError = ref('')
const lockHolder = ref<string | null>(null)
let lockTimer: ReturnType<typeof setInterval> | null = null
const selectionSaving = ref(false)
const paramsError = ref('')
const paramsSaving = ref(false)
let paramsTimer: ReturnType<typeof setTimeout> | null = null
let hydrating = true

const recentOutput = computed(() => {
  const outputs = caseInfo.value?.outputs ?? []
  if (!outputs.length) return null
  const adopted = String(caseInfo.value?.adopt_round ?? '')
    .split(/[\\/]/)
    .pop()
  return outputs.find((item) => item.version === adopted) ?? outputs[outputs.length - 1]
})
const rounds = computed<RoundSummary[]>(() => caseInfo.value?.rounds?.rounds ?? [])
const roundsHint = computed(() => caseInfo.value?.rounds?.hint ?? '')

const latestUsableRound = computed<RoundSummary | null>(() => {
  const rows = [...rounds.value]
    .filter((round) => round.result === 'yes')
    .sort((a, b) => String(b.updated).localeCompare(String(a.updated)))
  return rows[0] ?? null
})
const adoptedRoundName = computed(() =>
  String(caseInfo.value?.adopt_round ?? '')
    .split(/[\\/]/)
    .pop(),
)
const canAdopt = computed(
  () =>
    Boolean(latestUsableRound.value) && latestUsableRound.value?.round !== adoptedRoundName.value,
)

const generationParams = ref<GenerationParams | null>(null)
const modeOptions = ref<ModeInfo[]>([])
const resolutionOptions = ref<OutputOption[]>([])
const scaleOptions = ref<InputOption[]>([])

const selectedCount = computed(() => assets.value.filter((asset) => asset.selected).length)
const promptOpen = ref(false)
const uploadOpen = ref(false)

type SaveState = 'saved' | 'saving' | 'unsaved'
const saveState = ref<SaveState>('saved')

type RunStatus =
  | '未预演'
  | '预演中'
  | '预演通过'
  | '预演已过期'
  | '提交中'
  | '排队中'
  | '运行中'
  | '已完成'
  | '失败'
const status = ref<RunStatus>('未预演')
const statusDetail = ref('')
const previewPassedSig = ref<string | null>(null)
const caseTasks = ref<TaskView[]>([])
const activeTask = ref<TaskView | null>(null)
const adopting = ref(false)
let taskTimer: ReturnType<typeof setInterval> | null = null
let taskInFlight = false

function assetTone(asset: AssetDetail): string {
  if (asset.kind === 'video') return 'linear-gradient(135deg, #1e3a8a, #3b82f6)'
  if (asset.role === 'subject_ref') return 'linear-gradient(135deg, #7c2d12, #f59e0b)'
  if (asset.role === 'scene_ref') return 'linear-gradient(135deg, #134e4a, #14b8a6)'
  return 'linear-gradient(135deg, #4c1d95, #8b5cf6)'
}

function assetMeta(asset: AssetDetail): string {
  const parts: string[] = []
  if (asset.width && asset.height) parts.push(`${asset.width}×${asset.height}`)
  if (asset.duration) parts.push(`${asset.duration.toFixed(2)} 秒`)
  if (asset.fps) parts.push(`${asset.fps}fps`)
  if (!asset.exists) parts.push('文件缺失')
  return parts.join(' · ') || asset.rel || asset.path
}

function toAssetRow(asset: AssetDetail, selected: Set<string>): AssetRow {
  return {
    id: asset.id,
    name: `${asset.label} · ${asset.name}`,
    kind: asset.kind,
    role: ASSET_ROLE_LABELS[asset.role] ?? asset.role,
    roleRaw: asset.role,
    roleFixed: asset.role_fixed,
    meta: assetMeta(asset),
    selected: selected.has(asset.id),
    tone: assetTone(asset),
    url: asset.url,
    exists: asset.exists,
  }
}

function applyCaseInfo(data: CaseInfo): void {
  caseInfo.value = data
  flow.value = data.flow
  const selected = new Set(data.submit_selected ?? [])
  assets.value = (data.assets ?? []).map((asset) => toAssetRow(asset, selected))

  const prompt = data.prompt
  if (prompt?.error) {
    promptRenderError.value = prompt.error
  } else if (prompt) {
    promptVars.value = Object.entries(prompt.vars ?? {}).map(([key, value]) => ({
      key,
      label: prompt.labels?.[key] || key,
      help: prompt.help?.[key] || '',
      value,
    }))
    promptRendered.value = prompt.rendered || prompt.template_text || ''
    promptRenderError.value = prompt.render_error || ''
  }

  const params = data.generation_params
  if (params?.error) {
    paramsError.value = params.error
  } else if (params) {
    generationParams.value = params
    modeOptions.value = params.modes ?? []
    resolutionOptions.value = params.output ?? []
    scaleOptions.value = params.input ?? []
    const current = params.current
    if (current) {
      if (modeOptions.value.some((item) => item.value === current.mode)) {
        mode.value = current.mode
      }
      if (resolutionOptions.value.some((item) => item.value === current.resolution)) {
        resolution.value = current.resolution
      }
      if (scaleOptions.value.some((item) => item.value === current.scale)) {
        scale.value = current.scale
      }
      ratio.value = current.aspect_ratio || 'auto'
      if (current.session_id) {
        selectedSession.value = String(current.session_id)
        selectedProject.value = String(current.project_id || '')
        selectedEpisode.value = String(current.episode || '')
      }
    }
    duration.value = params.fixed?.duration_s ? String(params.fixed.duration_s) : 'auto'
    paramsError.value = ''
  }
}

async function loadCase(): Promise<void> {
  if (!caseName.value) {
    caseError.value = '缺少 case 名称'
    return
  }
  caseLoading.value = true
  caseError.value = ''
  hydrating = true
  try {
    applyCaseInfo(await fetchCase(caseName.value))
  } catch (err) {
    caseError.value = err instanceof Error ? err.message : '读取 case 失败'
    assets.value = []
  } finally {
    caseLoading.value = false
    await nextTick()
    hydrating = false
  }
}

async function toggleAsset(asset: AssetRow, selected: boolean): Promise<void> {
  if (selectionSaving.value) return
  const previous = asset.selected
  asset.selected = selected
  selectionSaving.value = true
  try {
    const ids = assets.value.filter((item) => item.selected).map((item) => item.id)
    const data = await saveSubmitSelection(caseName.value, ids)
    caseInfo.value = data
    const next = new Set(data.submit_selected ?? [])
    assets.value.forEach((item) => {
      item.selected = next.has(item.id)
    })
  } catch (err) {
    asset.selected = previous
    ElMessage.error(err instanceof Error ? err.message : '保存提交集失败')
  } finally {
    selectionSaving.value = false
  }
}

async function updateAssetRole(asset: AssetRow, role: string): Promise<void> {
  try {
    const data = await changeAssetRole(caseName.value, asset.id, role)
    caseInfo.value = data
    const selected = new Set(data.submit_selected ?? [])
    assets.value = (data.assets ?? []).map((item) => toAssetRow(item, selected))
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '修改素材角色失败')
  }
}

function onRoleChange(asset: AssetRow, value: unknown): void {
  void updateAssetRole(asset, String(value))
}

const mode = ref('firstLastFrame')
const ratio = ref('16:9')
const resolution = ref('1080p')
const scale = ref('auto')
const duration = ref('auto')

const projects = ref<ProjectOption[]>([])
const selectedProject = ref('')
const selectedEpisode = ref('')
const selectedSession = ref('0')
const sessionsLoading = ref(false)
const sessionError = ref('')
const lastRefreshed = ref('')

const currentProject = computed(() =>
  projects.value.find((item) => String(item.id) === selectedProject.value),
)
const episodes = computed<EpisodeOption[]>(() => currentProject.value?.episodes ?? [])
const currentEpisode = computed(() =>
  episodes.value.find((item) => String(item.no) === selectedEpisode.value),
)
const sessions = computed<SessionOption[]>(() => currentEpisode.value?.sessions ?? [])

const currentMode = computed(() => modeOptions.value.find((item) => item.value === mode.value))

const ratioOptions = computed<string[]>(() => {
  const ratios = currentMode.value?.ratios ?? []
  return ratios.length ? ratios : ['auto']
})

const durationOptions = computed<{ value: string; label: string }[]>(() => {
  const duration = currentMode.value?.duration
  if (!duration || duration.type !== 'range' || !duration.min || !duration.max) {
    return [{ value: 'auto', label: duration?.type === '' ? '自动（由母版决定）' : '自动' }]
  }
  const autoLabel = generationParams.value?.auto_duration_s
    ? `自动（约 ${generationParams.value.auto_duration_s} 秒）`
    : '自动'
  const options = [{ value: 'auto', label: autoLabel }]
  for (let value = duration.min; value <= duration.max; value += 1) {
    options.push({ value: String(value), label: `${value} 秒` })
  }
  return options
})

function ratioLabel(value: string): string {
  return value === 'auto' ? '跟随输入' : value
}

function scaleLabel(option: InputOption): string {
  const base =
    option.value === 'auto'
      ? '自动'
      : option.value === 'full'
        ? '原始尺寸'
        : option.value === 'half'
          ? '一半'
          : option.value
  const size = option.width && option.height ? `${option.width}×${option.height}` : ''
  if (!option.ok) {
    return `${base}${size ? `（${size}）` : ''}：${option.reason || '不可用'}`
  }
  return size ? `${base}（${size}）` : base
}

function schedulePromptSave(key: string, value: string): void {
  pendingPromptSaves.set(key, value)
  saveState.value = 'unsaved'
  if (promptTimer) clearTimeout(promptTimer)
  promptTimer = setTimeout(() => {
    void flushPromptSaves()
  }, 400)
}

async function flushPromptSaves(): Promise<boolean> {
  if (promptTimer) {
    clearTimeout(promptTimer)
    promptTimer = null
  }
  const entries = [...pendingPromptSaves.entries()]
  if (!entries.length) {
    if (saveState.value === 'unsaved') saveState.value = 'saved'
    return true
  }
  promptSaving.value = true
  saveState.value = 'saving'
  let latest: CaseInfo | null = null
  try {
    for (let index = 0; index < entries.length; index += 1) {
      const [key, value] = entries[index]
      latest = await savePromptVar(caseName.value, key, value)
      pendingPromptSaves.delete(key)
    }
    if (latest) {
      caseInfo.value = latest
      promptRendered.value = latest.prompt?.rendered || promptRendered.value
      promptRenderError.value = latest.prompt?.render_error || ''
    }
    saveState.value = 'saved'
    return true
  } catch (err) {
    for (const [key, value] of entries) {
      if (!pendingPromptSaves.has(key)) pendingPromptSaves.set(key, value)
    }
    saveState.value = 'unsaved'
    ElMessage.error(err instanceof Error ? err.message : '保存提示词失败')
    return false
  } finally {
    promptSaving.value = false
  }
}

function paramsBody() {
  const body: {
    mode: string
    aspect_ratio: string
    resolution?: string
    scale?: string
    session_id?: number
    project_id?: number
    episode?: number
  } = {
    mode: mode.value,
    aspect_ratio: ratio.value,
  }
  if (resolution.value) body.resolution = resolution.value
  if (scale.value) body.scale = scale.value
  if (selectedSession.value !== '') {
    const sessionId = Number(selectedSession.value)
    body.session_id = Number.isFinite(sessionId) ? sessionId : 0
    if (sessionId) {
      body.project_id = Number(selectedProject.value) || 0
      body.episode = Number(selectedEpisode.value) || 0
    } else {
      body.project_id = 0
      body.episode = 0
    }
  }
  return body
}

async function persistParams(): Promise<boolean> {
  if (hydrating || !caseName.value) return true
  if (paramsTimer) {
    clearTimeout(paramsTimer)
    paramsTimer = null
  }
  paramsSaving.value = true
  try {
    const data = await saveParams(caseName.value, paramsBody())
    if (data.generation_params) {
      generationParams.value = data.generation_params
      paramsError.value = data.generation_params.error ?? ''
    }
    return true
  } catch (err) {
    const message = err instanceof Error ? err.message : '保存生成参数失败'
    paramsError.value = message
    ElMessage.error(message)
    return false
  } finally {
    paramsSaving.value = false
  }
}

function scheduleParamsSave(): void {
  if (hydrating) return
  if (paramsTimer) clearTimeout(paramsTimer)
  paramsTimer = setTimeout(() => {
    void persistParams()
  }, 300)
}

watch(ratioOptions, (options) => {
  if (!options.includes(ratio.value)) ratio.value = options[0] ?? 'auto'
})

watch(durationOptions, (options) => {
  if (!options.some((item) => item.value === duration.value)) {
    duration.value = options[0]?.value ?? 'auto'
  }
})

watch([mode, ratio, resolution, scale], scheduleParamsSave)
watch([selectedProject, selectedEpisode, selectedSession], scheduleParamsSave)

function selectStep(key: string): void {
  router.push({ name: 'case', params: { name: caseName.value, step: key } })
}

function onStepUpdated(data: CaseInfo): void {
  applyCaseInfo(data)
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

function openPrompt(): void {
  promptOpen.value = true
}

async function savePrompt(): Promise<void> {
  const saved = await flushPromptSaves()
  if (!saved) return
  promptOpen.value = false
  if (status.value === '预演通过') status.value = '预演已过期'
  ElMessage.success('提示词已保存，预演状态回到未预演')
}

const previewSignature = computed(() =>
  JSON.stringify({
    assets: assets.value
      .filter((item) => item.selected)
      .map((item) => [item.id, item.roleRaw, item.meta]),
    params: [mode.value, ratio.value, resolution.value, scale.value, duration.value],
    prompt: [promptVars.value.map((item) => [item.key, item.value]), promptRendered.value],
    quota: [selectedProject.value, selectedEpisode.value, selectedSession.value],
  }),
)

watch(previewSignature, () => {
  if (status.value === '预演通过' && previewPassedSig.value !== previewSignature.value) {
    status.value = '预演已过期'
  }
})

const generateGate = computed(() => caseInfo.value?.gates?.generate)

function submitBody() {
  const body = {
    mode: mode.value,
    aspect_ratio: ratio.value,
    resolution: resolution.value,
    scale: scale.value,
  } as {
    mode: string
    aspect_ratio: string
    resolution: string
    scale: string
    duration?: number
  }
  if (duration.value !== 'auto') body.duration = Number(duration.value)
  return body
}

const running = computed(() => ['预演中', '提交中', '排队中', '运行中'].includes(status.value))

const activeKind = ref<'dry_run' | 'submit' | 'adopt' | null>(null)
const activeStartedAt = ref(0)

function taskTime(task: TaskView): number {
  const value = new Date(task.created_at).getTime()
  return Number.isNaN(value) ? 0 : value
}

function syncRunStatus(): void {
  if (!activeKind.value) return
  const candidates = caseTasks.value
    .filter((task) => task.kind === activeKind.value)
    .filter((task) => taskTime(task) >= activeStartedAt.value - 5000)
    .sort((a, b) => taskTime(b) - taskTime(a))
  const task = candidates[0]
  if (!task) return
  activeTask.value = task
  if (task.status === 'queued') {
    statusDetail.value = task.queue_position ? `排队第 ${task.queue_position} 位` : '排队中'
  } else if (task.status === 'running') {
    statusDetail.value = '正在执行'
  }
  if (activeKind.value === 'dry_run') {
    if (task.status === 'queued' || task.status === 'running') status.value = '预演中'
    if (task.status === 'done') {
      status.value = '预演通过'
      statusDetail.value = '预演通过，当前选择未改动'
      previewPassedSig.value = previewSignature.value
      activeKind.value = null
    }
    if (task.status === 'failed') {
      status.value = '失败'
      statusDetail.value = task.error || task.tail[task.tail.length - 1] || '预演失败'
      activeKind.value = null
    }
  } else if (activeKind.value === 'submit') {
    if (task.status === 'queued') status.value = '排队中'
    if (task.status === 'running') status.value = '运行中'
    if (task.status === 'done') {
      status.value = '已完成'
      statusDetail.value = '生成任务已完成'
      activeKind.value = null
      void loadCase()
    }
    if (task.status === 'failed') {
      status.value = '失败'
      statusDetail.value = task.error || task.tail[task.tail.length - 1] || '生成失败'
      activeKind.value = null
    }
  } else if (activeKind.value === 'adopt') {
    if (task.status === 'done') {
      adopting.value = false
      activeKind.value = null
      ElMessage.success('已采用成片')
      void loadCase()
    }
    if (task.status === 'failed') {
      adopting.value = false
      activeKind.value = null
      ElMessage.error(task.error || '采用成片失败')
    }
  }
}

async function loadCaseTasks(): Promise<void> {
  if (taskInFlight || document.hidden || !caseName.value) return
  taskInFlight = true
  try {
    const data = await fetchTasks('all')
    caseTasks.value = data.tasks
      .filter((task) => task.case === caseName.value)
      .sort((a, b) => taskTime(b) - taskTime(a))
    if (!activeKind.value) activeTask.value = caseTasks.value[0] ?? null
    syncRunStatus()
  } catch {
    // 任务列表是辅助信息，失败不打断主页面；下一轮轮询会重试。
  } finally {
    taskInFlight = false
  }
}

function startTaskPolling(): void {
  void loadCaseTasks()
  if (taskTimer) clearInterval(taskTimer)
  taskTimer = setInterval(() => {
    void loadCaseTasks()
  }, 2000)
}

async function execute(op: 'dry_run' | 'submit'): Promise<void> {
  if (running.value) return
  if (generateGate.value && !generateGate.value.ready) {
    ElMessage.warning(generateGate.value.reason || '生成条件还没有满足')
    return
  }
  if (!(await flushPromptSaves())) return
  if (!(await persistParams())) return
  activeKind.value = op
  activeStartedAt.value = Date.now()
  activeTask.value = null
  status.value = op === 'dry_run' ? '预演中' : '提交中'
  statusDetail.value = ''
  try {
    await runOperation(caseName.value, op, submitBody())
    startTaskPolling()
  } catch (err) {
    activeKind.value = null
    status.value = '失败'
    statusDetail.value = err instanceof Error ? err.message : '提交失败'
    ElMessage.error(statusDetail.value)
  }
}

function dryRun(): void {
  void execute('dry_run')
}

function submit(): void {
  void execute('submit')
}

async function adoptResult(): Promise<void> {
  const round = latestUsableRound.value
  if (!round || adopting.value) return
  adopting.value = true
  activeKind.value = 'adopt'
  activeStartedAt.value = Date.now()
  try {
    await runOperation(caseName.value, 'adopt', { round: round.round_dir })
    startTaskPolling()
  } catch (err) {
    adopting.value = false
    activeKind.value = null
    ElMessage.error(err instanceof Error ? err.message : '采用成片失败')
  }
}

function applyAssetData(data: CaseInfo): void {
  caseInfo.value = data
  const selected = new Set(data.submit_selected ?? [])
  assets.value = (data.assets ?? []).map((item) => toAssetRow(item, selected))
}

function onAssetsAdded(data: CaseInfo): void {
  applyAssetData(data)
  uploadOpen.value = false
}

async function deleteAsset(asset: AssetRow): Promise<void> {
  try {
    await ElMessageBox.confirm(`删除素材「${asset.name}」？文件会从 case 里移除。`, '删除素材', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
    const data = await removeAsset(caseName.value, asset.id)
    applyAssetData(data)
    ElMessage.success('素材已删除')
  } catch (err) {
    if (err === 'cancel' || err === 'close') return
    ElMessage.error(err instanceof Error ? err.message : '删除素材失败')
  }
}

async function moveAsset(asset: AssetRow, delta: number): Promise<void> {
  const index = assets.value.findIndex((item) => item.id === asset.id)
  const target = index + delta
  if (index < 0 || target < 0 || target >= assets.value.length) return
  const previous = [...assets.value]
  const next = [...assets.value]
  const [moved] = next.splice(index, 1)
  next.splice(target, 0, moved)
  assets.value = next
  try {
    const data = await reorderAssets(
      caseName.value,
      next.map((item) => item.id),
    )
    applyAssetData(data)
  } catch (err) {
    assets.value = previous
    ElMessage.error(err instanceof Error ? err.message : '调整素材顺序失败')
  }
}

async function loadSessions(): Promise<boolean> {
  sessionsLoading.value = true
  sessionError.value = ''
  try {
    const data = await fetchSessions()
    if (data.error) throw new Error(data.error)
    projects.value = data.projects ?? []
    if (!projects.value.some((item) => String(item.id) === selectedProject.value)) {
      selectedProject.value = projects.value.length ? String(projects.value[0].id) : ''
    }
    lastRefreshed.value = new Date().toLocaleTimeString('zh-CN', { hour12: false })
    return true
  } catch (err) {
    sessionError.value = err instanceof Error ? err.message : '读取会话失败'
    return false
  } finally {
    sessionsLoading.value = false
  }
}

async function refreshSessions(): Promise<void> {
  await loadSessions()
}

watch(
  episodes,
  (list) => {
    if (!list.some((item) => String(item.no) === selectedEpisode.value)) {
      selectedEpisode.value = list.length ? String(list[0].no) : ''
    }
  },
  { immediate: true },
)

watch(
  sessions,
  (list) => {
    if (selectedSession.value === '0') return
    if (!list.some((item) => String(item.id) === selectedSession.value)) {
      selectedSession.value = '0'
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
  void loadSessions()
  startTaskPolling()
  void acquireCaseLock()
  startLockHeartbeat()
  window.addEventListener('case-conflict', onCaseConflict)
})

onUnmounted(() => {
  if (taskTimer) {
    clearInterval(taskTimer)
    taskTimer = null
  }
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
  void loadCase()
  void loadCaseTasks()
  void acquireCaseLock()
})

function statusTone(): string {
  if (status.value === '预演通过' || status.value === '已完成') return 'done'
  if (['提交中', '排队中', '运行中', '预演中'].includes(status.value)) return 'queued'
  if (status.value === '失败') return 'failed'
  if (status.value === '预演已过期') return 'stale'
  return ''
}

function formatRoundTime(value: string): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('zh-CN', { hour12: false })
}

function roundResultType(round: RoundSummary): 'success' | 'danger' | 'info' | 'warning' {
  if (round.result === 'yes') return 'success'
  if (round.stage === 'failed') return 'danger'
  if (round.stage === 'prepared') return 'warning'
  return 'info'
}

function roundResultText(round: RoundSummary): string {
  if (round.result === 'yes') return '可用'
  if (round.stage === 'failed') return '失败'
  if (round.stage === 'prepared') return '已准备'
  return '未出片'
}
</script>

<template>
  <div class="page wizard-page">
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

    <template v-else-if="caseInfo">
      <PickStep
        v-if="activeStep === 'pick'"
        :case-name="caseName"
        :info="caseInfo"
        @updated="onStepUpdated"
        @geometry="applyGeometry"
      />
      <ExtractStep
        v-else-if="activeStep === 'extract'"
        :case-name="caseName"
        :info="caseInfo"
        @updated="onStepUpdated"
      />
      <KeyframeStep
        v-else-if="activeStep === 'keyframe'"
        :case-name="caseName"
        :info="caseInfo"
        @updated="onStepUpdated"
        @handoff="onHandoff"
      />
      <MergeStep
        v-else-if="['merge', 'pad', 'recompose'].includes(activeStep)"
        :case-name="caseName"
        :info="caseInfo"
        @updated="onStepUpdated"
      />
      <CompareStep v-else-if="activeStep === 'compare'" :case-name="caseName" :info="caseInfo" />

      <template v-else>
        <div v-loading="caseLoading" class="gen-layout">
          <section class="gen-main">
            <div class="panel">
              <div class="panel-head">
                <h2 class="panel-title">素材与本次提交</h2>
                <div class="panel-head-actions">
                  <span class="muted">{{ selectedCount }} 项已勾选</span>
                  <el-button size="small" :icon="Plus" @click="uploadOpen = true"
                    >上传素材</el-button
                  >
                </div>
              </div>
              <div class="panel-body asset-list">
                <div v-if="!assets.length" class="asset-empty">
                  <el-empty description="还没有素材" :image-size="72" />
                </div>
                <div
                  v-for="(asset, index) in assets"
                  :key="asset.id"
                  class="asset-row"
                  :class="{ dim: !asset.selected }"
                >
                  <el-checkbox
                    :model-value="asset.selected"
                    :disabled="selectionSaving"
                    aria-label="参与本次提交"
                    @change="toggleAsset(asset, Boolean($event))"
                  />
                  <div class="asset-thumb" :style="{ background: asset.tone }">
                    <img
                      v-if="asset.url && asset.exists"
                      :src="asset.url"
                      alt=""
                      class="asset-thumb-img"
                    />
                    <el-icon v-else :size="20">
                      <VideoCamera v-if="asset.kind === 'video'" />
                      <Picture v-else />
                    </el-icon>
                  </div>
                  <div class="asset-main">
                    <div class="asset-name">{{ asset.name }}</div>
                    <div class="asset-meta">{{ asset.meta }}</div>
                  </div>
                  <el-select
                    v-if="!asset.roleFixed"
                    :model-value="asset.roleRaw || asset.role"
                    size="small"
                    style="width: 128px"
                    @change="onRoleChange(asset, $event)"
                  >
                    <el-option
                      v-for="role in ASSET_ROLE_OPTIONS"
                      :key="role.value"
                      :label="role.label"
                      :value="role.value"
                    />
                  </el-select>
                  <span v-else class="role-fixed">{{ asset.role }}</span>
                  <div class="asset-actions">
                    <el-button
                      link
                      :icon="Top"
                      :disabled="index === 0"
                      aria-label="上移素材"
                      @click="moveAsset(asset, -1)"
                    />
                    <el-button
                      link
                      :icon="Bottom"
                      :disabled="index === assets.length - 1"
                      aria-label="下移素材"
                      @click="moveAsset(asset, 1)"
                    />
                    <el-button
                      link
                      type="danger"
                      :icon="Delete"
                      aria-label="删除素材"
                      @click="deleteAsset(asset)"
                    />
                  </div>
                </div>
                <div class="asset-note muted">
                  排序、角色与勾选会即时保存；上传素材下一小步接入。
                </div>
              </div>
            </div>

            <div class="panel">
              <div class="panel-head">
                <h2 class="panel-title">提示词</h2>
                <el-button link type="primary" @click="openPrompt">展开编辑</el-button>
              </div>
              <div class="panel-body">
                <el-alert
                  v-if="promptRenderError"
                  type="warning"
                  :closable="false"
                  :title="promptRenderError"
                  class="prompt-error"
                />
                <div class="prompt-chips">
                  <span v-for="item in promptVars" :key="item.key" class="prompt-chip">
                    {{ item.label }}
                  </span>
                </div>
                <div class="prompt-summary">
                  <div class="prompt-line">
                    <span class="prompt-label">{{ promptVars[0]?.label }}</span>
                    <span>{{ promptVars[0]?.value }}</span>
                  </div>
                  <div class="prompt-line">
                    <span class="prompt-label">{{ promptVars[2]?.label }}</span>
                    <span>{{ promptVars[2]?.value }}</span>
                  </div>
                </div>
                <div class="prompt-note muted">
                  模板正文只读；变量改动会自动保存，提交与预演前会先写入。
                </div>
              </div>
            </div>
          </section>

          <aside class="gen-side">
            <div class="panel">
              <div class="panel-head">
                <h2 class="panel-title">最近成片</h2>
                <el-tag
                  v-if="recentOutput"
                  :type="recentOutput.partial ? 'warning' : 'success'"
                  effect="light"
                  size="small"
                >
                  {{ recentOutput.partial ? '仅中间段' : '可用' }}
                </el-tag>
                <span v-else class="muted">无</span>
              </div>
              <div class="panel-body">
                <video
                  v-if="recentOutput"
                  class="output-video"
                  :src="
                    recentOutput.proxy_state === 'ready' ? recentOutput.proxy_url : recentOutput.url
                  "
                  controls
                  preload="metadata"
                />
                <div v-else class="output-frame">
                  <el-icon :size="34"><VideoPlay /></el-icon>
                  <span>还没有成片</span>
                </div>
                <div v-if="recentOutput" class="output-meta mono">{{ recentOutput.file }}</div>
                <div class="output-actions">
                  <el-button
                    type="primary"
                    plain
                    size="small"
                    :disabled="!canAdopt || adopting"
                    :loading="adopting"
                    @click="adoptResult"
                  >
                    选用成片
                  </el-button>
                  <span class="muted">
                    {{
                      canAdopt
                        ? `最新可用轮次：${latestUsableRound?.round}`
                        : '当前成片已是采用结果'
                    }}
                  </span>
                </div>
              </div>
            </div>

            <div class="panel">
              <div class="panel-head">
                <h2 class="panel-title">生成轮次</h2>
                <span class="muted">{{ rounds.length }} 轮</span>
              </div>
              <div class="panel-body candidate-list">
                <div v-if="!rounds.length" class="candidate-empty">
                  <el-empty description="还没有生成轮次" :image-size="72" />
                  <p v-if="roundsHint" class="muted rounds-hint">{{ roundsHint }}</p>
                </div>
                <div v-for="round in rounds" :key="round.round" class="candidate">
                  <div class="candidate-head">
                    <span class="round-name">{{ round.round }}</span>
                    <span class="muted mono">{{ formatRoundTime(round.updated) }}</span>
                  </div>
                  <div class="round-tags">
                    <el-tag size="small" effect="light">{{ round.stage || '未知阶段' }}</el-tag>
                    <el-tag size="small" effect="light" :type="roundResultType(round)">
                      {{ roundResultText(round) }}
                    </el-tag>
                    <span v-if="round.gate && round.gate !== '?'" class="muted mono">
                      gate={{ round.gate }}
                    </span>
                  </div>
                </div>
              </div>
            </div>
            <div class="panel">
              <div class="panel-head">
                <h2 class="panel-title">最近任务</h2>
                <el-tag
                  v-if="activeTask"
                  size="small"
                  effect="light"
                  :type="
                    activeTask.status === 'failed'
                      ? 'danger'
                      : activeTask.status === 'done'
                        ? 'success'
                        : 'primary'
                  "
                >
                  {{ activeTask.kind }} · {{ activeTask.status }}
                </el-tag>
                <span v-else class="muted">无</span>
              </div>
              <div class="panel-body">
                <div v-if="!activeTask" class="muted task-empty">这个 case 还没有任务。</div>
                <template v-else>
                  <div class="task-summary">
                    <span>{{ activeTask.kind }}</span>
                    <span class="muted mono">{{ activeTask.id }}</span>
                    <span v-if="activeTask.queue_position" class="muted"
                      >排队第 {{ activeTask.queue_position }} 位</span
                    >
                  </div>
                  <el-alert
                    v-if="activeTask.error"
                    type="error"
                    :closable="false"
                    :title="activeTask.error"
                    class="task-error"
                  />
                  <pre v-if="activeTask.tail.length" class="task-tail">{{
                    activeTask.tail.join('\n')
                  }}</pre>
                </template>
              </div>
            </div>
          </aside>
        </div>

        <div class="submit-bar">
          <div class="submit-inner">
            <div class="submit-row">
              <label class="ctl">
                <span>模式</span>
                <el-select v-model="mode" size="small" style="width: 180px">
                  <el-option
                    v-for="item in modeOptions"
                    :key="item.value"
                    :label="item.label"
                    :value="item.value"
                  />
                  <el-option v-if="!modeOptions.length" label="暂无模式" value="" disabled />
                </el-select>
              </label>
              <label class="ctl">
                <span>比例</span>
                <el-select v-model="ratio" size="small" style="width: 110px">
                  <el-option
                    v-for="value in ratioOptions"
                    :key="value"
                    :label="ratioLabel(value)"
                    :value="value"
                  />
                </el-select>
              </label>
              <label class="ctl">
                <span>输出分辨率</span>
                <el-select v-model="resolution" size="small" style="width: 170px">
                  <el-option
                    v-for="item in resolutionOptions"
                    :key="item.value"
                    :label="`${item.value}（${item.width}×${item.height}）`"
                    :value="item.value"
                  />
                </el-select>
              </label>
              <label class="ctl">
                <span>输入分辨率</span>
                <el-select v-model="scale" size="small" style="width: 190px">
                  <el-option
                    v-for="item in scaleOptions"
                    :key="item.value"
                    :label="scaleLabel(item)"
                    :value="item.value"
                    :disabled="!item.ok"
                  />
                </el-select>
              </label>
              <label class="ctl">
                <span>时长</span>
                <el-select v-model="duration" size="small" style="width: 150px">
                  <el-option
                    v-for="item in durationOptions"
                    :key="item.value"
                    :label="item.label"
                    :value="item.value"
                  />
                </el-select>
              </label>
              <span v-if="paramsError" class="session-error">{{ paramsError }}</span>
            </div>
            <div class="submit-row">
              <label class="ctl">
                <span>项目</span>
                <el-select
                  v-model="selectedProject"
                  size="small"
                  style="width: 170px"
                  :loading="sessionsLoading"
                >
                  <el-option
                    v-for="item in projects"
                    :key="item.id"
                    :label="item.name"
                    :value="String(item.id)"
                  />
                </el-select>
              </label>
              <label class="ctl">
                <span>剧集</span>
                <el-select
                  v-model="selectedEpisode"
                  size="small"
                  style="width: 160px"
                  :disabled="!episodes.length"
                >
                  <el-option
                    v-for="item in episodes"
                    :key="item.no"
                    :label="`第 ${item.no} 集 · ${item.name}`"
                    :value="String(item.no)"
                  />
                </el-select>
              </label>
              <label class="ctl">
                <span>会话</span>
                <el-select v-model="selectedSession" size="small" style="width: 220px">
                  <el-option label="个人额度（不使用项目会话）" value="0" />
                  <el-option
                    v-for="item in sessions"
                    :key="item.id"
                    :label="item.name || '未命名'"
                    :value="String(item.id)"
                  />
                </el-select>
              </label>
              <el-button
                size="small"
                :icon="Refresh"
                :loading="sessionsLoading"
                @click="refreshSessions"
              >
                刷新
              </el-button>
              <span class="muted quota-hint">
                已获取 {{ projects.length }} 个项目<template v-if="lastRefreshed">
                  · 刷新于 {{ lastRefreshed }}</template
                >
                <template v-if="paramsSaving"> · 参数保存中</template>
              </span>
              <span v-if="sessionError" class="session-error">{{ sessionError }}</span>
              <div class="submit-actions">
                <span class="run-state">
                  <span class="status-dot" :class="statusTone()" />
                  {{ status }}
                  <span v-if="statusDetail" class="run-detail">{{ statusDetail }}</span>
                </span>
                <el-button
                  size="small"
                  :loading="activeKind === 'dry_run'"
                  :disabled="(running && activeKind !== 'dry_run') || !generateGate?.ready"
                  @click="dryRun"
                >
                  预演检查（不扣费）
                </el-button>
                <el-button
                  type="primary"
                  size="small"
                  :loading="activeKind === 'submit'"
                  :disabled="(running && activeKind !== 'submit') || !generateGate?.ready"
                  @click="submit"
                >
                  提交生成
                </el-button>
              </div>
            </div>
            <div v-if="generateGate && !generateGate.ready" class="gate-reason">
              提交条件未满足：{{ generateGate.reason || '请先完成前一步' }}
            </div>
          </div>
        </div>
      </template>
    </template>

    <el-drawer v-model="promptOpen" title="编辑提示词" size="760px">
      <div class="prompt-editor">
        <section class="template-block">
          <h3>模板正文（只读）</h3>
          <el-alert
            v-if="promptRenderError"
            type="warning"
            :closable="false"
            :title="promptRenderError"
            class="prompt-error"
          />
          <pre>{{ promptRendered || '还没有模板正文' }}</pre>
        </section>
        <section class="vars-block">
          <el-empty
            v-if="!promptVars.length"
            description="没有可编辑的提示词变量"
            :image-size="72"
          />
          <div v-for="item in promptVars" :key="item.key" class="var-card">
            <div class="var-head">
              <span class="var-label">{{ item.label }}</span>
              <span class="var-key mono">{{ item.key }}</span>
            </div>
            <p class="var-help">{{ item.help }}</p>
            <el-input
              v-model="item.value"
              type="textarea"
              :autosize="{ minRows: 3, maxRows: 8 }"
              @input="schedulePromptSave(item.key, item.value)"
            />
          </div>
        </section>
      </div>
      <template #footer>
        <el-button @click="promptOpen = false">关闭</el-button>
        <el-button type="primary" :loading="promptSaving" @click="savePrompt">保存并关闭</el-button>
      </template>
    </el-drawer>

    <AssetUploadDialog v-model="uploadOpen" :case-name="caseName" @added="onAssetsAdded" />
  </div>
</template>

<style scoped>
.wizard-page {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
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

.panel-head-actions {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
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

.placeholder-panel {
  padding: var(--space-8);
}

.gen-layout {
  display: grid;
  grid-template-columns: minmax(0, 1.55fr) minmax(320px, 1fr);
  gap: var(--space-4);
  align-items: start;
}

.gen-main,
.gen-side {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  min-width: 0;
}

.asset-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.asset-row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-2) var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  transition:
    background var(--duration-fast) ease,
    opacity var(--duration-fast) ease;
}

.asset-row:hover {
  background: var(--color-surface-muted);
}

.asset-row.dim {
  opacity: 0.55;
}

.asset-thumb {
  display: grid;
  place-items: center;
  width: 56px;
  height: 40px;
  flex: 0 0 auto;
  overflow: hidden;
  border-radius: var(--radius-sm);
  color: rgba(255, 255, 255, 0.9);
}

.asset-thumb-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.role-fixed {
  width: 128px;
  color: var(--color-text-muted);
  font-size: var(--text-xs);
  text-align: right;
}

.asset-empty {
  padding: var(--space-3);
}

.asset-note {
  margin-top: var(--space-2);
  font-size: var(--text-xs);
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

.asset-main {
  flex: 1;
  min-width: 0;
}

.asset-actions {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  margin-left: var(--space-1);
}

.asset-actions .el-button + .el-button {
  margin-left: 0;
}

.task-empty {
  padding: var(--space-2) 0;
}

.task-summary {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--text-sm);
}

.task-error {
  margin-top: var(--space-2);
}

.task-tail {
  max-height: 180px;
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

.run-detail {
  margin-left: var(--space-2);
  color: var(--color-text-muted);
  font-size: var(--text-xs);
}

.gate-reason {
  padding: var(--space-1) 0 var(--space-2);
  color: var(--color-warning);
  font-size: var(--text-xs);
}

.status-dot.stale {
  background: var(--color-warning);
}

.asset-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 600;
}

.asset-meta {
  color: var(--color-text-muted);
  font-size: var(--text-xs);
}

.add-asset {
  align-self: flex-start;
  margin-top: var(--space-1);
}

.prompt-chips {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-1);
  margin-bottom: var(--space-3);
}

.prompt-chip {
  padding: 2px 8px;
  border-radius: 999px;
  background: var(--color-surface-muted);
  color: var(--color-text-muted);
  font-size: var(--text-xs);
}

.prompt-summary {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.prompt-line {
  display: grid;
  grid-template-columns: 76px minmax(0, 1fr);
  gap: var(--space-2);
  color: var(--color-text);
  font-size: var(--text-sm);
  line-height: 1.55;
}

.prompt-line > span:last-child {
  display: -webkit-box;
  overflow: hidden;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.prompt-label {
  color: var(--color-text-muted);
}

.prompt-note {
  margin-top: var(--space-3);
  font-size: var(--text-xs);
}

.output-frame {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  width: 100%;
  max-width: 560px;
  aspect-ratio: 16 / 9;
  border-radius: var(--radius-md);
  background:
    radial-gradient(circle at 30% 25%, rgba(37, 99, 235, 0.35), transparent 55%),
    linear-gradient(135deg, #0f172a, #334155);
  color: rgba(255, 255, 255, 0.86);
  font-size: var(--text-xs);
}

.output-video {
  display: block;
  width: 100%;
  max-width: 560px;
  aspect-ratio: 16 / 9;
  border-radius: var(--radius-md);
  background: #0f172a;
}

.output-meta {
  margin-top: var(--space-2);
  color: var(--color-text-muted);
  font-size: var(--text-xs);
  word-break: break-all;
}

.output-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  max-width: 560px;
  margin-top: var(--space-3);
  font-size: var(--text-xs);
}

.candidate-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.candidate {
  padding: var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}

.candidate-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  font-size: var(--text-sm);
  font-weight: 600;
}

.round-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.round-tags {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
  margin-top: var(--space-2);
}

.candidate-empty {
  padding: var(--space-2);
}

.rounds-hint {
  margin: var(--space-2) 0 0;
  font-size: var(--text-xs);
  word-break: break-all;
}

.submit-bar {
  position: fixed;
  left: var(--nav-current-width, var(--nav-width));
  right: 0;
  bottom: 0;
  z-index: 20;
  background: rgba(255, 255, 255, 0.97);
  border-top: 1px solid var(--color-border);
  box-shadow: 0 -8px 24px rgba(15, 23, 42, 0.08);
  backdrop-filter: blur(8px);
  transition: left var(--duration-base) ease;
}

.submit-inner {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  max-width: 1600px;
  margin: 0 auto;
  padding: var(--space-3) var(--space-6);
}

.submit-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-3);
}

.quota-hint {
  font-size: var(--text-xs);
  white-space: nowrap;
}

.session-error {
  color: var(--color-danger);
  font-size: var(--text-xs);
}

.submit-actions {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  margin-left: auto;
}

.ctl {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  color: var(--color-text-muted);
  font-size: var(--text-xs);
  white-space: nowrap;
}

.run-state {
  display: inline-flex;
  align-items: center;
  color: var(--color-text-muted);
  font-size: var(--text-sm);
  white-space: nowrap;
}

.prompt-editor {
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
}

.prompt-error {
  margin-bottom: var(--space-3);
}

.template-block h3,
.vars-block h3 {
  margin: 0 0 var(--space-2);
  font-size: var(--text-base);
}

.template-block pre {
  max-height: 260px;
  margin: 0;
  padding: var(--space-3);
  overflow: auto;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface-muted);
  color: var(--color-text-muted);
  font-family: var(--font-sans);
  font-size: var(--text-sm);
  line-height: 1.65;
  white-space: pre-wrap;
}

.vars-block {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.var-card {
  padding: var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}

.var-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-2);
}

.var-label {
  font-weight: 600;
}

.var-key {
  color: var(--color-text-faint);
  font-size: var(--text-xs);
}

.var-help {
  margin: var(--space-1) 0 var(--space-2);
  color: var(--color-text-muted);
  font-size: var(--text-xs);
}

@media (max-width: 1280px) {
  .gen-layout {
    grid-template-columns: minmax(0, 1fr);
  }
}

@media (max-width: 1024px) {
  .submit-inner {
    padding: var(--space-2) var(--space-4);
  }
}
</style>
