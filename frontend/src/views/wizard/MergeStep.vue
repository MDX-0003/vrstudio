<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Download, Refresh, VideoPlay } from '@element-plus/icons-vue'
import { fetchCase, runOperation } from '@/api/case'
import MergeInputControls from '@/components/assets/MergeInputControls.vue'
import OutputModeSelect from '@/components/assets/OutputModeSelect.vue'
import FileRow from '@/components/common/FileRow.vue'
import RangeRow from '@/components/range/RangeRow.vue'
import type { RangeTag } from '@/components/range/types'
import { useCaseTasks } from '@/composables/useCaseTasks'
import { gateBlocked } from '@/utils/gates'
import { downloadHref } from '@/utils/download'
import { geometrySummary, formatRangeTime } from '@/utils/range'
import { taskStatusMeta } from '@/constants/taskStatus'
import type {
  FullRedrawImageReport,
  CaseInfo,
  MergeRange,
  MergeVersion,
  OutputMode,
  PatchImageReport,
} from '@/types/case-info'

const props = defineProps<{ caseName: string; info: CaseInfo; step: string }>()
const emit = defineEmits<{ updated: [data: CaseInfo] }>()

const { latest, start } = useCaseTasks(
  computed(() => props.caseName),
  ['merge', 'assemble', 'pad', 'recompose', 'export_upload'],
)
const running = ref('')
const allowCrop = ref(false)
let startedAt = 0

const isPatch = computed(() => props.info.flow === 'patch')
const isImageFlow = computed(
  () => props.info.flow === 'patchimg' || props.info.flow === 'fullRedrawImg',
)
const isPatchImg = computed(() => props.info.flow === 'patchimg')
const isFullRedrawImg = computed(() => props.info.flow === 'fullRedrawImg')
const imageMergeLabel = computed(() => (isPatchImg.value ? '贴回' : '拼接'))
const mergeInputKind = computed<'image' | 'video'>(() => (isImageFlow.value ? 'image' : 'video'))
const outputOptions = computed(() => props.info.output_options ?? [])
const outputMode = ref<OutputMode>('case')
const showOutputMode = computed(() => outputOptions.value.length > 1)
watch(outputOptions, (options) => {
  if (!options.some((item) => item.mode === outputMode.value)) outputMode.value = 'case'
})
const mergeGate = computed(() => props.info.gates.merge)
const mergeRanges = computed<MergeRange[]>(() => props.info.merge_ranges ?? [])
const selectedExtract = ref<string | null>(
  props.info.extract_current ?? mergeRanges.value[0]?.versions[0]?.version ?? null,
)
const selectedVersion = computed<MergeVersion | null>(() => {
  for (const group of mergeRanges.value) {
    const match = group.versions.find((item) => item.version === selectedExtract.value)
    if (match) return match
  }
  return null
})
const mergeReady = computed(
  () =>
    Boolean(selectedExtract.value) &&
    Boolean(selectedVersion.value?.has_result || props.info.merge_input),
)
const mergeBlockReason = computed(() => {
  if (!mergeRanges.value.length) return '还没有抽取记录；先去抽取页跑一次。'
  if (!selectedExtract.value) return '先选一个抽取版本。'
  if (!mergeReady.value) return '这个版本没有采用结果，先上传外部视口视频。'
  return ''
})

function selectMergeVersion(version: string): void {
  selectedExtract.value = version
}

function mergeTags(version: MergeVersion): RangeTag[] {
  const tags: RangeTag[] = []
  if (version.version === selectedExtract.value) tags.push({ text: '已选', kind: 'current' })
  tags.push(version.has_result ? { text: '有结果', kind: 'ok' } : { text: '无结果', kind: 'warn' })
  return tags
}

function groupGeometry(group: MergeRange): string {
  return geometrySummary(group.viewport, group.inner, group.frames)
}

function groupPresetLabel(group: MergeRange): string {
  if (!group.presets.length) return '仅抽取记录'
  return `与 ${group.presets.map((item) => formatRangeTime(item.created_at)).join('、')} 的范围相同`
}

function versionMeta(version: MergeVersion): string {
  return version.fps ? `${version.fps.toFixed(1)} fps` : ''
}
const padGate = computed(() => props.info.gates.pad)
const recomposeGate = computed(() => props.info.gates.recompose)
// 几何前提闸门拒绝出片后，页面才给「还是出片（诊断）」这个出口：它只对裁回
// （fullredraw 视频）的 geometry 类别成立，别的报错点它没有意义（服务端只按
// 报错原文归类，见 web/tasks.py 的 ERROR_KINDS）。
const canForce = computed(
  () =>
    props.info.flow === 'fullredraw' &&
    latest.value?.status === 'failed' &&
    latest.value?.error_kind === 'geometry',
)
const FORCE_CONFIRM =
  '只在「几何前提校验失败」（两条冗余带对不上）时才值得点：' +
  '出片的全景几何是错的，接缝会有重复或错位的内容、绕一圈接不上；' +
  '它只能用来先看画面，不能交付，并且会覆盖同名成片。'
const patchFiles = computed(() => props.info.step_files.merge)
const padFiles = computed(() => props.info.step_files.pad)
const recomposeFiles = computed(() => props.info.step_files.recompose)
// 视频全幅重绘按步骤只渲染本步内容（决策 87）：扩充与裁回不再共用一个面板。
const isFullRedrawVideo = computed(() => !isPatch.value && !isImageFlow.value)
const isRecomposeStep = computed(() => props.step === 'recompose')
const padReady = computed(() => Boolean(padGate.value?.ok))
const uploadFiles = computed(() => padFiles.value?.uploads ?? [])
const uploadSummary = computed(() => props.info.pad_upload ?? null)
const uploadGate = computed(() => props.info.upload_gate ?? null)
const uploadBlocked = computed(() =>
  uploadGate.value ? gateBlocked(uploadGate.value) : gateBlocked(padGate.value),
)
// 扩充件还没生成时，原因已经由上方 pad 门控提示；这里只显示重复不了的时长问题。
const uploadBlockReason = computed(() => (padReady.value ? (uploadGate.value?.reason ?? '') : ''))
const uploadHint = computed(() => {
  const up = uploadSummary.value
  if (!up || !up.upload) return '按当前模型的输入限额缩放，供外部工具使用'
  const limit = up.limit?.max_pixel ? `，上限 ${up.limit.max_pixel.toLocaleString()} 像素` : ''
  const model = up.video_model_id ? `model ${up.video_model_id}` : ''
  return `按 ${[model, up.mode, `scale ${up.scale}`].filter(Boolean).join(' · ')} 缩放${limit}`
})
// 生成参数变过之后，已有的上传件不再对应本次提交；页面给出重新生成的入口。
const uploadStale = computed(() => {
  const up = uploadSummary.value
  const params = props.info.generation_params
  if (!up || !params) return false
  if (up.scale && params.current?.scale && up.scale !== params.current.scale) return true
  if (up.mode && params.current?.mode && up.mode !== params.current.mode) return true
  if (
    up.video_model_id &&
    params.fixed?.video_model_id &&
    up.video_model_id !== params.fixed.video_model_id
  )
    return true
  return false
})
// 后端 ``_outputs`` 按 mtime 升序（对比页取最后一项作默认）；成片面板反过来
// 展示，最新一份排在最上面。
const outputs = computed(() => [...props.info.outputs].reverse())
const patchReport = computed<PatchImageReport | null>(() =>
  props.info.flow === 'patchimg' ? ((props.info.image_report as PatchImageReport) ?? null) : null,
)
const fullReport = computed<FullRedrawImageReport | null>(() =>
  props.info.flow === 'fullRedrawImg'
    ? ((props.info.image_report as FullRedrawImageReport) ?? null)
    : null,
)
const imageOutputs = computed(() => outputs.value.filter((file) => file.open_url))

function verdictType(value: string | undefined): 'success' | 'warning' | 'danger' {
  if (value === 'ok') return 'success'
  if (value === 'bad') return 'danger'
  return 'warning'
}

onMounted(() => start())

watch(
  () => props.info.merge_ranges,
  (ranges) => {
    const versions = (ranges ?? []).flatMap((group) => group.versions.map((item) => item.version))
    if (selectedExtract.value && versions.includes(selectedExtract.value)) return
    selectedExtract.value = props.info.extract_current ?? versions[0] ?? null
  },
)

async function runOp(
  op: 'merge' | 'pad' | 'recompose' | 'export_upload',
  extra: Record<string, unknown> = {},
): Promise<void> {
  if (running.value) return
  running.value = op
  startedAt = Date.now()
  try {
    const payload: Record<string, unknown> = { ...extra }
    if (op === 'merge' && !isImageFlow.value) payload.allow_crop = allowCrop.value
    if (op === 'merge' && isPatch.value && selectedExtract.value) {
      payload.extract = selectedExtract.value
    }
    if (op === 'recompose' || (op === 'merge' && isFullRedrawImg.value)) {
      payload.output_mode = outputMode.value
    }
    await runOperation(props.caseName, op, payload)
    start()
  } catch (err) {
    running.value = ''
    ElMessage.error(err instanceof Error ? err.message : '启动任务失败')
  }
}

// 用户明确要求「先看一眼」时才带 force；确认框把利害与适用范围说在前面，
// 不把它做成常驻开关，避免有人在正常流程里勾上后拿到一张接不上的全景。
async function forceRender(): Promise<void> {
  if (running.value) return
  try {
    await ElMessageBox.confirm(FORCE_CONFIRM, '还是出片（诊断）', {
      type: 'warning',
      confirmButtonText: '还是出片',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  await runOp('recompose', { force: true })
}

watch(latest, async (task) => {
  if (!running.value || !task) return
  const created = new Date(task.created_at).getTime()
  if (Number.isNaN(created) || created < startedAt - 5000) return
  if (task.status === 'done' && task.kind !== 'assemble') {
    running.value = ''
    emit('updated', await fetchCase(props.caseName))
    ElMessage.success('任务完成')
  } else if (task.status === 'failed') {
    running.value = ''
    ElMessage.error(task.error || '任务失败，原因见下方日志')
  }
})
</script>

<template>
  <div class="merge-layout">
    <section class="panel">
      <div class="panel-head">
        <h2 class="panel-title">
          {{
            isImageFlow
              ? `${imageMergeLabel}成片`
              : isPatch
                ? '合并与时间拼接'
                : isRecomposeStep
                  ? '裁回原尺寸'
                  : '扩充画面'
          }}
        </h2>
        <el-button link type="primary" :icon="Refresh" @click="start()">刷新任务</el-button>
      </div>
      <div class="panel-body">
        <template v-if="isImageFlow">
          <div class="flow-line">
            <span class="node">抽取</span>
            <span class="muted">→</span>
            <span class="node">生成并选用一张候选</span>
            <span class="muted">→</span>
            <span class="node">{{ imageMergeLabel }}成片</span>
          </div>
          <el-alert
            v-if="mergeGate && !mergeGate.ready"
            type="warning"
            :closable="false"
            :title="mergeGate.reason || '先在生成步选用一张候选'"
            class="gate-alert"
          />
          <div class="action-row">
            <el-button
              type="primary"
              size="small"
              :icon="VideoPlay"
              :loading="running === 'merge'"
              :disabled="gateBlocked(mergeGate)"
              @click="runOp('merge')"
            >
              开始{{ imageMergeLabel }}
            </el-button>
            <OutputModeSelect v-if="showOutputMode" v-model="outputMode" :options="outputOptions" />
            <span v-if="mergeGate?.ok" class="muted"> 已有成片；再跑一次会覆盖同名文件 </span>
          </div>
          <MergeInputControls
            v-if="isFullRedrawImg"
            :case-name="caseName"
            :info="info"
            kind="image"
            purpose="image"
            @updated="emit('updated', $event)"
          />

          <div v-if="patchReport || fullReport" class="report-box">
            <h3>{{ imageMergeLabel }}报告</h3>
            <div v-if="patchReport" class="report-grid mono">
              <span>改动像素（选区外为 0）</span>
              <span
                >{{ patchReport.local.changed_inside }} /
                {{ patchReport.local.support_pixels }}</span
              >
              <span>选区外最大差异</span>
              <span>{{ patchReport.local.maxdiff_outside }}</span>
              <span>ERP 尺寸</span>
              <span>{{ patchReport.erp[0] }}×{{ patchReport.erp[1] }}</span>
            </div>
            <div v-if="fullReport" class="report-grid mono">
              <span>配准相关性</span>
              <span>{{ fullReport.registration.correlation.toFixed(3) }}</span>
              <span>闸门 / 结论</span>
              <span>
                <el-tag size="small" effect="light" :type="verdictType(fullReport.gate)">
                  {{ fullReport.gate }}
                </el-tag>
                <el-tag size="small" effect="light" :type="verdictType(fullReport.verdict)">
                  {{ fullReport.verdict }}
                </el-tag>
              </span>
              <span>宽高漂移</span>
              <span>{{ fullReport.aspect_drift_pct.toFixed(1) }}%</span>
              <span>接缝原因</span>
              <span>{{ fullReport.reasons.length ? fullReport.reasons.join('；') : '无' }}</span>
            </div>
          </div>

          <div class="file-groups">
            <div class="file-group">
              <h3>{{ imageMergeLabel }}输入</h3>
              <FileRow v-for="file in patchFiles?.inputs ?? []" :key="file.path" :entry="file" />
            </div>
            <div class="file-group">
              <h3>{{ imageMergeLabel }}输出</h3>
              <FileRow v-for="file in patchFiles?.outputs ?? []" :key="file.path" :entry="file" />
              <span v-if="!(patchFiles?.outputs ?? []).length" class="muted">还没有输出</span>
            </div>
          </div>
        </template>

        <template v-else-if="isPatch">
          <div class="flow-line">
            <span class="node">空间合并</span>
            <span class="muted">→</span>
            <span class="node">时间拼接（成功后自动排队）</span>
          </div>

          <section class="merge-ranges">
            <h3>合并范围</h3>
            <p class="muted merge-ranges-hint">
              只列抽取记录；点击版本行选择本次合并用哪版，不改最新抽取版本。
            </p>
            <div v-if="!mergeRanges.length" class="muted">还没有抽取记录。</div>
            <div v-for="group in mergeRanges" :key="groupGeometry(group)" class="range-group">
              <div class="group-head">
                <span class="mono">{{ groupGeometry(group) }}</span>
                <span class="muted">{{ groupPresetLabel(group) }}</span>
              </div>
              <RangeRow
                v-for="version in group.versions"
                :key="version.version"
                :time="version.version"
                :geometry="versionMeta(version)"
                :meta="version.has_result ? '有采用结果' : '无采用结果'"
                :tags="mergeTags(version)"
                :selected="version.version === selectedExtract"
                selectable
                @select="selectMergeVersion(version.version)"
              />
            </div>
          </section>

          <MergeInputControls
            :case-name="caseName"
            :info="info"
            kind="video"
            purpose="patch"
            @updated="emit('updated', $event)"
          />
          <el-alert
            v-if="!mergeReady"
            type="warning"
            :closable="false"
            :title="mergeBlockReason"
            class="gate-alert"
          />
          <div class="action-row">
            <el-checkbox v-model="allowCrop">允许裁切</el-checkbox>
            <el-button
              type="primary"
              size="small"
              :icon="VideoPlay"
              :loading="running === 'merge'"
              :disabled="!mergeReady"
              @click="runOp('merge')"
            >
              开始合并
            </el-button>
            <span v-if="mergeGate?.ok" class="muted">已有成片；再跑一次会覆盖同名文件</span>
          </div>
          <p class="crop-hint muted">
            AI 成片与视口比例不一致时默认拒绝合并；勾选后按 cover 缩放并居中裁掉多余画面。
          </p>
          <div class="file-groups">
            <div class="file-group">
              <h3>本次合并输入</h3>
              <span class="muted">
                版本 {{ selectedExtract ?? '未选' }} ·
                {{ selectedVersion?.has_result ? 'result.mp4' : '需要外部视口视频' }}
              </span>
            </div>
            <div class="file-group">
              <h3>合并输出</h3>
              <FileRow v-for="file in patchFiles?.outputs ?? []" :key="file.path" :entry="file" />
              <span v-if="!(patchFiles?.outputs ?? []).length" class="muted">还没有输出</span>
            </div>
          </div>
        </template>

        <template v-else>
          <div class="flow-line">
            <span class="node" :class="{ current: !isRecomposeStep }">扩充画面</span>
            <span class="muted">→</span>
            <span class="node">生成</span>
            <span class="muted">→</span>
            <span class="node" :class="{ current: isRecomposeStep }">裁回原尺寸</span>
          </div>

          <template v-if="isRecomposeStep">
            <MergeInputControls
              :case-name="caseName"
              :info="info"
              :kind="mergeInputKind"
              purpose="recompose"
              @updated="emit('updated', $event)"
            />
            <div class="action-row step-action">
              <OutputModeSelect
                v-if="showOutputMode"
                v-model="outputMode"
                :options="outputOptions"
              />
              <el-button
                type="primary"
                size="small"
                :icon="VideoPlay"
                :loading="running === 'recompose'"
                :disabled="gateBlocked(recomposeGate)"
                @click="runOp('recompose')"
              >
                裁回原尺寸
              </el-button>
            </div>
            <el-alert
              v-if="recomposeGate && !recomposeGate.ready"
              type="warning"
              :closable="false"
              :title="recomposeGate.reason || '先采用一次生成结果'"
              class="gate-alert"
            />
            <div class="file-group">
              <h3>裁回产物</h3>
              <FileRow
                v-for="file in recomposeFiles?.outputs ?? []"
                :key="file.path"
                :entry="file"
              />
              <span v-if="!(recomposeFiles?.outputs ?? []).length" class="muted"> 还没有输出 </span>
            </div>
          </template>

          <template v-else>
            <div class="action-row step-action">
              <el-button
                type="primary"
                size="small"
                :icon="VideoPlay"
                :loading="running === 'pad'"
                :disabled="gateBlocked(padGate)"
                @click="runOp('pad')"
              >
                {{ padReady ? '重新扩充' : '扩充画面' }}
              </el-button>
              <span v-if="padReady" class="muted">再跑一次会覆盖同名文件</span>
            </div>
            <el-alert
              v-if="padGate && !padGate.ok"
              type="info"
              :closable="false"
              :title="padGate.reason || '还没有扩充过画面'"
              class="gate-alert"
            />

            <div class="file-group">
              <div class="file-group-head">
                <h3>扩充产物</h3>
                <span class="muted group-hint">全尺寸，给本平台的生成路线用</span>
              </div>
              <FileRow v-for="file in padFiles?.outputs ?? []" :key="file.path" :entry="file" />
              <span v-if="!(padFiles?.outputs ?? []).length" class="muted">还没有输出</span>
            </div>

            <div class="file-group upload-group">
              <div class="file-group-head">
                <h3>
                  生成平台合规分辨率副本
                  <el-tag size="small" effect="light" type="primary">外部工具用这组</el-tag>
                </h3>
                <span class="muted group-hint">{{ uploadHint }}</span>
              </div>
              <el-alert
                v-if="uploadBlockReason"
                type="info"
                :closable="false"
                class="gate-alert"
                :title="uploadBlockReason"
              />
              <template v-if="uploadFiles.length">
                <FileRow v-for="file in uploadFiles" :key="file.path" :entry="file">
                  <template v-if="file.exists && file.open_url" #actions>
                    <el-link :href="downloadHref(file.open_url)" type="primary" underline="never">
                      下载
                    </el-link>
                  </template>
                </FileRow>
                <el-alert
                  v-if="uploadStale"
                  type="warning"
                  :closable="false"
                  class="gate-alert"
                  title="当前生成参数已变，这份副本不再对应当前设置"
                >
                  <el-button
                    link
                    type="primary"
                    :loading="running === 'export_upload'"
                    @click="runOp('export_upload')"
                  >
                    按当前参数重新生成
                  </el-button>
                </el-alert>
                <p class="upload-note muted">
                  全尺寸扩充件超过平台像素上限，不能直接上传。这组已按限额缩到同一尺寸：视频与 mask
                  的宽高一致，JSON 记录画布、缩放比例与冗余带位置；交给外部工具时用这组，
                  不要用上面的全尺寸文件。
                </p>
              </template>
              <div v-else class="upload-empty">
                <span class="muted">还没有合规副本；扩充完成后会自动生成，也可以现在补一份</span>
                <el-button
                  size="small"
                  :loading="running === 'export_upload'"
                  :disabled="uploadBlocked"
                  @click="runOp('export_upload')"
                >
                  生成合规副本
                </el-button>
              </div>
            </div>
          </template>
        </template>
      </div>
    </section>

    <section v-if="!isFullRedrawVideo || isRecomposeStep" class="panel">
      <div class="panel-head">
        <h2 class="panel-title">成片</h2>
        <span class="muted">{{ outputs.length }} 个</span>
      </div>
      <div class="panel-body output-list">
        <div v-if="!outputs.length" class="empty">
          <el-empty description="还没有成片" :image-size="72" />
        </div>
        <template v-else-if="isImageFlow">
          <a
            v-for="file in imageOutputs"
            :key="file.file"
            :href="file.open_url ?? ''"
            target="_blank"
            class="image-output"
            :aria-label="`打开 ${file.file}`"
          >
            <img :src="file.open_url ?? ''" :alt="file.file" />
            <span class="mono image-output-name">{{ file.file }}</span>
          </a>
        </template>
        <template v-else>
          <div v-for="file in outputs" :key="file.file" class="output-row">
            <div class="output-main">
              <div class="mono">{{ file.file }}</div>
              <div class="muted">
                {{ file.partial ? '仅中间段（时间拼接未完成）' : '整片' }}
              </div>
            </div>
            <el-link :href="file.open_url ?? ''" target="_blank" type="primary" underline="never">
              <el-icon><Download /></el-icon>播放
            </el-link>
          </div>
        </template>
      </div>
    </section>

    <section class="panel">
      <div class="panel-head">
        <h2 class="panel-title">最近任务</h2>
      </div>
      <div class="panel-body">
        <div v-if="!latest" class="muted">还没有合并或裁回任务。</div>
        <template v-else>
          <div class="task-summary">
            <el-tag size="small" effect="light" :type="taskStatusMeta(latest.status).type">
              {{ latest.kind }} · {{ taskStatusMeta(latest.status).text }}
            </el-tag>
            <span class="mono">{{ latest.id }}</span>
            <span v-if="latest.queue_position" class="muted"
              >排队第 {{ latest.queue_position }} 位</span
            >
          </div>
          <el-alert
            v-if="latest.error"
            type="error"
            :closable="false"
            :title="latest.error"
            class="gate-alert"
          />
          <div v-if="canForce" class="force-row">
            <p class="force-note">
              这条报错说明两条冗余带已经不是同一内容，按固定列切出来的区域错了。强制出片会用错的边界混合：接缝出现重复或错位的内容，最后一段接回开头时接不上；它只能用来先看画面，不能交付，并且会覆盖同名成片。
            </p>
            <p class="force-note muted">
              只有「几何前提校验失败」这一类值得点；文件缺失、参数错、编码失败之类的报错点它没有意义。
            </p>
            <el-button
              size="small"
              type="warning"
              :loading="running === 'recompose'"
              @click="forceRender"
            >
              还是出片（诊断）
            </el-button>
          </div>
          <pre v-if="latest.tail.length" class="task-tail">{{ latest.tail.join('\n') }}</pre>
        </template>
      </div>
    </section>
  </div>
</template>

<style scoped>
.merge-layout {
  display: grid;
  grid-template-columns: minmax(0, 1.4fr) minmax(0, 1fr);
  gap: var(--space-4);
  align-items: start;
}

.merge-ranges {
  margin-top: var(--space-3);
}

.merge-ranges h3 {
  margin: 0;
  font-size: var(--text-sm);
}

.merge-ranges-hint {
  margin: 2px 0 var(--space-2);
  font-size: var(--text-xs);
}

.range-group {
  margin-top: var(--space-2);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  overflow: hidden;
}

.group-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-3);
  border-bottom: 1px solid var(--color-border);
  background: var(--color-surface-muted);
  font-size: var(--text-xs);
}

.flow-line {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
  margin-bottom: var(--space-3);
}

.node {
  padding: 4px 10px;
  border: 1px solid var(--color-border);
  border-radius: 999px;
  background: var(--color-surface-muted);
  font-size: var(--text-sm);
  color: var(--color-text-muted);
}

.node.current {
  border-color: var(--color-primary);
  background: var(--color-primary-soft);
  color: var(--color-primary);
  font-weight: 600;
}

.gate-alert {
  margin-bottom: var(--space-2);
}

.action-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-3);
}

.crop-hint {
  margin: var(--space-1) 0 0;
  font-size: var(--text-xs);
}

.force-row {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: var(--space-2);
  margin-bottom: var(--space-2);
  padding: var(--space-3);
  border: 1px dashed var(--color-border-strong);
  border-radius: var(--radius-md);
}

.force-note {
  margin: 0;
  font-size: var(--text-xs);
  line-height: 1.55;
}

.file-groups {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: var(--space-3);
  margin-top: var(--space-3);
}

.file-group {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.panel-body > .file-group {
  margin-top: var(--space-4);
}

.file-group h3 {
  margin: 0;
  font-size: var(--text-sm);
}

.file-group-head {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-2);
}

.file-group-head h3 {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
}

.group-hint {
  font-size: var(--text-xs);
}

.step-action {
  margin-bottom: var(--space-2);
}

.upload-group {
  padding-top: var(--space-3);
  border-top: 1px solid var(--color-border);
}

.upload-empty {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-3);
  border: 1px dashed var(--color-border-strong);
  border-radius: var(--radius-md);
}

.upload-note {
  margin: 0;
  font-size: var(--text-xs);
  line-height: 1.55;
}

.output-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.output-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}

.output-main {
  min-width: 0;
  font-size: var(--text-sm);
}

.report-box {
  margin-top: var(--space-3);
  padding: var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}

.report-box h3 {
  margin: 0 0 var(--space-2);
  font-size: var(--text-sm);
}

.report-grid {
  display: grid;
  grid-template-columns: auto 1fr;
  gap: var(--space-1) var(--space-3);
  font-size: var(--text-xs);
}

.report-grid > span:nth-child(odd) {
  color: var(--color-text-muted);
}

.report-grid .el-tag + .el-tag {
  margin-left: var(--space-1);
}

.image-output {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  text-decoration: none;
}

.image-output img {
  width: 100%;
  border-radius: var(--radius-md);
  background: var(--color-video-bg);
}

.image-output-name {
  color: var(--color-text-muted);
  font-size: var(--text-xs);
  word-break: break-all;
}

@media (max-width: 1280px) {
  .merge-layout {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
