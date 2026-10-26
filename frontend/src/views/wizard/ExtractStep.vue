<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Download, Refresh, VideoPlay } from '@element-plus/icons-vue'
import { fetchCase, runOperation } from '@/api/case'
import { useCaseTasks } from '@/composables/useCaseTasks'
import { formatSize } from '@/utils/format'
import { taskStatusMeta } from '@/constants/taskStatus'
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

function fileUrl(file: FileInfo): string {
  if (!current.value) return ''
  return `/media/extract/${encodeURIComponent(props.caseName)}/${encodeURIComponent(current.value)}/${encodeURIComponent(file.name)}`
}

function isVideo(file: FileInfo): boolean {
  return file.name.endsWith('.mp4')
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
  <div class="step-layout">
    <section class="panel">
      <div class="panel-head">
        <h2 class="panel-title">抽取</h2>
        <el-button
          type="primary"
          size="small"
          :icon="VideoPlay"
          :loading="running"
          :disabled="Boolean(pickGate && !pickGate.ok)"
          @click="runExtract"
        >
          {{ current ? '重新抽取' : '运行抽取' }}
        </el-button>
      </div>
      <div class="panel-body">
        <div class="summary-row">
          <span
            >当前版本：<b class="mono">{{ current || '还没有' }}</b></span
          >
          <span>选区：{{ pickGate?.ok ? '已保存' : '未完成' }}</span>
          <span>抽取：{{ extractGate?.ok ? '已完成' : '未完成' }}</span>
        </div>
        <el-alert
          v-if="pickGate && !pickGate.ok"
          type="warning"
          :closable="false"
          :title="pickGate.reason || '先完成选区'"
          class="gate-alert"
        />
        <el-alert
          v-else-if="extractGate && !extractGate.ok"
          type="info"
          :closable="false"
          :title="extractGate.reason || '点击运行抽取'"
          class="gate-alert"
        />
        <p class="muted summary-note">
          一次抽取生成 clip.mp4、clip_mask.png、clip.json 与首尾原始帧；版本目录保留旧结果，不覆盖。
        </p>
      </div>
    </section>

    <section class="panel">
      <div class="panel-head">
        <h2 class="panel-title">输出文件</h2>
        <span class="muted">{{ outputs.length }} 个</span>
      </div>
      <div class="panel-body file-list">
        <div v-if="!outputs.length" class="empty">
          <el-empty description="还没有抽取结果" :image-size="72" />
        </div>
        <div
          v-for="file in outputs"
          :key="file.path"
          class="file-row"
          :class="{ missing: !file.exists }"
        >
          <div class="file-main">
            <div class="file-name mono">{{ file.name }}</div>
            <div class="file-meta">
              <span>{{ file.rel || file.path }}</span>
              <span>{{ formatSize(file.size) }}</span>
              <span v-if="!file.exists" class="bad">文件缺失</span>
            </div>
          </div>
          <el-link
            v-if="file.exists"
            :href="fileUrl(file)"
            target="_blank"
            type="primary"
            underline="never"
          >
            <el-icon><Download /></el-icon>
            {{ isVideo(file) ? '播放' : '打开' }}
          </el-link>
        </div>
      </div>
    </section>

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
      </div>
    </section>

    <section class="panel">
      <div class="panel-head">
        <h2 class="panel-title">抽取任务</h2>
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
            class="gate-alert"
          />
          <pre v-if="latest.tail.length" class="task-tail">{{ latest.tail.join('\n') }}</pre>
          <p class="muted task-note">本页只显示最近一次抽取任务；全部任务在任务中心。</p>
        </template>
      </div>
    </section>
  </div>
</template>

<style scoped>
.step-layout {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(0, 1fr);
  gap: var(--space-4);
  align-items: start;
}

.summary-row {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-4);
  font-size: var(--text-sm);
}

.summary-note {
  margin: var(--space-3) 0 0;
  font-size: var(--text-xs);
}

.gate-alert {
  margin-top: var(--space-2);
}

.file-list,
.version-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.file-row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-2) var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}

.file-row.missing {
  opacity: 0.55;
}

.file-main {
  flex: 1;
  min-width: 0;
}

.file-name {
  font-weight: 600;
}

.file-meta {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  color: var(--color-text-muted);
  font-size: var(--text-xs);
  word-break: break-all;
}

.file-meta .bad {
  color: var(--color-danger);
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

.task-note {
  margin: var(--space-2) 0 0;
  font-size: var(--text-xs);
}

@media (max-width: 1280px) {
  .step-layout {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
