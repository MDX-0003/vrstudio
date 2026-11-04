<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Picture, Refresh, VideoPlay } from '@element-plus/icons-vue'
import { fetchCase, runOperation, saveRelightDraft } from '@/api/case'
import { getJson } from '@/api/client'
import FileRow from '@/components/common/FileRow.vue'
import { useCaseTasks } from '@/composables/useCaseTasks'
import { taskStatusMeta } from '@/constants/taskStatus'
import { gateBlocked } from '@/utils/gates'
import type { CaseInfo } from '@/types/case-info'

interface RelightReport {
  size: [number, number]
  frames?: number
  fps?: string
  kind?: 'sample' | 'full'
  started_at?: string
  finished_at?: string
  cache?: { enabled?: boolean; hit?: boolean; load?: number }
  timing?: Record<string, number>
  ref_frame?: number
  ref_matched?: number
  atmo_kind?: 'image' | 'video'
  fields_mode?: 'fixed' | 'per_frame'
  atmo_frames?: number
  atmo_fps?: string
  atmo_mapping?: 'normalized_progress' | 'time' | 'sequential'
  atmo_ref_frame?: number
  atmo_short?: boolean
  atmo_match?: { min: number; mean: number; frames: number } | null
  align: {
    trusted: boolean
    inliers: number
    matches: number
    scale: number
    angle_deg: number
    correlation_before: number
    correlation_after: number
    fallback: string | null
  } | null
  light: {
    gain_mean: number
    gain_p05: number
    gain_p95: number
    visibility_mean: number
  } | null
  clip: {
    knee: number
    max: number
    clipped_before: number | null
    clipped_after?: number
  } | null
  seconds: number
  sample?: { count: number; frames: number[] }
}

const props = defineProps<{ caseName: string; info: CaseInfo }>()
const emit = defineEmits<{ updated: [data: CaseInfo] }>()

const { latest, start } = useCaseTasks(
  computed(() => props.caseName),
  ['relight'],
)
const running = ref('')
const report = ref<RelightReport | null>(null)
const sampleReport = ref<RelightReport | null>(null)
const reportError = ref('')
let startedAt = 0

const payload = computed(() => props.info.relight ?? null)
const draft = computed(() => payload.value?.draft ?? {})
const media = computed(() => payload.value?.media ?? null)
const isVideo = computed(() => media.value === 'video')
const hasSlots = computed(() => Boolean(payload.value?.gt && payload.value?.atmo))
const gate = computed(() => props.info.gates.relight)
const current = computed(() => payload.value?.current ?? null)
const samples = computed(() => payload.value?.samples ?? [])

const form = ref({
  strength: 1,
  local_shading: 0.35,
  highlight_clip: false,
  clip_knee: 0.72,
  clip_max: 0.98,
  ref_frame: '' as string,
  crf: '' as string,
  sample: 1,
})

function applyDraft(): void {
  const d = draft.value
  form.value = {
    strength: d.strength ?? 1,
    local_shading: d.local_shading ?? 0.35,
    highlight_clip: d.highlight_clip ?? false,
    clip_knee: d.clip_knee ?? 0.72,
    clip_max: d.clip_max ?? 0.98,
    ref_frame: d.ref_frame === undefined ? '' : String(d.ref_frame),
    crf: '',
    sample: 1,
  }
}

async function loadReport(): Promise<void> {
  reportError.value = ''
  report.value = null
  const url = payload.value?.current_report?.open_url
  if (!url) return
  try {
    report.value = await getJson<RelightReport>(url)
  } catch (err) {
    reportError.value = err instanceof Error ? err.message : '报告读取失败'
  }
}

async function loadSampleReport(): Promise<void> {
  sampleReport.value = null
  const url = payload.value?.sample_report?.open_url
  if (!url) return
  try {
    sampleReport.value = await getJson<RelightReport>(url)
  } catch {
    // 老小样可能没有报告；时间戳仍可从素材文件名或 mtime 回退。
  }
}

/** 时间戳是本页的重点：运行什么时候发生，比分段数字更先被看到。 */
function formatDate(date: Date): string {
  const pad = (n: number): string => String(n).padStart(2, '0')
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ` +
    `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
  )
}

function formatTime(iso?: string): string {
  if (!iso) return ''
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? iso : formatDate(date)
}

function reportTime(r: RelightReport | null, fallbackMtime?: number | null): string {
  if (r?.started_at) return formatTime(r.started_at)
  if (fallbackMtime) return formatDate(new Date(fallbackMtime * 1000))
  return '时间未知'
}

const MAPPING_LABELS: Record<string, string> = {
  normalized_progress: '按进度',
  time: '按时间',
  sequential: '逐帧',
}

function atmoLabel(r: RelightReport): string {
  if (r.atmo_kind !== 'video') return '图片 · 固定光场'
  const frames = r.atmo_frames ? `${r.atmo_frames} 帧` : '帧数未知'
  const mapping = r.atmo_mapping ? (MAPPING_LABELS[r.atmo_mapping] ?? r.atmo_mapping) : '按进度'
  return `视频 · 逐帧光场（${frames}，${mapping}对齐）`
}

const TIMING_LABELS: Record<string, string> = {
  cache_load: '缓存读取',
  scan: '选帧扫描',
  read: '参考帧解码',
  align: '相似变换',
  fields: '光照场',
  sample_decode: '小样解码',
  sample_compose: '小样合成',
  sample_write: '小样写图',
  render_decode: '渲染解码',
  render_compose: '渲染合成',
  render_write: '编码写入',
  render_flush: '编码收尾',
}

function timingRows(r: RelightReport | null): Array<{ label: string; value: string }> {
  const timing = r?.timing
  if (!timing) return []
  return Object.entries(TIMING_LABELS)
    .filter(([key]) => typeof timing[key] === 'number' && timing[key] > 0)
    .map(([key, label]) => ({ label, value: timing[key].toFixed(2) }))
}

function timingSummary(r: RelightReport | null): string {
  const rows = timingRows(r).map((item) => `${item.label} ${item.value}s`)
  return rows.join(' · ')
}

function cacheLabel(cache?: { enabled?: boolean; hit?: boolean } | null): string {
  if (!cache?.enabled) return '未启用缓存'
  return cache.hit ? '命中缓存' : '未命中缓存'
}

onMounted(() => {
  applyDraft()
  void start()
  void loadReport()
  void loadSampleReport()
})

watch(draft, applyDraft)
watch(current, () => void loadReport())
watch(
  () => payload.value?.sample_report?.open_url,
  () => void loadSampleReport(),
)

function runPayload() {
  const body: Record<string, string | number | boolean> = {
    strength: form.value.strength,
    local_shading: form.value.local_shading,
    highlight_clip: form.value.highlight_clip,
  }
  if (form.value.highlight_clip) {
    body.clip_knee = form.value.clip_knee
    body.clip_max = form.value.clip_max
  }
  if (isVideo.value && form.value.ref_frame !== '') {
    body.ref_frame = Number(form.value.ref_frame)
  }
  return body
}

async function run(kind: 'sample' | 'full'): Promise<void> {
  if (running.value) return
  running.value = kind
  startedAt = Date.now()
  try {
    const body = runPayload()
    await saveRelightDraft(props.caseName, body)
    const payloadBody = body as Record<string, unknown>
    if (kind === 'sample') payloadBody.sample = Math.max(1, Math.trunc(form.value.sample))
    if (kind === 'full' && isVideo.value && form.value.crf !== '') {
      payloadBody.crf = Number(form.value.crf)
    }
    await runOperation(props.caseName, 'relight', payloadBody)
    start()
  } catch (err) {
    running.value = ''
    ElMessage.error(err instanceof Error ? err.message : '启动任务失败')
  }
}

watch(latest, async (task) => {
  if (!running.value || !task) return
  const created = new Date(task.created_at).getTime()
  if (Number.isNaN(created) || created < startedAt - 5000) return
  if (task.status === 'done') {
    const wasSample = running.value === 'sample'
    running.value = ''
    emit('updated', await fetchCase(props.caseName))
    ElMessage.success(wasSample ? '小样出来了，看下面的条' : '重打光完成')
  } else if (task.status === 'failed') {
    running.value = ''
    ElMessage.error(task.error || '任务失败，原因见下方日志')
  }
})

const percent = (value: number | undefined): string =>
  value === undefined || value === null ? '—' : `${(value * 100).toFixed(2)}%`
</script>

<template>
  <div class="relight-layout">
    <section class="panel">
      <div class="panel-head">
        <h2 class="panel-title">打光参数</h2>
        <el-tag v-if="media" size="small" effect="light">
          底版：{{ media === 'video' ? '视频（整段）' : '图片（单帧）' }}
        </el-tag>
      </div>
      <div class="panel-body">
        <el-alert
          v-if="!hasSlots"
          type="warning"
          :closable="false"
          :title="props.info.gates.assets?.reason || '先在素材步选定底版与氛围依据'"
          class="gate-alert"
        />

        <div class="param-grid">
          <label class="param">
            <span>整体施加量</span>
            <el-slider v-model="form.strength" :min="0" :max="1" :step="0.05" show-input />
          </label>
          <label class="param">
            <span>局部明暗</span>
            <el-slider v-model="form.local_shading" :min="0" :max="1" :step="0.05" show-input />
          </label>
          <label v-if="isVideo" class="param">
            <span>参考帧（留空自动选）</span>
            <el-input v-model="form.ref_frame" size="small" placeholder="例如 12；0 起计" />
          </label>
          <label v-if="isVideo" class="param">
            <span>编码质量 crf（留空用配置）</span>
            <el-input v-model="form.crf" size="small" placeholder="0–51，18 是生产默认" />
          </label>
        </div>

        <div class="clip-row">
          <el-checkbox v-model="form.highlight_clip">高光软裁切</el-checkbox>
          <template v-if="form.highlight_clip">
            <span class="muted">拐点</span>
            <el-input-number
              v-model="form.clip_knee"
              :min="0.01"
              :max="0.99"
              :step="0.01"
              size="small"
            />
            <span class="muted">上限</span>
            <el-input-number
              v-model="form.clip_max"
              :min="0.02"
              :max="1"
              :step="0.01"
              size="small"
            />
          </template>
        </div>
        <p class="clip-hint muted">
          高光软裁切（通常不需要开，只在合并完成后，对亮度大于拐点的像素进行缩放）
        </p>

        <div class="action-row">
          <template v-if="isVideo">
            <span class="muted">小样帧数</span>
            <el-input-number
              v-model="form.sample"
              :min="1"
              :max="30"
              size="small"
              aria-label="小样帧数"
            />
            <el-button
              size="small"
              :icon="Picture"
              :loading="running === 'sample'"
              :disabled="gateBlocked(gate)"
              @click="run('sample')"
            >
              先看 {{ form.sample }} 帧小样
            </el-button>
            <el-button
              type="primary"
              size="small"
              :icon="VideoPlay"
              :loading="running === 'full'"
              :disabled="gateBlocked(gate)"
              @click="run('full')"
            >
              开始重打光（整段）
            </el-button>
          </template>
          <el-button
            v-else
            type="primary"
            size="small"
            :icon="VideoPlay"
            :loading="running === 'full'"
            :disabled="gateBlocked(gate)"
            @click="run('full')"
          >
            开始重打光
          </el-button>
          <span v-if="isVideo" class="muted">整段逐帧复用同一份光照场；8K 单帧约 3–4 秒</span>
        </div>

        <div v-if="samples.length" class="samples">
          <h3>
            小样（{{ samples.length }} 帧）
            <span class="run-time">
              {{ reportTime(sampleReport, samples[0]?.mtime) }}
            </span>
          </h3>
          <p v-if="sampleReport" class="timing-line mono">
            总耗时 {{ sampleReport.seconds }}s
            <span> · {{ cacheLabel(sampleReport.cache) }}</span>
            <span v-if="timingSummary(sampleReport)"> · {{ timingSummary(sampleReport) }}</span>
          </p>
          <div class="sample-strip">
            <a
              v-for="file in samples"
              :key="file.name"
              :href="file.open_url ?? ''"
              target="_blank"
              class="sample"
              :aria-label="`打开 ${file.name}`"
            >
              <img :src="file.open_url ?? ''" :alt="file.name" />
              <span class="mono">{{ file.name }}</span>
            </a>
          </div>
        </div>

        <div class="file-groups">
          <div class="file-group">
            <h3>输入</h3>
            <FileRow
              v-for="file in props.info.step_files.relight?.inputs ?? []"
              :key="file.path"
              :entry="file"
            />
            <span v-if="!(props.info.step_files.relight?.inputs ?? []).length" class="muted">
              还没有选定底版与氛围依据
            </span>
          </div>
          <div class="file-group">
            <h3>当前成片</h3>
            <FileRow
              v-for="file in props.info.step_files.relight?.outputs ?? []"
              :key="file.path"
              :entry="file"
            />
            <span v-if="!(props.info.step_files.relight?.outputs ?? []).length" class="muted">
              还没出片
            </span>
          </div>
        </div>
      </div>
    </section>

    <section class="panel">
      <div class="panel-head">
        <h2 class="panel-title">
          上次运行
          <span v-if="current" class="run-time">{{ reportTime(report, current.mtime) }}</span>
        </h2>
        <el-button link type="primary" :icon="Refresh" @click="loadReport">刷新报告</el-button>
      </div>
      <div class="panel-body">
        <div v-if="!current" class="empty">
          <el-empty description="还没有成片" :image-size="72" />
        </div>
        <template v-else>
          <div v-if="report?.align" class="report-grid mono">
            <span>运行时间</span>
            <span>
              {{ reportTime(report, current.mtime) }}
              <template v-if="report.finished_at"> → {{ formatTime(report.finished_at) }}</template>
            </span>
            <template v-if="report.cache">
              <span>缓存</span>
              <span>{{ cacheLabel(report.cache) }}</span>
            </template>
            <template v-if="report.atmo_kind">
              <span>氛围依据</span>
              <span>{{ atmoLabel(report) }}</span>
            </template>
            <span>对齐内点 / 匹配</span>
            <span>{{ report.align.inliers }} / {{ report.align.matches }}</span>
            <span>缩放 · 旋转</span>
            <span
              >{{ report.align.scale.toFixed(5) }} · {{ report.align.angle_deg.toFixed(4) }}°</span
            >
            <span>结构重合度</span>
            <span>
              {{ report.align.correlation_before.toFixed(3) }} →
              {{ report.align.correlation_after.toFixed(3) }}
            </span>
            <span>增益 均值 / p05 / p95</span>
            <span v-if="report.light">
              {{ report.light.gain_mean }} / {{ report.light.gain_p05 }} /
              {{ report.light.gain_p95 }}
            </span>
            <span v-else>逐帧光场（随氛围帧变化）</span>
            <span>可见度均值</span>
            <span>{{ report.light ? report.light.visibility_mean : '—' }}</span>
            <template v-if="report.ref_frame !== undefined">
              <span>参考帧</span>
              <span>
                {{ report.ref_frame }}
                <template v-if="report.atmo_ref_frame !== undefined">
                  → 氛围 {{ report.atmo_ref_frame }}
                </template>
              </span>
            </template>
            <template v-if="report.atmo_match">
              <span>氛围逐帧结构相关</span>
              <span>
                {{ report.atmo_match.min.toFixed(3) }} / {{ report.atmo_match.mean.toFixed(3) }}
                （最低 / 均值）
              </span>
            </template>
            <span>高光裁切</span>
            <span>{{
              report.clip ? percent(report.clip.clipped_before ?? undefined) : '未开启'
            }}</span>
            <span>耗时</span>
            <span>{{ report.seconds }}s</span>
          </div>
          <p v-if="timingSummary(report)" class="timing-line mono">
            分段耗时：{{ timingSummary(report) }}
          </p>
          <el-alert
            v-if="report?.atmo_short"
            type="warning"
            :closable="false"
            title="氛围视频比 GT 短：后段复用了氛围的最后一帧"
            class="gate-alert"
          />
          <el-alert
            v-if="reportError"
            type="warning"
            :closable="false"
            :title="reportError"
            class="gate-alert"
          />
          <p class="muted report-note">
            「当前成片」按 [relight] current 取，没写指针时取 out/ 下最新的交付物；
            想看更早的版本去素材库的文件列表。
          </p>
        </template>
      </div>
    </section>

    <section class="panel">
      <div class="panel-head">
        <h2 class="panel-title">最近任务</h2>
      </div>
      <div class="panel-body">
        <div v-if="!latest" class="muted">还没有重打光任务。</div>
        <template v-else>
          <div class="task-summary">
            <el-tag size="small" effect="light" :type="taskStatusMeta(latest.status).type">
              {{ latest.kind }} · {{ taskStatusMeta(latest.status).text }}
            </el-tag>
            <span class="mono">{{ latest.id }}</span>
            <span v-if="latest.queue_position" class="muted">
              排队第 {{ latest.queue_position }} 位
            </span>
          </div>
          <el-alert
            v-if="latest.error"
            type="error"
            :closable="false"
            :title="latest.error"
            class="gate-alert"
          />
          <pre v-if="latest.tail.length" class="task-tail">{{ latest.tail.join('\n') }}</pre>
        </template>
      </div>
    </section>
  </div>
</template>

<style scoped>
.relight-layout {
  display: grid;
  grid-template-columns: minmax(0, 1.4fr) minmax(0, 1fr);
  gap: var(--space-4);
  align-items: start;
}

.relight-layout > .panel:nth-child(3) {
  grid-column: 1 / -1;
}

.param-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: var(--space-3);
}

.param {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  font-size: var(--text-xs);
  color: var(--color-text-muted);
}

.clip-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
  margin-top: var(--space-3);
  font-size: var(--text-xs);
}

.clip-hint {
  margin: var(--space-2) 0 0;
  font-size: var(--text-xs);
}

.action-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-3);
  margin-top: var(--space-4);
  font-size: var(--text-xs);
}

.gate-alert {
  margin-bottom: var(--space-3);
}

.file-groups {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: var(--space-3);
  margin-top: var(--space-4);
}

.file-group {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.file-group h3 {
  margin: 0;
  font-size: var(--text-sm);
}

.samples {
  margin-top: var(--space-4);
}

.samples h3 {
  margin: 0 0 var(--space-2);
  font-size: var(--text-sm);
}

.run-time {
  margin-left: var(--space-2);
  font-size: var(--text-sm);
  font-weight: 400;
  color: var(--color-text-muted);
}

.timing-line {
  margin: var(--space-1) 0 var(--space-2);
  color: var(--color-text-muted);
  font-size: var(--text-xs);
}

.sample-strip {
  display: flex;
  gap: var(--space-2);
  overflow-x: auto;
  padding-bottom: var(--space-1);
}

.sample {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  width: 200px;
  flex: 0 0 auto;
  text-decoration: none;
}

.sample img {
  width: 100%;
  border-radius: var(--radius-md);
  background: var(--color-video-bg);
}

.sample span {
  color: var(--color-text-muted);
  font-size: var(--text-xs);
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

.report-note {
  margin: var(--space-3) 0 0;
  font-size: var(--text-xs);
}

@media (max-width: 1280px) {
  .relight-layout {
    grid-template-columns: minmax(0, 1fr);
  }

  .relight-layout > .panel:nth-child(3) {
    grid-column: auto;
  }
}
</style>
