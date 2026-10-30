<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import {
  fetchCase,
  runOperation,
  saveParams,
  savePromptVar,
  saveSubmitSelection,
} from '@/api/case'
import type { RunPayload, SaveParamsPayload } from '@/api/case'
import { fetchSessions } from '@/api/sessions'
import AssetPickerDialog from '@/components/assets/AssetPickerDialog.vue'
import GenerateSubmitList from '@/components/generate/GenerateSubmitList.vue'
import GeneratePrompt from '@/components/generate/GeneratePrompt.vue'
import GenerateResults from '@/components/generate/GenerateResults.vue'
import GenerateSubmitBar from '@/components/generate/GenerateSubmitBar.vue'
import { defaultRole } from '@/constants/assetRoles'
import { useCaseSaveQueue } from '@/composables/useCaseSaveQueue'
import { useCaseTasks } from '@/composables/useCaseTasks'
import { buildPromptVariables, shouldExpirePreview } from '@/utils/prompt'
import { visibleRounds } from '@/utils/rounds'
import { aggregateSaveState, type SaveState } from '@/utils/saveState'
import type { PromptVariable } from '@/types/case'
import type { ReferenceRow } from '@/types/reference'
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

const props = defineProps<{ caseName: string; info: CaseInfo }>()
const emit = defineEmits<{
  updated: [data: CaseInfo]
  'save-state': [state: SaveState]
}>()

const caseName = computed(() => props.caseName)

const promptVars = ref<PromptVariable[]>([])
const promptRendered = ref('')
const promptRenderError = ref('')
const promptSaving = ref(false)
const selectionSaving = ref(false)
const paramsError = ref('')
const paramsSaving = ref(false)
const promptOpen = ref(false)
const pickerOpen = ref(false)
let syncing = true

const saveState = ref<SaveState>('saved')
const effectiveSaveState = computed(() =>
  aggregateSaveState(
    saveState.value,
    promptSaving.value || paramsSaving.value || selectionSaving.value,
  ),
)
watch(effectiveSaveState, (state) => emit('save-state', state), { immediate: true })

const promptSaveQueue = useCaseSaveQueue<string>(
  async (entry) => {
    if (entry.caseName === caseName.value) saveState.value = 'saving'
    const data = await savePromptVar(entry.caseName, entry.key, entry.payload)
    if (entry.caseName !== caseName.value) return
    promptRendered.value = data.prompt?.rendered || promptRendered.value
    promptRenderError.value = data.prompt?.render_error || ''
    emit('updated', data)
  },
  {
    delayMs: 400,
    onFlush: (ok, name) => {
      if (name && name !== caseName.value) return
      saveState.value = ok ? 'saved' : 'unsaved'
    },
  },
)

const recentOutput = computed(() => {
  const outputs = props.info.outputs ?? []
  return outputs.length ? outputs[outputs.length - 1] : null
})
const rounds = computed<RoundSummary[]>(() => props.info.rounds?.rounds ?? [])
const visibleRoundRows = computed(() => visibleRounds(rounds.value))
const roundsHint = computed(() => props.info.rounds?.hint ?? '')

const adoptedRoundName = computed(() =>
  String(props.info.adopt_round ?? '')
    .split(/[\\/]/)
    .pop() ?? '',
)

const generationParams = ref<GenerationParams | null>(null)
const modeOptions = ref<ModeInfo[]>([])
const resolutionOptions = ref<OutputOption[]>([])
const scaleOptions = ref<InputOption[]>([])

const selectedCount = computed(() => submitRows.value.length)

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
const activeTask = ref<TaskView | null>(null)
const adoptingRound = ref<string | null>(null)
const activeKind = ref<'dry_run' | 'submit' | 'adopt' | null>(null)
const activeStartedAt = ref(0)

const {
  tasks: caseTasks,
  refresh: refreshTasks,
  start: startTasks,
} = useCaseTasks(
  computed(() => props.caseName),
  ['dry_run', 'submit', 'adopt'],
)

const submitRows = computed<ReferenceRow[]>(() => {
  const byId = new Map((props.info.assets ?? []).map((asset) => [asset.id, asset]))
  return (props.info.submit_entries ?? []).map((entry, index) => {
    const asset = byId.get(entry.asset)
    return {
      asset: entry.asset,
      role: entry.role,
      kind: asset?.kind,
      number: index + 1,
      url: asset?.url ?? null,
      exists: asset?.exists ?? false,
      roleFixed: asset?.role_fixed,
      file: asset
        ? {
            name: asset.name,
            rel: asset.rel,
            path: asset.path,
            exists: asset.exists,
            mtime: asset.mtime,
            size: asset.size,
            open_url: asset.open_url,
          }
        : null,
    }
  })
})

function assetById(id: string): AssetDetail | undefined {
  return (props.info.assets ?? []).find((asset) => asset.id === id)
}

function applyInfo(data: CaseInfo): void {
  syncing = true

  const promptDirty = promptSaveQueue.has(caseName.value)
  const prompt = data.prompt
  if (prompt?.error) {
    promptRenderError.value = prompt.error
    if (!promptDirty) {
      promptVars.value = []
      promptRendered.value = ''
    }
  } else if (prompt && !promptDirty) {
    promptVars.value = buildPromptVariables(prompt)
    promptRendered.value = prompt.rendered || prompt.template_text || ''
    promptRenderError.value = prompt.render_error || ''
  }

  const paramsDirty = paramsSaveQueue.has(caseName.value)
  const params = data.generation_params
  if (params?.error) {
    paramsError.value = params.error
  } else if (params && !paramsDirty) {
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
  void nextTick(() => {
    syncing = false
  })
}

async function saveSubmit(
  ids: string[],
  roles: Record<string, string>,
): Promise<void> {
  if (selectionSaving.value) return
  selectionSaving.value = true
  try {
    const data = await saveSubmitSelection(caseName.value, ids, roles)
    emit('updated', data)
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '保存提交清单失败')
  } finally {
    selectionSaving.value = false
  }
}

function defaultSubmitRole(id: string, usedRoles: string[]): string {
  if (mode.value === 'firstLastFrame') {
    if (!usedRoles.includes('first_frame')) return 'first_frame'
    if (!usedRoles.includes('last_frame')) return 'last_frame'
    return 'first_frame'
  }
  return defaultRole(assetById(id)?.kind ?? 'image')
}

async function addFromPicker(ids: string[]): Promise<void> {
  const entries = props.info.submit_entries ?? []
  const roles = { ...(props.info.submit_roles ?? {}) }
  const next = entries.map((entry) => entry.asset)
  const usedRoles = entries.map((entry) => roles[entry.asset] ?? entry.role)
  for (const id of ids) {
    if (next.includes(id)) continue
    const role = defaultSubmitRole(id, usedRoles)
    next.push(id)
    roles[id] = role
    usedRoles.push(role)
  }
  pickerOpen.value = false
  await saveSubmit(next, roles)
}

async function removeSubmit(id: string): Promise<void> {
  const next = (props.info.submit_entries ?? [])
    .map((entry) => entry.asset)
    .filter((asset) => asset !== id)
  const roles = { ...(props.info.submit_roles ?? {}) }
  delete roles[id]
  await saveSubmit(next, roles)
}

async function moveSubmit(id: string, delta: number): Promise<void> {
  const ids = (props.info.submit_entries ?? []).map((entry) => entry.asset)
  const index = ids.indexOf(id)
  const target = index + delta
  if (index < 0 || target < 0 || target >= ids.length) return
  const next = [...ids]
  const [moved] = next.splice(index, 1)
  next.splice(target, 0, moved)
  await saveSubmit(next, { ...(props.info.submit_roles ?? {}) })
}

async function changeSubmitRole(id: string, role: string): Promise<void> {
  const ids = (props.info.submit_entries ?? []).map((entry) => entry.asset)
  await saveSubmit(ids, { ...(props.info.submit_roles ?? {}), [id]: role })
}

function onPickerUpdated(data: CaseInfo): void {
  emit('updated', data)
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

function schedulePromptSave(key: string, value: string): void {
  promptSaveQueue.schedule(caseName.value, key, value)
  saveState.value = 'unsaved'
}

async function flushPromptSaves(): Promise<boolean> {
  const name = caseName.value
  if (!promptSaveQueue.has(name)) {
    if (saveState.value === 'unsaved') saveState.value = 'saved'
    return true
  }
  promptSaving.value = true
  saveState.value = 'saving'
  try {
    const ok = await promptSaveQueue.flush(name)
    if (ok) {
      saveState.value = 'saved'
      return true
    }
    saveState.value = 'unsaved'
    const err = promptSaveQueue.lastError.value
    ElMessage.error(err instanceof Error ? err.message : '保存提示词失败')
    return false
  } finally {
    promptSaving.value = false
  }
}

function paramsBody(): SaveParamsPayload {
  const body: SaveParamsPayload = {
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

const paramsSaveQueue = useCaseSaveQueue<SaveParamsPayload>(
  async (entry) => {
    const current = entry.caseName === caseName.value
    if (current) paramsSaving.value = true
    try {
      const data = await saveParams(entry.caseName, entry.payload)
      if (entry.caseName !== caseName.value) return
      if (data.generation_params) {
        generationParams.value = data.generation_params
        paramsError.value = data.generation_params.error ?? ''
      }
      emit('updated', data)
    } catch (err) {
      if (current) {
        const message = err instanceof Error ? err.message : '保存生成参数失败'
        paramsError.value = message
        ElMessage.error(message)
      }
      throw err
    } finally {
      if (current) paramsSaving.value = false
    }
  },
  { delayMs: 300 },
)

async function persistParams(): Promise<boolean> {
  if (!caseName.value) return true
  const name = caseName.value
  if (paramsSaveQueue.has(name)) {
    const queued = await paramsSaveQueue.flush(name)
    if (!queued) paramsSaveQueue.cancel(name)
  }
  paramsSaving.value = true
  try {
    const data = await saveParams(name, paramsBody())
    if (name === caseName.value && data.generation_params) {
      generationParams.value = data.generation_params
      paramsError.value = data.generation_params.error ?? ''
    }
    emit('updated', data)
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
  if (syncing || !caseName.value) return
  paramsSaving.value = true
  paramsSaveQueue.schedule(caseName.value, 'params', paramsBody())
}

type GenerationPreset = '4k' | '1080p'

/** 两条 4K 路线共用的模型预设；4K 需要 Seedance 2.0（22），1080p 对照用 2.5（37）。 */
async function applyPreset(preset: GenerationPreset): Promise<void> {
  if (!caseName.value || paramsSaving.value) return
  const name = caseName.value
  const viewportWidth = props.info.viewport?.width ?? 0
  if (preset === '4k' && viewportWidth < 3840) {
    ElMessage.warning('当前视口不是 3840×2160；4K 路线建议先在 Pick 页切到 4K 视口')
  }
  if (preset === '1080p' && viewportWidth >= 3840) {
    ElMessage.warning('视口是 4K，1080p 输出会在贴回时被放大；只作对照用')
  }
  if (paramsSaveQueue.has(name)) {
    const queued = await paramsSaveQueue.flush(name)
    if (!queued) paramsSaveQueue.cancel(name)
  }
  const payload: SaveParamsPayload =
    preset === '4k'
      ? {
          model_id: 22,
          // 已经在 4K 可用的两种模式里就保持，否则回到首尾帧。
          mode: ['firstLastFrame', 'allMediaRef'].includes(mode.value)
            ? mode.value
            : 'firstLastFrame',
          resolution: '4K',
          aspect_ratio: '16:9',
        }
      : { model_id: 37, mode: 'videoEdit', resolution: '1080p', aspect_ratio: 'auto' }
  paramsSaving.value = true
  try {
    const data = await saveParams(name, payload)
    if (name !== caseName.value) return
    if (data.generation_params) {
      generationParams.value = data.generation_params
      paramsError.value = data.generation_params.error ?? ''
    }
    emit('updated', data)
    ElMessage.success(
      preset === '4k' ? '已切到 4K（Seedance 2.0）' : '已切回 1080p（Seedance 2.5）',
    )
  } catch (err) {
    const message = err instanceof Error ? err.message : '切换生成预设失败'
    paramsError.value = message
    ElMessage.error(message)
  } finally {
    paramsSaving.value = false
  }
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

async function savePrompt(): Promise<void> {
  const hadPending = promptSaveQueue.has(caseName.value)
  const saved = await flushPromptSaves()
  if (!saved) return
  promptOpen.value = false
  if (shouldExpirePreview(hadPending, status.value)) status.value = '预演已过期'
  ElMessage.success(hadPending ? '提示词已保存' : '提示词没有改动')
}

const addingVar = ref(false)

async function addPromptVar(key: string, value: string): Promise<void> {
  const name = caseName.value
  if (!name || addingVar.value) return
  addingVar.value = true
  try {
    const data = await savePromptVar(name, key, value)
    if (name === caseName.value) applyInfo(data)
    ElMessage.success(`已添加变量 ${key}`)
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '添加变量失败')
  } finally {
    addingVar.value = false
  }
}

const previewSignature = computed(() =>
  JSON.stringify({
    assets: submitRows.value.map((row) => [
      row.asset,
      row.role,
      row.exists,
      row.file?.mtime ?? null,
      row.file?.size ?? null,
    ]),
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

const generateGate = computed(() => props.info.gates?.generate)

const firstLastFrameIssue = computed(() => {
  if (mode.value !== 'firstLastFrame') return ''
  const selected = submitRows.value
  const first = selected.filter((row) => row.role === 'first_frame')
  const last = selected.filter((row) => row.role === 'last_frame')
  if (first.length !== 1 || last.length !== 1) {
    return 'firstLastFrame 需要恰好一张首帧定稿和一张尾帧定稿'
  }
  if (selected.length !== 2) {
    return 'firstLastFrame 不能带视频、mask 或其他素材'
  }
  return ''
})

const runBlockReason = computed(() => {
  if (generateGate.value && !generateGate.value.ready) {
    return generateGate.value.reason || '生成条件还没有满足'
  }
  return firstLastFrameIssue.value
})

function submitBody(): RunPayload {
  const body: RunPayload = {
    mode: mode.value,
    aspect_ratio: ratio.value,
    resolution: resolution.value,
    scale: scale.value,
  }
  if (duration.value !== 'auto') body.duration = Number(duration.value)
  return body
}

const running = computed(() => ['预演中', '提交中', '排队中', '运行中'].includes(status.value))

function taskTime(task: TaskView): number {
  const value = new Date(task.created_at).getTime()
  return Number.isNaN(value) ? 0 : value
}

function syncRunStatus(): void {
  if (!activeKind.value) {
    activeTask.value = caseTasks.value[0] ?? null
    return
  }
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
      void refreshCase()
    }
    if (task.status === 'failed') {
      status.value = '失败'
      statusDetail.value = task.error || task.tail[task.tail.length - 1] || '生成失败'
      activeKind.value = null
    }
  } else if (activeKind.value === 'adopt') {
    if (task.status === 'done') {
      adoptingRound.value = null
      activeKind.value = null
      ElMessage.success('已采用成片')
      void refreshCase()
    }
    if (task.status === 'failed') {
      adoptingRound.value = null
      activeKind.value = null
      ElMessage.error(task.error || '采用成片失败')
    }
  }
}

watch(caseTasks, syncRunStatus)

async function refreshCase(): Promise<void> {
  try {
    emit('updated', await fetchCase(caseName.value))
  } catch {
    // 任务完成后的刷新失败不覆盖页面的成功提示；下一次进入页面会重新读取。
  }
}

async function execute(op: 'dry_run' | 'submit'): Promise<void> {
  if (running.value) return
  if (runBlockReason.value) {
    ElMessage.warning(runBlockReason.value)
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
    void refreshTasks()
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

async function adoptResult(round: RoundSummary): Promise<void> {
  if (!round || adoptingRound.value) return
  adoptingRound.value = round.round
  activeKind.value = 'adopt'
  activeStartedAt.value = Date.now()
  try {
    await runOperation(caseName.value, 'adopt', { round: round.round_dir })
    void refreshTasks()
  } catch (err) {
    adoptingRound.value = null
    activeKind.value = null
    ElMessage.error(err instanceof Error ? err.message : '采用成片失败')
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

watch(() => props.info, applyInfo, { immediate: true })

onMounted(() => {
  void loadSessions()
  startTasks()
})

onUnmounted(() => {
  void promptSaveQueue.flush()
  void paramsSaveQueue.flush()
})

function statusTone(): string {
  if (status.value === '预演通过' || status.value === '已完成') return 'done'
  if (['提交中', '排队中', '运行中', '预演中'].includes(status.value)) return 'queued'
  if (status.value === '失败') return 'failed'
  if (status.value === '预演已过期') return 'stale'
  return ''
}
</script>

<template>
  <div class="gen-layout">
    <section class="gen-main">
      <GenerateSubmitList
        :case-name="caseName"
        :rows="submitRows"
        :mode="mode"
        :roles="info.roles || []"
        :role-help="info.role_help || {}"
        :selected-count="selectedCount"
        :disabled="selectionSaving"
        @add="pickerOpen = true"
        @remove="removeSubmit"
        @move="moveSubmit"
        @role="changeSubmitRole"
      />
      <GeneratePrompt
        v-model:open="promptOpen"
        :variables="promptVars"
        :rendered="promptRendered"
        :render-error="promptRenderError"
        :saving="promptSaving"
        :adding="addingVar"
        @save="savePrompt"
        @input="schedulePromptSave"
        @add-var="addPromptVar"
      />
    </section>

    <aside class="gen-side">
      <GenerateResults
        :recent-output="recentOutput"
        :adopted-round="adoptedRoundName"
        :adopting-round="adoptingRound"
        :visible-round-rows="visibleRoundRows"
        :rounds-count="rounds.length"
        :rounds-hint="roundsHint"
        :active-task="activeTask"
        @adopt="adoptResult"
      />
    </aside>
  </div>

  <GenerateSubmitBar
    v-model:mode="mode"
    v-model:ratio="ratio"
    v-model:resolution="resolution"
    v-model:scale="scale"
    v-model:duration="duration"
    v-model:selected-project="selectedProject"
    v-model:selected-episode="selectedEpisode"
    v-model:selected-session="selectedSession"
    :mode-options="modeOptions"
    :ratio-options="ratioOptions"
    :resolution-options="resolutionOptions"
    :scale-options="scaleOptions"
    :duration-options="durationOptions"
    :projects="projects"
    :episodes="episodes"
    :sessions="sessions"
    :sessions-loading="sessionsLoading"
    :session-error="sessionError"
    :params-error="paramsError"
    :params-saving="paramsSaving"
    :last-refreshed="lastRefreshed"
    :status="status"
    :status-detail="statusDetail"
    :tone="statusTone()"
    :running="running"
    :active-kind="activeKind"
    :run-block-reason="runBlockReason"
    :show-presets="info.flow === 'patch'"
    @refresh-sessions="refreshSessions"
    @preset-4k="applyPreset('4k')"
    @preset-1080p="applyPreset('1080p')"
    @dry-run="dryRun"
    @submit="submit"
  />

  <AssetPickerDialog
    v-model="pickerOpen"
    :case-name="caseName"
    :info="info"
    mode="video"
    @confirm="addFromPicker"
    @updated="onPickerUpdated"
  />
</template>

<style scoped>
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

@media (max-width: 1280px) {
  .gen-layout {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
