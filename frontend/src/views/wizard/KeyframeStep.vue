<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Refresh } from '@element-plus/icons-vue'
import { fetchCase, saveParams } from '@/api/case'
import type { SaveParamsPayload } from '@/api/case'
import { fetchSessions } from '@/api/sessions'
import {
  fetchKeyframePrompt,
  fetchKeyframeRounds,
  handoffKeyframe,
  pickKeyframe,
  runKeyframe,
  saveKeyframePrompt,
  setKeyframeRefs,
  useRawKeyframe,
} from '@/api/keyframe'
import type { KeyframeRefInput, KeyframeRound } from '@/api/keyframe'
import { useCaseTasks } from '@/composables/useCaseTasks'
import { useCaseSaveQueue } from '@/composables/useCaseSaveQueue'
import AssetPickerDialog from '@/components/assets/AssetPickerDialog.vue'
import AssetReferenceList from '@/components/assets/AssetReferenceList.vue'
import KeyframeSubmitBar from '@/components/generate/KeyframeSubmitBar.vue'
import VariableCard from '@/components/prompt/VariableCard.vue'
import FileOpenLink from '@/components/common/FileOpenLink.vue'
import KeyframeRoundBlock from '@/components/keyframe/KeyframeRoundBlock.vue'
import { KEYFRAME_ROLE_OPTIONS } from '@/constants/assetRoles'
import { KEYFRAME_MAX_CANDIDATES, KEYFRAME_RESOLUTIONS } from '@/constants/keyframe'
import { taskStatusMeta } from '@/constants/taskStatus'
import { buildPromptVariables, promptVariablesToRecord } from '@/utils/prompt'
import type { CaseInfo, ModelOption, PromptPayload } from '@/types/case-info'
import type { PromptVariable } from '@/types/case'
import type { ReferenceRow } from '@/types/reference'
import type { EpisodeOption, ProjectOption, SessionOption } from '@/types/session'

const props = defineProps<{ caseName: string; info: CaseInfo }>()
const emit = defineEmits<{
  updated: [data: CaseInfo]
  handoff: [data: CaseInfo]
}>()

type Step = 'first' | 'last'

/** 图片模型能力：比例、分辨率、候选数由 params show 的 `image` 下发。 */
const imageCaps = computed(() => props.info.generation_params?.image)
const imageRatios = computed(() => imageCaps.value?.ratios ?? [])
const imageResolutionValues = computed(() => {
  const values = imageCaps.value?.resolutions ?? []
  return values.length ? values : KEYFRAME_RESOLUTIONS.map((item) => item.value)
})
const imageMaxNum = computed(() => imageCaps.value?.max_num ?? KEYFRAME_MAX_CANDIDATES)
/** patch 的视口比例由服务端下发；几何不合约时是 null。 */
const caseRatio = computed(() => props.info.keyframe?.ratio ?? '16:9')
/** 比例跟着视口走：只列模型支持且与视口一致的比例。 */
const ratioOptions = computed(() => {
  const fixed = caseRatio.value
  const ratios = imageRatios.value
  if (!fixed) return ratios
  if (!ratios.length) return [fixed]
  return ratios.includes(fixed) ? [fixed] : []
})

/** 4K 视口的首尾帧默认出 4K；1080p 视口仍默认 2K，控制额度。 */
function defaultKeyframeResolution(info: CaseInfo, options: string[]): string {
  const wanted = (info.viewport?.width ?? 0) >= 3840 ? '4K' : '2K'
  if (!options.length || options.includes(wanted)) return wanted
  const order: string[] = KEYFRAME_RESOLUTIONS.map((item) => item.value)
  const wantedRank = order.indexOf(wanted)
  const below = options.filter((value) => {
    const rank = order.indexOf(value)
    return rank >= 0 && rank <= wantedRank
  })
  return below.length ? below[below.length - 1] : options[0]
}

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
const refsSaving = ref(false)
const refsPanel = ref<HTMLElement | null>(null)
const pickerOpen = ref(false)
const taskOpen = ref(false)
const olderOpen = ref(false)
const resolution = ref(defaultKeyframeResolution(props.info, imageResolutionValues.value))
const ratio = ref(caseRatio.value)
const num = ref(Math.min(KEYFRAME_MAX_CANDIDATES, imageMaxNum.value))
const noteDraft = ref<Record<string, string>>({})
let startedAt = 0
let syncing = true
let promptTimer: ReturnType<typeof setTimeout> | null = null
let noteTimer: ReturnType<typeof setTimeout> | null = null
let roundsTimer: ReturnType<typeof setInterval> | null = null
let pendingRefs: KeyframeRefInput[] | null = null

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
const stepRounds = computed(() => rounds.value.filter((round) => round.step === step.value))
const recentRounds = computed(() => stepRounds.value.slice(0, 8))
const pinnedRound = computed(() => {
  const pick = stepPick.value
  if (!pick) return null
  return stepRounds.value.find((round) => round.round === pick.round) ?? null
})
// 常显：最新一批，加上当前定稿所在的那一批（它可能不是最新）。
const shownRounds = computed(() => {
  const head = recentRounds.value.slice(0, 1)
  const pinned = pinnedRound.value
  return pinned && !head.some((round) => round.round === pinned.round) ? [...head, pinned] : head
})
const olderRounds = computed(() => {
  const shown = new Set(shownRounds.value.map((round) => round.round))
  return recentRounds.value.filter((round) => !shown.has(round.round))
})
const canRun = computed(() => {
  if (!ready.value?.ready) return false
  if (!primary.value) return false
  if (!sessionReady.value) return false
  if (!ratioOptions.value.length) return false
  if (step.value === 'last' && !firstPick.value) return false
  return true
})
const runReason = computed(() => {
  if (!ready.value?.ready) return ready.value?.reason || '先完成抽取并准备两张原始帧'
  if (!primary.value) return '先添加一张人物三视图并设为主三视图'
  if (!sessionReady.value) return '先在底栏选择项目生成会话；图片生成按项目会话计费'
  if (!ratioOptions.value.length) {
    return `所选图片模型不支持视口比例 ${caseRatio.value}；换一个图片模型`
  }
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
const refs = computed(() => keyframe.value.refs ?? [])
const primary = computed(() => refs.value.find((ref) => ref.role === 'subject_ref') ?? null)
const details = computed(() => refs.value.filter((ref) => ref.role === 'detail_ref'))
/** 首尾帧共用一个图片模型：在固定底栏选择，保存进 generation.toml。 */
const imageModelId = ref<number | null>(null)
const imageModelSaving = ref(false)
const imageModels = computed(() => props.info.generation_params?.image_models ?? [])
const imageModelsError = computed(() => props.info.generation_params?.image_models_error ?? '')
const imageModelChoices = computed<ModelOption[]>(() => {
  const options = imageModels.value
  const id = imageModelId.value
  if (id == null || id <= 0 || options.some((item) => item.id === id)) return options
  // 列表读不到时至少用详情里的名字；两者都没有才退回 id。
  const name = imageCaps.value?.name?.trim() || `图片模型 #${id}`
  return [{ id, name, description: '不在当前清单里' }, ...options]
})

function syncImageModel(): void {
  const id = props.info.generation_params?.fixed?.image_model_id
  imageModelId.value = id == null || id <= 0 ? null : id
}

async function changeImageModel(value: number | null): Promise<void> {
  const saved = props.info.generation_params?.fixed?.image_model_id
  if (value == null || value <= 0 || value === saved) return
  imageModelSaving.value = true
  try {
    const data = await saveParams(props.caseName, { image_model_id: value })
    emit('updated', data)
    ElMessage.success('图片模型已切换')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '切换图片模型失败')
    syncImageModel()
  } finally {
    imageModelSaving.value = false
  }
}

/** 扣费额度与视频页共用 generation.toml；首尾帧运行时由服务端读取会话。 */
const projects = ref<ProjectOption[]>([])
const selectedProject = ref('')
const selectedEpisode = ref('')
const selectedSession = ref('0')
const sessionsLoading = ref(false)
const sessionError = ref('')
const lastRefreshed = ref('')
const sessionSaving = ref(false)

const currentProject = computed(() =>
  projects.value.find((item) => String(item.id) === selectedProject.value),
)
const episodes = computed<EpisodeOption[]>(() => currentProject.value?.episodes ?? [])
const currentEpisode = computed(() =>
  episodes.value.find((item) => String(item.no) === selectedEpisode.value),
)
const sessions = computed<SessionOption[]>(() => currentEpisode.value?.sessions ?? [])
/** 图片生成按项目会话计费；'0' 是个人额度，这里不算选中项目会话。 */
const sessionReady = computed(() => {
  const value = selectedSession.value
  return value !== '' && value !== '0' && Number.isFinite(Number(value))
})

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

function syncSession(): void {
  const current = props.info.generation_params?.current
  if (!current) return
  selectedSession.value = String(current.session_id || 0)
  selectedProject.value = String(current.project_id || '')
  selectedEpisode.value = String(current.episode || '')
}

async function loadSessions(): Promise<void> {
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
  } catch (err) {
    sessionError.value = err instanceof Error ? err.message : '读取会话失败'
  } finally {
    sessionsLoading.value = false
  }
}

const sessionSaveQueue = useCaseSaveQueue<SaveParamsPayload>(
  async (entry) => {
    if (entry.caseName !== props.caseName) return
    sessionSaving.value = true
    try {
      emit('updated', await saveParams(entry.caseName, entry.payload))
    } finally {
      if (entry.caseName === props.caseName) sessionSaving.value = false
    }
  },
  { delayMs: 300 },
)

function scheduleSessionSave(): void {
  if (syncing || !props.caseName) return
  sessionSaveQueue.schedule(props.caseName, 'session', { ...sessionBody() })
}

async function flushSessionSave(): Promise<boolean> {
  if (!sessionSaveQueue.has(props.caseName)) return true
  const ok = await sessionSaveQueue.flush(props.caseName)
  if (!ok) ElMessage.error('保存项目会话失败')
  return ok
}

const barStatus = computed(() => {
  if (running.value) return '生成中'
  if (sessionsLoading.value) return '读取会话中'
  if (!sessionReady.value) return '未选项目会话'
  const task = latest.value
  return task ? taskStatusMeta(task.status).text : '就绪'
})
const barTone = computed(() => {
  if (running.value) return 'queued'
  if (!sessionReady.value) return 'stale'
  const task = latest.value
  if (!task) return ''
  const type = taskStatusMeta(task.status).type
  return type === 'success'
    ? 'done'
    : type === 'danger'
      ? 'failed'
      : type === 'warning'
        ? 'queued'
        : type === 'primary'
          ? 'running'
          : ''
})
const anchorNumber = computed(() =>
  step.value === 'last' && firstPick.value && primary.value ? 3 : null,
)
const refRows = computed<ReferenceRow[]>(() => {
  const rows: ReferenceRow[] = []
  if (primary.value) {
    rows.push({
      asset: primary.value.asset,
      role: 'subject_ref',
      note: primary.value.note,
      number: 2,
      file: primary.value.file,
      url: primary.value.url,
      exists: primary.value.exists,
      locked: true,
      roleLabel: '人物三视图（主图）',
      noteLabel: '主三视图',
    })
  }
  if (step.value === 'last' && firstPick.value && primary.value) {
    rows.push({
      asset: firstPick.value.asset,
      role: 'first_frame',
      number: 3,
      file: firstPick.value.file ?? null,
      url: pickedUrl(firstPick.value),
      exists: true,
      locked: true,
      roleLabel: '首帧定稿',
      noteLabel: '尾帧批次自动追加',
    })
  }
  const start = rows.length && anchorNumber.value ? 4 : 3
  details.value.forEach((ref, index) => {
    rows.push({
      asset: ref.asset,
      role: ref.role,
      note: noteDraft.value[ref.asset] ?? ref.note,
      number: start + index,
      file: ref.file,
      url: ref.url,
      exists: ref.exists,
    })
  })
  return rows
})

function focusRefs(): void {
  refsPanel.value?.scrollIntoView({ behavior: 'smooth', block: 'center' })
}

function assetUrl(assetId: string | null | undefined): string {
  if (!assetId) return ''
  return `/img/asset/${encodeURIComponent(props.caseName)}/${encodeURIComponent(assetId)}`
}

function pickedUrl(pick: { asset: string } | null): string {
  return assetUrl(pick?.asset)
}

function pickedIndexFor(round: KeyframeRound): number | null {
  const pick = stepPick.value
  return pick && pick.round === round.round ? pick.index : null
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
  if (!(await flushSessionSave())) return
  running.value = true
  startedAt = Date.now()
  try {
    const payload: {
      step: Step
      ratio?: string
      resolution?: string
      num?: number
      image_model_id?: number
    } = {
      step: step.value,
      ratio: ratio.value,
      resolution: resolution.value,
      num: num.value,
    }
    if (imageModelId.value != null && imageModelId.value > 0) {
      payload.image_model_id = imageModelId.value
    }
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
      ElMessage.success(
        data.keyframe_hint || '首帧已定稿；尾帧不会自动生成，点「生成尾帧候选」提交',
      )
      step.value = 'last'
      await loadPrompt('last')
    } else {
      ElMessage.success(data.keyframe_hint || '尾帧已定稿')
    }
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '挑选候选失败')
  } finally {
    picking.value = null
  }
}

const rawUsing = ref(false)

async function useRawFrame(): Promise<void> {
  if (rawUsing.value || !rawFrame.value?.exists) return
  rawUsing.value = true
  try {
    const data = await useRawKeyframe(props.caseName, step.value)
    emit('updated', data)
    await loadRounds()
    if (step.value === 'first') {
      ElMessage.success(data.keyframe_hint || '首帧已用原始帧定稿')
      step.value = 'last'
      await loadPrompt('last')
    } else {
      ElMessage.success(data.keyframe_hint || '尾帧已用原始帧定稿')
    }
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '用原始帧定稿失败')
  } finally {
    rawUsing.value = false
  }
}

function currentRefs(): KeyframeRefInput[] {
  return refs.value.map((ref) => ({
    asset: ref.asset,
    role: ref.role,
    note: noteDraft.value[ref.asset] ?? ref.note,
  }))
}

async function saveRefs(next: KeyframeRefInput[]): Promise<void> {
  if (refsSaving.value) {
    pendingRefs = next
    return
  }
  refsSaving.value = true
  try {
    const data = await setKeyframeRefs(props.caseName, next)
    noteDraft.value = {}
    emit('updated', data)
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '保存参考图清单失败')
  } finally {
    refsSaving.value = false
    if (pendingRefs) {
      const queued = pendingRefs
      pendingRefs = null
      void saveRefs(queued)
    }
  }
}

async function addRefs(ids: string[]): Promise<void> {
  const current = currentRefs()
  let hasPrimary = current.some((ref) => ref.role === 'subject_ref')
  const next = [...current]
  for (const id of ids) {
    if (next.some((ref) => ref.asset === id)) continue
    if (!hasPrimary) {
      next.unshift({ asset: id, role: 'subject_ref', note: '' })
      hasPrimary = true
    } else {
      next.push({ asset: id, role: 'detail_ref', note: '' })
    }
  }
  pickerOpen.value = false
  await saveRefs(next)
}

async function changeRefRole(asset: string, role: string): Promise<void> {
  if (role !== 'subject_ref' && role !== 'detail_ref') return
  const current = currentRefs()
  const target = current.find((ref) => ref.asset === asset)
  if (!target) return
  if (role === 'subject_ref') {
    if (target.role === 'subject_ref') return
    const oldPrimary = current.find((ref) => ref.role === 'subject_ref')
    if (oldPrimary) oldPrimary.role = 'detail_ref'
    target.role = 'subject_ref'
    await saveRefs([target, ...current.filter((ref) => ref.asset !== asset)])
    return
  }
  if (target.role === 'subject_ref') {
    ElMessage.warning('先指定另一张人物三视图，再改这张的角色')
    return
  }
  target.role = role
  await saveRefs(current)
}

async function moveRef(asset: string, delta: number): Promise<void> {
  const current = currentRefs()
  const editable = current.filter((ref) => ref.role === 'detail_ref')
  const index = editable.findIndex((ref) => ref.asset === asset)
  const target = index + delta
  if (index < 0 || target < 0 || target >= editable.length) return
  const nextDetails = [...editable]
  const [moved] = nextDetails.splice(index, 1)
  nextDetails.splice(target, 0, moved)
  const primaryRef = current.find((ref) => ref.role === 'subject_ref')
  await saveRefs(primaryRef ? [primaryRef, ...nextDetails] : nextDetails)
}

async function removeRef(asset: string): Promise<void> {
  const current = currentRefs()
  const target = current.find((ref) => ref.asset === asset)
  if (!target || target.role === 'subject_ref') return
  await saveRefs(current.filter((ref) => ref.asset !== asset))
}

function changeRefNote(asset: string, note: string): void {
  noteDraft.value = { ...noteDraft.value, [asset]: note }
  if (noteTimer) clearTimeout(noteTimer)
  noteTimer = setTimeout(() => void saveRefs(currentRefs()), 400)
}

function onPickerUpdated(data: CaseInfo): void {
  emit('updated', data)
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
  olderOpen.value = false
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

watch(() => props.info.generation_params?.fixed?.image_model_id, syncImageModel)

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

watch([selectedProject, selectedEpisode, selectedSession], scheduleSessionSave)

watch(imageResolutionValues, (options) => {
  if (!options.length) return
  if (!options.includes(resolution.value)) {
    resolution.value = defaultKeyframeResolution(props.info, options)
  }
})

watch(ratioOptions, (options) => {
  if (!options.length || options.includes(ratio.value)) return
  ratio.value = options[0]
})

watch(imageMaxNum, (max) => {
  const capped = Math.min(KEYFRAME_MAX_CANDIDATES, Math.max(1, max))
  if (num.value > capped) num.value = capped
})

onMounted(() => {
  syncImageModel()
  syncSession()
  void nextTick(() => {
    syncing = false
  })
  void loadPrompt()
  void loadRounds()
  void loadSessions()
})

onUnmounted(() => {
  if (roundsTimer) clearInterval(roundsTimer)
  if (promptTimer) clearTimeout(promptTimer)
  if (noteTimer) clearTimeout(noteTimer)
  void sessionSaveQueue.flush()
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
          编号由下方参考图清单生成；图1 是当前步骤的原始帧，图2 起是参考图；
          尾帧步骤在人物三视图后插入首帧定稿。
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
              <div v-else class="kf-shot-empty">
                <span>缺{{ rawLabel }}</span>
              </div>
              <FileOpenLink :file="rawFrame" :label="`打开${rawLabel}`" class="card-open" />
            </div>
            <div class="kf-slot-file mono">{{ rawFrame?.name || '—' }}</div>
            <div v-if="rawFrame?.width" class="kf-slot-file mono">
              {{ rawFrame.width }}×{{ rawFrame.height }}
              <template v-if="rawFrame.size">
                · {{ (rawFrame.size / 1048576).toFixed(1) }} MB
              </template>
            </div>
          </figure>

          <figure class="kf-slot">
            <figcaption class="kf-slot-cap">
              <span class="kf-slot-no">{{ primary ? '图2' : '—' }}</span>
              <span>参考图组</span>
              <el-button link type="primary" size="small" class="kf-slot-action" @click="focusRefs">
                编辑参考图
              </el-button>
            </figcaption>
            <div class="kf-shot contain">
              <img v-if="primary" :src="primary.url || assetUrl(primary.asset)" alt="人物三视图" />
              <div v-else class="kf-shot-empty"><span>还没有人物三视图</span></div>
              <span v-if="refs.length > 1" class="kf-stack">共 {{ refs.length }} 张</span>
              <FileOpenLink
                v-if="primary?.file"
                :file="primary.file"
                label="打开三视图"
                class="card-open"
              />
            </div>
            <div class="kf-slot-file mono">{{ primary?.file?.name || '—' }}</div>
          </figure>

          <figure class="kf-slot">
            <figcaption class="kf-slot-cap">
              <span class="kf-slot-no">{{ anchorNumber ?? '—' }}</span>
              <span>首帧定稿</span>
              <span class="kf-slot-note">{{ anchorNumber ? '自动追加' : '尾帧步骤插入' }}</span>
            </figcaption>
            <div v-if="step === 'last' && firstPick && primary" class="kf-shot">
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
              <el-button
                link
                type="primary"
                size="small"
                class="kf-slot-action"
                :loading="rawUsing"
                :disabled="!rawFrame?.exists"
                :title="
                  rawFrame?.exists
                    ? '不重绘，直接把当前抽取的原始帧当定稿（不花额度）'
                    : '当前抽取版本没有这张原始帧；先重新抽取'
                "
                @click="useRawFrame"
              >
                直接用原始帧
              </el-button>
            </figcaption>
            <div v-if="stepPick" class="kf-shot">
              <img :src="pickedUrl(stepPick)" :alt="targetLabel" />
              <FileOpenLink :file="stepPick.file" :label="`打开${targetLabel}`" class="card-open" />
            </div>
            <div v-else class="kf-shot kf-shot-target"><span>从下方候选里挑一张</span></div>
            <div class="kf-slot-file mono">
              {{ stepPick?.file?.name || '待定稿' }}
              <template v-if="stepPick?.source === 'raw'">（原始帧，未重绘）</template>
            </div>
          </figure>
        </div>
      </div>
    </section>

    <section class="panel">
      <div class="panel-head">
        <h2 class="panel-title">参考图清单</h2>
        <span class="spacer" />
        <span class="muted">{{ refs.length }} 张参考图</span>
      </div>
      <div ref="refsPanel" class="panel-body">
        <el-alert
          v-if="!primary"
          type="warning"
          :closable="false"
          title="缺少人物三视图；先添加一张并设为主三视图"
        />
        <AssetReferenceList
          :rows="refRows"
          :role-options="KEYFRAME_ROLE_OPTIONS"
          :role-help="keyframe.role_help || {}"
          :disabled="refsSaving"
          show-note
          add-label="添加参考图"
          empty-text="还没有参考图"
          @add="pickerOpen = true"
          @remove="removeRef"
          @move="moveRef"
          @role="changeRefRole"
          @note="changeRefNote"
        />
        <p class="kf-refs-note muted">
          编号与提示词里的图N 一致；第一张人物三视图固定在图2，尾帧步骤的首帧定稿自动插在它后面。
        </p>
      </div>
    </section>

    <section class="panel">
      <div class="panel-head">
        <h2 class="panel-title">{{ step === 'first' ? '首帧候选' : '尾帧候选' }}</h2>
        <span class="spacer" />
        <el-button link type="primary" :icon="Refresh" :loading="roundsLoading" @click="loadRounds">
          刷新批次
        </el-button>
      </div>
      <div class="panel-body">
        <div v-if="!stepRounds.length" class="empty">
          <el-empty
            :description="step === 'first' ? '还没有首帧批次' : '还没有尾帧批次'"
            :image-size="72"
          />
        </div>
        <div class="round-list">
          <KeyframeRoundBlock
            v-for="round in shownRounds"
            :key="round.round"
            :round="round"
            :picked-index="pickedIndexFor(round)"
            :disabled="Boolean(picking)"
            @pick="(index) => pick(round, index)"
          />
        </div>

        <div v-if="olderRounds.length" class="older-batches">
          <el-button link type="primary" @click="olderOpen = !olderOpen">
            {{ olderOpen ? '收起更早的批次' : `更早的批次（${olderRounds.length}）` }}
          </el-button>
          <div v-if="olderOpen" class="round-list older-list">
            <KeyframeRoundBlock
              v-for="round in olderRounds"
              :key="round.round"
              :round="round"
              :picked-index="pickedIndexFor(round)"
              :disabled="Boolean(picking)"
              @pick="(index) => pick(round, index)"
            />
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
        <span class="muted">{{ step === 'first' ? '只生成首帧' : '用首帧定稿作身份锚点' }}</span>
      </div>
      <div class="panel-body kf-vars">
        <el-alert v-if="promptError" type="warning" :closable="false" :title="promptError" />
        <details class="kf-prompt-full">
          <summary>完整提示词（含参考图分工）</summary>
          <pre v-if="prompt?.rendered" class="kf-prompt-text">{{ prompt.rendered }}</pre>
          <el-alert
            v-else-if="prompt?.render_error"
            type="error"
            :closable="false"
            :title="prompt.render_error"
          />
        </details>
        <VariableCard
          v-for="item in promptVars"
          :key="item.key"
          :variable="item"
          @update:value="(value) => schedulePromptSave(item.key, value)"
        />
      </div>
    </section>

    <KeyframeSubmitBar
      v-model:ratio="ratio"
      v-model:resolution="resolution"
      v-model:num="num"
      v-model:image-model-id="imageModelId"
      v-model:selected-project="selectedProject"
      v-model:selected-episode="selectedEpisode"
      v-model:selected-session="selectedSession"
      :step="step"
      :image-model-options="imageModelChoices"
      :image-models-error="imageModelsError"
      :model-saving="imageModelSaving"
      :resolution-options="imageResolutionValues"
      :ratio-options="ratioOptions"
      :max-num="imageMaxNum"
      :projects="projects"
      :episodes="episodes"
      :sessions="sessions"
      :sessions-loading="sessionsLoading"
      :session-error="sessionError"
      :params-saving="sessionSaving"
      :last-refreshed="lastRefreshed"
      :status="barStatus"
      :status-tone="barTone"
      :running="running"
      :can-run="canRun"
      :run-reason="runReason"
      @model-change="changeImageModel"
      @run="run"
      @refresh-sessions="loadSessions"
    />

    <AssetPickerDialog
      v-model="pickerOpen"
      :case-name="caseName"
      :info="info"
      mode="keyframe"
      @confirm="addRefs"
      @updated="onPickerUpdated"
    />
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

.kf-stack {
  position: absolute;
  right: 6px;
  bottom: 6px;
  display: inline-flex;
  align-items: center;
  height: 20px;
  padding: 0 7px;
  border-radius: 999px;
  background: rgba(15, 23, 42, 0.78);
  color: #fff;
  font-size: 11px;
}

.kf-refs-note {
  margin: var(--space-3) 0 0;
  font-size: var(--text-xs);
  line-height: 1.6;
}

.kf-prompt-full {
  grid-column: 1 / -1;
  font-size: var(--text-sm);
}

.kf-prompt-full summary {
  cursor: pointer;
  font-weight: 600;
}

.kf-prompt-text {
  max-height: 320px;
  margin: var(--space-2) 0 0;
  padding: var(--space-3);
  overflow: auto;
  border-radius: var(--radius-sm);
  background: var(--color-video-bg);
  color: var(--color-video-text);
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  line-height: 1.65;
  white-space: pre-wrap;
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

.round-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.older-batches {
  margin-top: var(--space-3);
}

.older-list {
  margin-top: var(--space-2);
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

@media (max-width: 1280px) {
  .kf-slots {
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
