<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
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
import ImageGeneratePanel from '@/components/generate/ImageGeneratePanel.vue'
import { defaultRole } from '@/constants/assetRoles'
import { useCaseSaveQueue } from '@/composables/useCaseSaveQueue'
import { useCaseTasks } from '@/composables/useCaseTasks'
import { buildPromptVariables, shouldExpirePreview } from '@/utils/prompt'
import { imageRoundStatus, visibleRounds } from '@/utils/rounds'
import { IMAGE_AUTO_ROLES, IMAGE_ROLE_MODE } from '@/utils/roles'
import { aggregateSaveState, type SaveState } from '@/utils/saveState'
import type { PromptVariable } from '@/types/case'
import type { ReferenceRow } from '@/types/reference'
import type {
  AssetDetail,
  CaseInfo,
  GenerationParams,
  InputOption,
  ModeInfo,
  ModelOption,
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
/** 图片流程（patchimg / fullRedrawImg）与视频流程的生成区走两套控件。 */
const isImageFlow = computed(
  () => props.info.flow === 'patchimg' || props.info.flow === 'fullRedrawImg',
)
/**
 * 素材角色词表按视频生成模式过滤（见 utils/roles）；图片流程没有模式字段，
 * 用 IMAGE_ROLE_MODE 这一个固定上下文去挑图片参考角色。
 */
const submitMode = computed(() => (isImageFlow.value ? IMAGE_ROLE_MODE : mode.value))
/** 图片流程的基图由装配器附带，不出现在角色下拉里。 */
const excludedRoles = computed(() => (isImageFlow.value ? IMAGE_AUTO_ROLES : []))

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
const videoModelOptions = ref<ModelOption[]>([])
const imageModelOptions = ref<ModelOption[]>([])
const imageModelsError = ref('')
const videoModelId = ref<number | null>(null)
const imageModelId = ref<number | null>(null)
/** 图片流程的候选张数（1..4）；只随本次提交生效，不写 params。 */
const imageNum = ref(1)

/** 保存的模型 id 可能不在当前清单里（下架/改名）；补一条占位项，下拉框才不会空白。 */
function withCurrentModel(options: ModelOption[], id: number | null): ModelOption[] {
  if (id == null || id <= 0 || options.some((item) => item.id === id)) return options
  return [{ id, name: `模型 #${id}`, description: '不在当前清单里' }, ...options]
}

const videoModelChoices = computed(() =>
  withCurrentModel(videoModelOptions.value, videoModelId.value),
)
const imageModelChoices = computed(() =>
  withCurrentModel(imageModelOptions.value, imageModelId.value),
)

/** 图片档固定 1K/2K/4K；params 没给清单时用这三档兜底。 */
const imageResolutionValues = computed(() => {
  const values = resolutionOptions.value.map((item) => item.value)
  return values.length ? values : ['1K', '2K', '4K']
})

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
  | '已复用上一轮'
  | '失败'
const status = ref<RunStatus>('未预演')
const statusDetail = ref('')
/** 一次性：勾上后的这次提交不复用已有轮次，提交发起后立刻清空。 */
const resubmit = ref(false)
/**
 * 图片流程的状态回显：本地运行态优先，没有再按轮次台账推导。
 * 页面刷新或 serve 重启后 status 会回到「未预演」，但成片已经出了，
 * 继续显示未预演会让人以为没生成。
 */
const displayStatus = computed(() => {
  if (status.value !== '未预演') return status.value
  return isImageFlow.value ? imageRoundStatus(props.info.image_rounds) : status.value
})
const router = useRouter()

function goStep(step: 'merge' | 'compare'): void {
  void router.push(`/case/${caseName.value}/${step}`)
}
const previewPassedSig = ref<string | null>(null)
const activeTask = ref<TaskView | null>(null)
const adoptingRound = ref<string | null>(null)
/** 图片候选的选用请求键（round:index）；与视频的 adoptingRound 分开。 */
const adoptingImageKey = ref<string | null>(null)
const activeKind = ref<'dry_run' | 'submit' | 'adopt' | null>(null)
const activeStartedAt = ref(0)
let extractStartedAt = 0

const {
  tasks: caseTasks,
  refresh: refreshTasks,
  start: startTasks,
} = useCaseTasks(
  computed(() => props.caseName),
  ['dry_run', 'submit', 'adopt'],
)
// 图片流程的抽取入口在本页，单独轮询，避免把 extract 混进视频页的「最近任务」。
const { tasks: extractTasks, start: startExtractTasks } = useCaseTasks(
  computed(() => props.caseName),
  ['extract'],
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

/**
 * 档位与模型清单只存在于 params 里，且随模型、模式、比例变化。
 * 保存成功与整页刷新都走这里，避免“只换模型 id、不换清单”的半更新。
 */
function applyParamsLists(params: GenerationParams): void {
  generationParams.value = params
  modeOptions.value = params.modes ?? []
  resolutionOptions.value = params.output ?? []
  scaleOptions.value = params.input ?? []
  videoModelOptions.value = params.models ?? []
  imageModelOptions.value = params.image_models ?? []
  imageModelsError.value = params.image_models_error ?? ''
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
      promptVars.value = buildPromptVariables(prompt, { includeUndefined: true })
    promptRendered.value = prompt.rendered || prompt.template_text || ''
    promptRenderError.value = prompt.render_error || ''
  }

  const paramsDirty = paramsSaveQueue.has(caseName.value)
  const params = data.generation_params
  if (params?.error) {
    paramsError.value = params.error
  } else if (params && !paramsDirty) {
    applyParamsLists(params)
    const current = params.current
    if (current) {
      if (isImageFlow.value) {
        // 图片流程没有视频的生成模式；modeOptions 可能为空，别保留视频默认值。
        mode.value = modeOptions.value.some((item) => item.value === current.mode)
          ? current.mode
          : ''
      } else if (modeOptions.value.some((item) => item.value === current.mode)) {
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
    videoModelId.value = params.fixed?.video_model_id ?? null
    imageModelId.value = params.fixed?.image_model_id ?? null
    if (params.fixed?.num) {
      imageNum.value = Math.min(4, Math.max(1, Number(params.fixed.num)))
    }
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
/** 结果音轨：keep 保留，none 下载后去掉（平台没有「不生成声音」的提交参数）。 */
const audio = ref<'keep' | 'none'>('keep')

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
  // clip 推导出的自动时长可能超出当前模式的区间（例如 16.2 秒的 clip 对上
  // allMediaRef 的 15 秒上限）；下拉里如实标出来，否则只看到「约 17 秒」会以为能用。
  const auto = generationParams.value?.fixed?.duration_s ?? generationParams.value?.auto_duration_s
  const overflow = typeof auto === 'number' && auto > duration.max
  const autoLabel = typeof auto === 'number'
    ? `自动（约 ${auto} 秒${overflow ? `，超出 ${duration.max} 秒上限` : ''}）`
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

function sessionBody(): Pick<SaveParamsPayload, 'session_id' | 'project_id' | 'episode'> {
  if (selectedSession.value === '') return {}
  const sessionId = Number(selectedSession.value)
  if (sessionId) {
    return {
      session_id: sessionId,
      project_id: Number(selectedProject.value) || 0,
      episode: Number(selectedEpisode.value) || 0,
    }
  }
  return { session_id: 0, project_id: 0, episode: 0 }
}

function paramsBody(): SaveParamsPayload {
  if (isImageFlow.value) {
    // 图片流程只认识分辨率、图片模型与计费会话；mode/aspect 不属于它。
    const body: SaveParamsPayload = {}
    if (resolution.value) body.resolution = resolution.value
    if (imageModelId.value != null) body.image_model_id = imageModelId.value
    return { ...body, ...sessionBody() }
  }
  const body: SaveParamsPayload = {
    mode: mode.value,
    aspect_ratio: ratio.value,
  }
  if (resolution.value) body.resolution = resolution.value
  if (scale.value) body.scale = scale.value
  if (videoModelId.value != null) body.video_model_id = videoModelId.value
  // 视频流的图片模型只服务首尾帧，改在「首尾帧」页的候选标题栏里设置。
  return { ...body, ...sessionBody() }
}

const paramsSaveQueue = useCaseSaveQueue<SaveParamsPayload>(
  async (entry) => {
    const current = entry.caseName === caseName.value
    if (current) paramsSaving.value = true
    try {
      const data = await saveParams(entry.caseName, entry.payload)
      if (entry.caseName !== caseName.value) return
      if (data.generation_params) {
        // 保存后的清单才是新模型的档位；这里不刷新，下拉会一直停在上一个模型。
        applyParamsLists(data.generation_params)
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
      applyParamsLists(data.generation_params)
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

watch(ratioOptions, (options) => {
  if (!options.includes(ratio.value)) ratio.value = options[0] ?? 'auto'
})

watch(imageResolutionValues, (options) => {
  if (isImageFlow.value && !options.includes(resolution.value)) {
    resolution.value = options[0] ?? '1K'
  }
})

watch(durationOptions, (options) => {
  if (!options.some((item) => item.value === duration.value)) {
    duration.value = options[0]?.value ?? 'auto'
  }
})

watch(resolutionOptions, (options) => {
  // 换模型或换比例后档位会重算；当前值不在新清单里就回落到第一档，
  // 不能把一个该模型不支持的档位留在下拉里再发出去。
  const values = options.map((item) => item.value)
  if (!values.length || values.includes(resolution.value)) return
  resolution.value = values[0]
})

watch(
  [mode, ratio, resolution, scale, videoModelId, imageModelId],
  scheduleParamsSave,
)
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
    params: [
      mode.value,
      ratio.value,
      resolution.value,
      scale.value,
      duration.value,
      audio.value,
      videoModelId.value,
      imageModelId.value,
      imageNum.value,
    ],
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
  if (isImageFlow.value) return ''
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
  if (isImageFlow.value) return ''
  return firstLastFrameIssue.value
})

/** 图片生成按项目会话计费；'0' 是个人额度，不算选中项目会话。 */
const imageSessionReady = computed(() => {
  const value = selectedSession.value
  return value !== '' && value !== '0' && Number.isFinite(Number(value))
})

const extracting = computed(() =>
  extractTasks.value.some(
    (task) => task.kind === 'extract' && (task.status === 'queued' || task.status === 'running'),
  ),
)

function imageSubmitBody(): RunPayload {
  const body: RunPayload = {
    resolution: resolution.value,
    num: imageNum.value,
  }
  if (resubmit.value) body.resubmit = true
  if (imageModelId.value != null && imageModelId.value > 0) {
    body.image_model_id = imageModelId.value
  }
  const sessionId = Number(selectedSession.value)
  if (imageSessionReady.value && Number.isFinite(sessionId)) body.session_id = sessionId
  return body
}

function submitBody(): RunPayload {
  const body: RunPayload = {
    mode: mode.value,
    aspect_ratio: ratio.value,
    resolution: resolution.value,
    scale: scale.value,
    audio: audio.value,
  }
  if (resubmit.value) body.resubmit = true
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
      if (task.reused) {
        // rc=0 且没有发请求：这一轮已存在，钱没花。必须和真提交区分开。
        status.value = '已复用上一轮'
        statusDetail.value =
          `未提交、未花费：复用了 ${task.reused.round}；` +
          '要多抽一版请勾选「再抽一版（重新计费）」再提交'
      } else {
        status.value = '已完成'
        statusDetail.value = '生成任务已完成'
      }
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
      adoptingImageKey.value = null
      activeKind.value = null
      ElMessage.success('已采用成片')
      void refreshCase()
    }
    if (task.status === 'failed') {
      adoptingRound.value = null
      adoptingImageKey.value = null
      activeKind.value = null
      ElMessage.error(task.error || '采用成片失败')
    }
  }
}

watch(caseTasks, syncRunStatus)

// 图片流程的抽取入口在本页：完成/失败后刷新 case，候选与门控才会跟着更新。
watch(extractTasks, (tasks) => {
  if (!extractStartedAt) return
  const task = tasks.find(
    (item) =>
      item.kind === 'extract' &&
      new Date(item.created_at).getTime() >= extractStartedAt - 5000,
  )
  if (!task) return
  if (task.status === 'done') {
    extractStartedAt = 0
    void refreshCase()
    ElMessage.success('抽取完成')
  } else if (task.status === 'failed') {
    extractStartedAt = 0
    ElMessage.error(task.error || task.tail[task.tail.length - 1] || '抽取失败')
  }
})

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
  if (isImageFlow.value && op === 'submit' && !imageSessionReady.value) {
    ElMessage.warning('图片生成按项目会话计费：先选择项目会话再提交')
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
    const body: RunPayload = isImageFlow.value ? imageSubmitBody() : submitBody()
    await runOperation(caseName.value, op, body)
    // 再抽一版是一次性的：提交发起后立即复位，避免下一次静默多花钱。
    if (op === 'submit') resubmit.value = false
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

/** 图片流程的抽取入口：patchimg 受选区门控，fullRedrawImg 总是可跑。 */
async function runImageExtract(): Promise<void> {
  const gate = props.info.gates.extract
  if (gate && gate.ready === false) {
    ElMessage.warning(gate.reason || '抽取条件还没有满足')
    return
  }
  if (extracting.value) return
  try {
    extractStartedAt = Date.now()
    await runOperation(caseName.value, 'extract')
    void refreshTasks()
  } catch (err) {
    extractStartedAt = 0
    ElMessage.error(err instanceof Error ? err.message : '启动抽取失败')
  }
}

/** 图片流程采用一张候选：adopt 必须带轮次目录名与候选下标。 */
async function adoptImage(round: string, index: number): Promise<void> {
  if (adoptingImageKey.value) return
  adoptingImageKey.value = `${round}:${index}`
  activeKind.value = 'adopt'
  activeStartedAt.value = Date.now()
  try {
    await runOperation(caseName.value, 'adopt', { round, index })
    void refreshTasks()
  } catch (err) {
    adoptingImageKey.value = null
    activeKind.value = null
    ElMessage.error(err instanceof Error ? err.message : '采用候选失败')
  }
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
  if (isImageFlow.value) startExtractTasks()
})

onUnmounted(() => {
  void promptSaveQueue.flush()
  void paramsSaveQueue.flush()
})

function statusTone(): string {
  if (status.value === '预演通过' || status.value === '已完成') return 'done'
  if (status.value === '已复用上一轮') return 'stale'
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
        :mode="submitMode"
        :roles="info.roles || []"
        :role-help="info.role_help || {}"
        :selected-count="selectedCount"
        :disabled="selectionSaving"
        :title="isImageFlow ? '本次图片提交' : '本次视频提交'"
        :exclude-roles="excludedRoles"
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
      <ImageGeneratePanel
        v-if="isImageFlow"
        v-model:resolution="resolution"
        v-model:num="imageNum"
        v-model:image-model-id="imageModelId"
        v-model:selected-project="selectedProject"
        v-model:selected-episode="selectedEpisode"
        v-model:selected-session="selectedSession"
        v-model:resubmit="resubmit"
        :case-name="caseName"
        :info="info"
        :resolution-options="imageResolutionValues"
        :image-model-options="imageModelChoices"
        :image-models-error="imageModelsError"
        :projects="projects"
        :episodes="episodes"
        :sessions="sessions"
        :sessions-loading="sessionsLoading"
        :session-error="sessionError"
        :extract-gate="info.gates.extract"
        :generate-gate="generateGate"
        :extracting="extracting"
        :running="running"
        :status="displayStatus"
        :status-detail="statusDetail"
        :run-block-reason="runBlockReason"
        :session-ready="imageSessionReady"
        :adopting-key="adoptingImageKey"
        :active-task="activeTask"
        @extract="runImageExtract"
        @dry-run="dryRun"
        @submit="submit"
        @adopt="adoptImage"
        @go="goStep"
        @refresh-sessions="refreshSessions"
      />
      <GenerateResults
        v-else
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
    v-if="!isImageFlow"
    v-model:mode="mode"
    v-model:ratio="ratio"
    v-model:resolution="resolution"
    v-model:scale="scale"
    v-model:duration="duration"
    v-model:audio="audio"
    v-model:video-model-id="videoModelId"
    v-model:selected-project="selectedProject"
    v-model:selected-episode="selectedEpisode"
    v-model:selected-session="selectedSession"
    v-model:resubmit="resubmit"
    :mode-options="modeOptions"
    :ratio-options="ratioOptions"
    :resolution-options="resolutionOptions"
    :scale-options="scaleOptions"
    :video-model-options="videoModelChoices"
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
    @refresh-sessions="refreshSessions"
    @dry-run="dryRun"
    @submit="submit"
  />

  <AssetPickerDialog
    v-model="pickerOpen"
    :case-name="caseName"
    :info="info"
    :mode="isImageFlow ? 'image' : 'video'"
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
