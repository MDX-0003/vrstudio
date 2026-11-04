<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Download, Refresh, VideoPlay } from '@element-plus/icons-vue'
import { fetchCase, runOperation } from '@/api/case'
import MergeInputControls from '@/components/assets/MergeInputControls.vue'
import FileRow from '@/components/common/FileRow.vue'
import { useCaseTasks } from '@/composables/useCaseTasks'
import { gateBlocked } from '@/utils/gates'
import { taskStatusMeta } from '@/constants/taskStatus'
import type { FullRedrawImageReport, CaseInfo, PatchImageReport } from '@/types/case-info'

const props = defineProps<{ caseName: string; info: CaseInfo }>()
const emit = defineEmits<{ updated: [data: CaseInfo] }>()

const { latest, start } = useCaseTasks(
  computed(() => props.caseName),
  ['merge', 'assemble', 'pad', 'recompose'],
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
const mergeGate = computed(() => props.info.gates.merge)
const padGate = computed(() => props.info.gates.pad)
const recomposeGate = computed(() => props.info.gates.recompose)
const patchFiles = computed(() => props.info.step_files.merge)
const padFiles = computed(() => props.info.step_files.pad)
const recomposeFiles = computed(() => props.info.step_files.recompose)
const outputs = computed(() => props.info.outputs)
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

async function runOp(op: 'merge' | 'pad' | 'recompose'): Promise<void> {
  if (running.value) return
  running.value = op
  startedAt = Date.now()
  try {
    const payload = op === 'merge' && !isImageFlow.value ? { allow_crop: allowCrop.value } : {}
    await runOperation(props.caseName, op, payload)
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
          {{ isImageFlow ? `${imageMergeLabel}成片` : isPatch ? '合并与时间拼接' : '扩充与裁回' }}
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
            <span v-if="mergeGate?.ok" class="muted"> 已有成片；再跑一次会覆盖同名文件 </span>
          </div>
          <MergeInputControls
            v-if="isFullRedrawImg"
            :case-name="caseName"
            :info="info"
            kind="image"
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
          <el-alert
            v-if="mergeGate && !mergeGate.ready"
            type="warning"
            :closable="false"
            :title="mergeGate.reason || '先采用一次生成结果'"
            class="gate-alert"
          />
          <div class="action-row">
            <el-checkbox v-model="allowCrop">允许裁切</el-checkbox>
            <el-button
              type="primary"
              size="small"
              :icon="VideoPlay"
              :loading="running === 'merge'"
              :disabled="Boolean(mergeGate && !mergeGate.ready)"
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
              <h3>合并输入</h3>
              <FileRow v-for="file in patchFiles?.inputs ?? []" :key="file.path" :entry="file" />
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
            <span class="node">扩充画面</span>
            <span class="muted">→</span>
            <span class="node">生成</span>
            <span class="muted">→</span>
            <span class="node">裁回原尺寸</span>
          </div>
          <div class="action-row">
            <el-button
              size="small"
              :loading="running === 'pad'"
              :disabled="gateBlocked(padGate)"
              @click="runOp('pad')"
            >
              扩充画面
            </el-button>
            <el-button
              type="primary"
              size="small"
              :loading="running === 'recompose'"
              :disabled="gateBlocked(recomposeGate)"
              @click="runOp('recompose')"
            >
              裁回原尺寸
            </el-button>
          </div>
          <el-alert
            v-if="padGate && !padGate.ok"
            type="info"
            :closable="false"
            :title="padGate.reason || '还没有扩充过画面'"
            class="gate-alert"
          />
          <el-alert
            v-if="recomposeGate && !recomposeGate.ready"
            type="warning"
            :closable="false"
            :title="recomposeGate.reason || '先采用一次生成结果'"
            class="gate-alert"
          />
          <MergeInputControls
            :case-name="caseName"
            :info="info"
            :kind="mergeInputKind"
            @updated="emit('updated', $event)"
          />
          <div class="file-groups">
            <div class="file-group">
              <h3>扩充画面输出</h3>
              <FileRow v-for="file in padFiles?.outputs ?? []" :key="file.path" :entry="file" />
              <span v-if="!(padFiles?.outputs ?? []).length" class="muted">还没有输出</span>
            </div>
            <div class="file-group">
              <h3>裁回原尺寸输出</h3>
              <FileRow
                v-for="file in recomposeFiles?.outputs ?? []"
                :key="file.path"
                :entry="file"
              />
              <span v-if="!(recomposeFiles?.outputs ?? []).length" class="muted">还没有输出</span>
            </div>
          </div>
        </template>
      </div>
    </section>

    <section class="panel">
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

.file-group h3 {
  margin: 0;
  font-size: var(--text-sm);
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
