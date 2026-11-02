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
  ref_frame?: number
  ref_matched?: number
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

const { latest, start } = useCaseTasks(computed(() => props.caseName), ['relight'])
const running = ref('')
const report = ref<RelightReport | null>(null)
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
  scene: 'night' as 'night' | 'day',
  strength: 1,
  local_shading: 0.35,
  highlight_clip: false,
  clip_knee: 0.72,
  clip_max: 0.98,
  ref_frame: '' as string,
  crf: '' as string,
  sample: 5,
})

/** 预设只给默认值：与 CLI 的 SCENE_PRESETS 同源，改那里这里要跟着改。 */
const PRESETS = { night: { strength: 1, local_shading: 0.35 }, day: { strength: 0.8, local_shading: 0.2 } }

function applyDraft(): void {
  const d = draft.value
  form.value = {
    scene: d.scene === 'day' ? 'day' : 'night',
    strength: d.strength ?? PRESETS[d.scene === 'day' ? 'day' : 'night'].strength,
    local_shading: d.local_shading ?? PRESETS[d.scene === 'day' ? 'day' : 'night'].local_shading,
    highlight_clip: d.highlight_clip ?? false,
    clip_knee: d.clip_knee ?? 0.72,
    clip_max: d.clip_max ?? 0.98,
    ref_frame: d.ref_frame === undefined ? '' : String(d.ref_frame),
    crf: '',
    sample: 5,
  }
}

function onSceneChange(value: 'night' | 'day'): void {
  // 切场景时把两个强度重置成该场景的预设，避免「日光 + 夜戏 1.0」这种组合没人发现。
  form.value.strength = PRESETS[value].strength
  form.value.local_shading = PRESETS[value].local_shading
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

onMounted(() => {
  applyDraft()
  void start()
  void loadReport()
})

watch(draft, applyDraft)
watch(current, () => void loadReport())

function runPayload() {
  const body: Record<string, string | number | boolean> = {
    scene: form.value.scene,
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
          :title="props.info.gates.assets?.reason || '先在素材步选定底版与氛围图'"
          class="gate-alert"
        />

        <div class="param-grid">
          <label class="param">
            <span>场景预设</span>
            <el-select v-model="form.scene" size="small" @change="onSceneChange">
              <el-option label="夜戏 1.0 / 0.35" value="night" />
              <el-option label="日光 0.8 / 0.2" value="day" />
            </el-select>
          </label>
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
            <el-input-number v-model="form.clip_knee" :min="0.01" :max="0.99" :step="0.01" size="small" />
            <span class="muted">上限</span>
            <el-input-number v-model="form.clip_max" :min="0.02" :max="1" :step="0.01" size="small" />
          </template>
        </div>

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
          <h3>小样（{{ samples.length }} 帧）</h3>
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
              还没有选定底版与氛围图
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
        <h2 class="panel-title">上次运行</h2>
        <el-button link type="primary" :icon="Refresh" @click="loadReport">刷新报告</el-button>
      </div>
      <div class="panel-body">
        <div v-if="!current" class="empty">
          <el-empty description="还没有成片" :image-size="72" />
        </div>
        <template v-else>
          <div v-if="report?.align" class="report-grid mono">
            <span>对齐内点 / 匹配</span>
            <span>{{ report.align.inliers }} / {{ report.align.matches }}</span>
            <span>缩放 · 旋转</span>
            <span>{{ report.align.scale.toFixed(5) }} · {{ report.align.angle_deg.toFixed(4) }}°</span>
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
            <span>可见度均值</span>
            <span v-if="report.light">{{ report.light.visibility_mean }}</span>
            <template v-if="report.ref_frame !== undefined">
              <span>参考帧</span>
              <span>{{ report.ref_frame }}</span>
            </template>
            <span>高光裁切</span>
            <span>{{ report.clip ? percent(report.clip.clipped_before ?? undefined) : '未开启' }}</span>
            <span>耗时</span>
            <span>{{ report.seconds }}s</span>
          </div>
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
