<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Refresh, VideoPlay } from '@element-plus/icons-vue'
import { fetchCase, runOperation } from '@/api/case'
import FileRow from '@/components/common/FileRow.vue'
import { useCaseTasks } from '@/composables/useCaseTasks'
import { taskStatusMeta } from '@/constants/taskStatus'
import { formatSize } from '@/utils/format'
import type { CaseInfo, FileInfo } from '@/types/case-info'

const props = defineProps<{ caseName: string; info: CaseInfo }>()
const emit = defineEmits<{ updated: [data: CaseInfo] }>()

const { latest, start } = useCaseTasks(
  computed(() => props.caseName),
  ['extract'],
)
// 进行中从任务状态推导，而不是“本页是否点过按钮”：中途刷新/切页回来后
// 按钮加载态与完成刷新都还能工作（2026-10-26 用户反馈的陈旧“文件缺失”）。
const submitting = ref(false)
const running = computed(() => {
  const task = latest.value
  return (
    submitting.value ||
    Boolean(
      task && task.kind === 'extract' && (task.status === 'queued' || task.status === 'running'),
    )
  )
})
const mountedAt = Date.now()

const pickGate = computed(() => props.info.gates.pick)
const extractGate = computed(() => props.info.gates.extract)
const outputs = computed<FileInfo[]>(() => props.info.step_files.extract?.outputs ?? [])
const current = computed(() => props.info.extract_current)
/** 图片流程的抽取产物是 PNG 与 JSON：没有 clip.mp4 与首尾帧卡片。 */
const isImageFlow = computed(
  () => props.info.flow === 'patchimg' || props.info.flow === 'fullRedrawImg',
)

function output(name: string): FileInfo | undefined {
  return outputs.value.find((file) => file.name === name)
}

const clip = computed(() => output('clip.mp4'))
const mask = computed(() => output('clip_mask.png'))
const sidecar = computed(() => output('clip.json'))
const rawFirstFile = computed(() => output('first_frame_raw.png'))
const rawLastFile = computed(() => output('last_frame_raw.png'))

function sizeText(file: FileInfo | undefined): string {
  if (!file || !file.exists) return '文件缺失'
  return formatSize(file.size)
}

async function runExtract(): Promise<void> {
  if (running.value) return
  if (pickGate.value && !pickGate.value.ok) {
    ElMessage.warning(pickGate.value.reason || '先完成选区')
    return
  }
  submitting.value = true
  try {
    await runOperation(props.caseName, 'extract')
    start()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '启动抽取失败')
  } finally {
    submitting.value = false
  }
}

// 挂载即开始轮询：任务从哪里发起（本页、别的页、重启前的旧任务）都看得到，
// 完成后的自动刷新才有数据源。
onMounted(() => start())

watch(latest, async (task) => {
  if (!task || task.kind !== 'extract') return
  const created = new Date(task.created_at).getTime()
  // 挂载之前就完成的旧任务不触发刷新：挂载时拿到的 info 已是最新。
  if (Number.isNaN(created) || created < mountedAt - 5000) return
  if (task.status === 'done') {
    const data = await fetchCase(props.caseName)
    emit('updated', data)
    ElMessage.success('抽取完成')
  } else if (task.status === 'failed') {
    ElMessage.error(task.error || '抽取失败，原因见下方任务日志')
  }
})
</script>

<template>
  <div class="ex-page">
    <section class="panel">
      <div class="panel-body">
        <div class="ex-bar">
          <div>
            <div class="ex-label">当前版本</div>
            <div class="ex-value mono">{{ current || '还没有' }}</div>
          </div>
          <div class="ex-divider" />
          <span class="ex-gate" :class="pickGate?.ok ? 'ok' : 'todo'">
            {{ pickGate?.ok ? '✓ 选区已保存' : '选区未完成' }}
          </span>
          <span class="ex-gate" :class="extractGate?.ok ? 'ok' : 'todo'">
            {{ extractGate?.ok ? '✓ 抽取已完成' : '抽取未完成' }}
          </span>
          <span class="spacer" />
          <el-button
            type="primary"
            size="large"
            :icon="VideoPlay"
            :loading="running"
            :disabled="Boolean(pickGate && !pickGate.ok)"
            @click="runExtract"
          >
            {{ current ? '重新抽取' : '运行抽取' }}
          </el-button>
        </div>
        <el-alert
          v-if="pickGate && !pickGate.ok"
          type="warning"
          :closable="false"
          :title="pickGate.reason || '先完成选区'"
          class="ex-alert"
        />
      </div>
    </section>

    <section class="panel">
      <div class="panel-head">
        <h2 class="panel-title">本次产物</h2>
        <span class="muted">版本目录保留旧结果，不覆盖</span>
      </div>
      <div class="panel-body">
        <div v-if="outputs.length" class="ex-products">
          <template v-if="isImageFlow">
            <FileRow v-for="file in outputs" :key="file.path" :entry="file" />
          </template>
          <template v-else>
          <div class="ex-prod">
            <a
              v-if="clip?.exists && clip.open_url"
              :href="clip.open_url"
              target="_blank"
              class="ex-prod-media ex-prod-video"
              :aria-label="`播放 ${clip.name}`"
            >
              <img
                v-if="rawFirstFile?.exists && rawFirstFile.open_url"
                :src="rawFirstFile.open_url"
                alt="clip.mp4 首帧"
                class="ex-poster"
              />
              <span class="ex-scrim" />
              <span class="ex-play">▶</span>
              <span class="ex-prod-hint">播放</span>
            </a>
            <div v-else class="ex-prod-media ex-prod-empty">文件缺失</div>
            <div class="ex-prod-body">
              <div class="ex-prod-name mono">clip.mp4</div>
              <div class="ex-prod-meta">{{ sizeText(clip) }}</div>
            </div>
          </div>

          <div class="ex-prod">
            <a
              v-if="mask?.exists && mask.open_url"
              :href="mask.open_url"
              target="_blank"
              class="ex-prod-media"
              :aria-label="`打开 ${mask.name}`"
            >
              <img :src="mask.open_url" alt="clip_mask.png" />
            </a>
            <div v-else class="ex-prod-media ex-prod-empty">文件缺失</div>
            <div class="ex-prod-body">
              <div class="ex-prod-name mono">clip_mask.png</div>
              <div class="ex-prod-meta">区域遮罩 · {{ sizeText(mask) }}</div>
            </div>
          </div>

          <div class="ex-prod">
            <a
              v-if="sidecar?.exists && sidecar.open_url"
              :href="sidecar.open_url"
              target="_blank"
              class="ex-prod-media ex-prod-json"
              :aria-label="`打开 ${sidecar.name}`"
            >
              { case, viewport, frames, … }
            </a>
            <div v-else class="ex-prod-media ex-prod-empty">文件缺失</div>
            <div class="ex-prod-body">
              <div class="ex-prod-name mono">clip.json</div>
              <div class="ex-prod-meta">片段台账 · {{ sizeText(sidecar) }}</div>
            </div>
          </div>

          <div class="ex-prod">
            <div v-if="rawFirstFile?.exists && rawLastFile?.exists" class="ex-pair">
              <a
                v-if="rawFirstFile.open_url"
                :href="rawFirstFile.open_url"
                target="_blank"
                :aria-label="`打开 ${rawFirstFile.name}`"
              >
                <img :src="rawFirstFile.open_url" alt="首帧原始图" />
              </a>
              <a
                v-if="rawLastFile.open_url"
                :href="rawLastFile.open_url"
                target="_blank"
                :aria-label="`打开 ${rawLastFile.name}`"
              >
                <img :src="rawLastFile.open_url" alt="尾帧原始图" />
              </a>
            </div>
            <div v-else class="ex-prod-media ex-prod-empty">文件缺失</div>
            <div class="ex-prod-body">
              <div class="ex-prod-name mono">首尾原始帧</div>
              <div class="ex-prod-meta">png × 2 · 工作台输入底图</div>
            </div>
          </div>
          </template>
        </div>
        <p v-if="!outputs.length" class="muted ex-empty-note">
          {{
            isImageFlow
              ? '还没有抽取结果。运行抽取会生成 PNG 与清单文件。'
              : '还没有抽取结果。运行抽取会生成 clip.mp4、clip_mask.png、clip.json 与首尾原始帧。'
          }}
        </p>
      </div>
    </section>

    <div class="ex-cols">
      <section class="panel">
        <div class="panel-head">
          <h2 class="panel-title">抽取版本</h2>
          <span class="muted">{{ info.extracts.length }} 个版本</span>
        </div>
        <div class="panel-body version-list">
          <div v-if="!info.extracts.length" class="muted">还没有可用版本。</div>
          <div v-for="item in info.extracts" :key="item.version" class="version-row">
            <span class="mono">{{ item.version }}</span>
            <el-tag v-if="item.version === current" size="small" type="success" effect="light"
              >当前</el-tag
            >
            <span class="muted">{{ item.has_result ? '有生成结果' : '未生成' }}</span>
          </div>
          <p class="muted ex-note">切换版本会同时切换首尾原始帧与后续步骤的输入；旧版本不清除。</p>
        </div>
      </section>

      <section class="panel">
        <div class="panel-head">
          <h2 class="panel-title">最近一次抽取任务</h2>
          <el-button link type="primary" :icon="Refresh" @click="start()">刷新</el-button>
        </div>
        <div class="panel-body">
          <div v-if="!latest" class="muted">还没有抽取任务。</div>
          <template v-else>
            <div class="task-summary">
              <el-tag size="small" effect="light" :type="taskStatusMeta(latest.status).type">
                {{ taskStatusMeta(latest.status).text }}
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
              class="ex-alert"
            />
            <pre v-if="latest.tail.length" class="task-tail">{{ latest.tail.join('\n') }}</pre>
            <p class="muted ex-note">全部任务在任务中心。</p>
          </template>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.ex-page {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.ex-bar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-3);
}

.ex-label {
  color: var(--color-text-muted);
  font-size: var(--text-xs);
}

.ex-value {
  font-size: var(--text-lg);
  font-weight: 600;
}

.ex-divider {
  width: 1px;
  height: 32px;
  background: var(--color-border);
}

.ex-gate {
  display: inline-flex;
  align-items: center;
  height: 26px;
  padding: 0 10px;
  border-radius: 999px;
  font-size: var(--text-xs);
}

.ex-gate.ok {
  background: var(--color-success-soft);
  color: var(--color-success);
}

.ex-gate.todo {
  background: var(--color-warning-soft);
  color: var(--color-warning);
}

.ex-alert {
  margin-top: var(--space-3);
}

.ex-products {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--space-3);
}

.ex-prod {
  display: flex;
  flex-direction: column;
  overflow: hidden;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}

.ex-prod-media {
  position: relative;
  display: grid;
  place-items: center;
  overflow: hidden;
  aspect-ratio: 16 / 9;
  background: var(--color-surface-muted);
  color: var(--color-text-muted);
  text-decoration: none;
}

.ex-prod-media img {
  width: 100%;
  height: 100%;
  object-fit: contain;
}

.ex-prod-video {
  gap: var(--space-1);
  background: linear-gradient(135deg, #1e3a8a, #3b82f6);
  color: #e2e8f0;
}

.ex-poster {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.ex-scrim {
  position: absolute;
  inset: 0;
  background: rgba(15, 23, 42, 0.35);
}

.ex-play {
  position: relative;
  z-index: 1;
  display: grid;
  place-items: center;
  width: 40px;
  height: 40px;
  border-radius: 50%;
  background: rgba(15, 23, 42, 0.45);
  font-size: 14px;
}

.ex-prod-hint {
  position: relative;
  z-index: 1;
  font-size: var(--text-xs);
}

.ex-prod-json {
  font-family: var(--font-mono);
  font-size: var(--text-xs);
}

.ex-prod-empty {
  border-bottom: 1px dashed var(--color-border-strong);
  color: var(--color-text-faint);
  font-size: var(--text-xs);
}

.ex-pair {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 2px;
  aspect-ratio: 16 / 9;
  overflow: hidden;
  background: var(--color-video-bg);
}

.ex-pair img {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.ex-prod-body {
  padding: var(--space-2) var(--space-3);
}

.ex-prod-name {
  font-size: var(--text-xs);
  font-weight: 600;
}

.ex-prod-meta {
  overflow: hidden;
  color: var(--color-text-muted);
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ex-cols {
  display: grid;
  grid-template-columns: minmax(0, 1.2fr) minmax(0, 1fr);
  gap: var(--space-4);
  align-items: start;
}

.version-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.version-row {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-1) 0;
  border-bottom: 1px solid var(--color-border);
  font-size: var(--text-sm);
}

.version-row:last-child {
  border-bottom: 0;
}

.ex-note {
  margin: var(--space-2) 0 0;
  font-size: var(--text-xs);
}

.ex-empty-note {
  margin: 0;
  font-size: var(--text-xs);
}

@media (max-width: 1280px) {
  .ex-products {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .ex-cols {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
