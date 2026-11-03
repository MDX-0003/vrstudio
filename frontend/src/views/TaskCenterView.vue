<script setup lang="ts">
import { onMounted, onUnmounted, ref, watch } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import PageHeader from '@/components/common/PageHeader.vue'
import { fetchTasks } from '@/api/tasks'
import { taskStatusMeta as statusOf } from '@/constants/taskStatus'
import type { TaskView } from '@/types/task'

const tasks = ref<TaskView[]>([])
const loading = ref(false)
const error = ref('')
const scope = ref<'all' | 'mine'>('all')
const drawerOpen = ref(false)
const selected = ref<TaskView | null>(null)
let timer: ReturnType<typeof setInterval> | null = null
let inFlight = false

function positionText(task: TaskView): string {
  if (task.status === 'running') return '运行中'
  return task.queue_position ? `第 ${task.queue_position} 位` : '—'
}

function formatTime(value: string | null): string {
  if (!value) return '—'
  return value.replace('T', ' ').slice(0, 19)
}

function durationText(task: TaskView): string {
  if (!task.started_at) return '—'
  const start = new Date(task.started_at).getTime()
  const end = task.finished_at ? new Date(task.finished_at).getTime() : Date.now()
  const seconds = Math.max(0, Math.round((end - start) / 1000))
  if (seconds < 60) return `${seconds} 秒`
  return `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`
}

function quotaText(task: TaskView): string {
  if (!task.session_id) return '个人额度'
  return `项目 ${task.project_id ?? '—'} · 第 ${task.episode_id ?? '—'} 集 · 会话 ${task.session_id}`
}

async function loadTasks(): Promise<void> {
  if (inFlight || document.hidden) return
  inFlight = true
  loading.value = tasks.value.length === 0
  error.value = ''
  try {
    const data = await fetchTasks(scope.value)
    tasks.value = data.tasks
    if (selected.value) {
      selected.value = data.tasks.find((task) => task.id === selected.value?.id) ?? selected.value
    }
  } catch (err) {
    error.value = err instanceof Error ? err.message : '读取任务失败'
  } finally {
    loading.value = false
    inFlight = false
  }
}

function openTask(task: TaskView): void {
  selected.value = task
  drawerOpen.value = true
}

function startPolling(): void {
  if (timer) clearInterval(timer)
  timer = setInterval(() => {
    void loadTasks()
  }, 2000)
}

function stopPolling(): void {
  if (timer) {
    clearInterval(timer)
    timer = null
  }
}

onMounted(() => {
  void loadTasks()
  startPolling()
})

onUnmounted(stopPolling)

watch(scope, () => {
  void loadTasks()
})
</script>

<template>
  <div class="page">
    <PageHeader
      title="任务中心"
      subtitle="读取真实 /api/tasks；Phase 4 增加按人筛选与扣费归属，Phase 5 增加完整日志下载。"
    >
      <el-select v-model="scope" style="width: 130px">
        <el-option label="全部任务" value="all" />
        <el-option label="我的任务" value="mine" />
      </el-select>
      <el-button :icon="Refresh" :loading="loading" @click="loadTasks">刷新</el-button>
    </PageHeader>

    <div v-if="error" class="load-error">
      <span>{{ error }}</span>
      <el-button link type="primary" @click="loadTasks">重试</el-button>
    </div>

    <div class="panel">
      <el-table v-loading="loading" :data="tasks" row-key="id" @row-click="openTask">
        <el-table-column label="状态" width="150">
          <template #default="{ row }">
            <el-tag :type="statusOf(row.status).type" effect="light" size="small">
              {{ statusOf(row.status).text }}
            </el-tag>
            <el-tag
              v-if="row.reused"
              type="info"
              effect="plain"
              size="small"
              class="reused-tag"
              title="这一轮没有发请求、没有扣额度"
            >
              未花费
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="任务" min-width="160">
          <template #default="{ row }">
            <div class="task-kind">{{ row.kind }}</div>
            <div class="task-id mono">{{ row.id }}</div>
          </template>
        </el-table-column>
        <el-table-column prop="case" label="Case" width="140" />
        <el-table-column label="负责人" width="110">
          <template #default="{ row }">{{ row.owner_name || '未归属' }}</template>
        </el-table-column>
        <el-table-column label="扣费归属" min-width="220">
          <template #default="{ row }">
            <span class="quota">{{ quotaText(row) }}</span>
          </template>
        </el-table-column>
        <el-table-column label="排队位置" width="110">
          <template #default="{ row }">{{ positionText(row) }}</template>
        </el-table-column>
        <el-table-column label="提交时间" width="180">
          <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column label="耗时" width="120">
          <template #default="{ row }">{{ durationText(row) }}</template>
        </el-table-column>
        <el-table-column label="" width="90" align="right">
          <template #default="{ row }">
            <el-button link type="primary" @click.stop="openTask(row)">查看</el-button>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty description="还没有任务" />
        </template>
      </el-table>
    </div>

    <el-drawer
      v-model="drawerOpen"
      :title="selected ? `任务 ${selected.kind} · ${selected.case}` : '任务详情'"
      size="620px"
    >
      <template v-if="selected">
        <el-descriptions :column="2" border>
          <el-descriptions-item label="状态">
            {{ statusOf(selected.status).text }}
          </el-descriptions-item>
          <el-descriptions-item label="任务 ID">
            <span class="mono">{{ selected.id }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="负责人">
            {{ selected.owner_name || '未归属' }}
          </el-descriptions-item>
          <el-descriptions-item label="扣费归属" :span="2">
            {{ quotaText(selected) }}
          </el-descriptions-item>
          <el-descriptions-item label="排队位置">
            {{ positionText(selected) }}
          </el-descriptions-item>
          <el-descriptions-item label="提交时间">
            {{ formatTime(selected.created_at) }}
          </el-descriptions-item>
          <el-descriptions-item label="开始时间">
            {{ formatTime(selected.started_at) }}
          </el-descriptions-item>
          <el-descriptions-item label="结束时间">
            {{ formatTime(selected.finished_at) }}
          </el-descriptions-item>
          <el-descriptions-item label="请求 ID">
            <span class="mono">{{ selected.request_id || '—' }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="日志路径" :span="2">
            <span class="mono path">{{ selected.log_path || '—' }}</span>
          </el-descriptions-item>
        </el-descriptions>

        <el-alert
          v-if="selected.error"
          type="error"
          :closable="false"
          :title="selected.error"
          class="task-error"
        />

        <h3 class="log-title">日志尾部</h3>
        <pre class="log-tail">{{
          selected.tail.length ? selected.tail.join('\n') : '没有日志输出'
        }}</pre>
        <p class="log-note">
          <el-link
            :href="`/api/tasks/${selected.id}/log`"
            target="_blank"
            type="primary"
            underline="never"
          >
            打开完整日志
          </el-link>
        </p>
      </template>
    </el-drawer>
  </div>
</template>

<style scoped>
.reused-tag {
  margin-left: var(--space-1);
}

.task-kind {
  font-weight: 600;
}

.quota {
  color: var(--color-text-muted);
  font-size: var(--text-xs);
}

.task-id {
  color: var(--color-text-faint);
  font-size: var(--text-xs);
}

.task-error {
  margin-top: var(--space-4);
}

.log-title {
  margin: var(--space-4) 0 var(--space-2);
  font-size: var(--text-base);
}

.log-tail {
  max-height: 320px;
  margin: 0;
  padding: var(--space-3);
  overflow: auto;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-video-bg);
  color: var(--color-video-text);
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  line-height: 1.6;
  white-space: pre-wrap;
}

.log-note {
  font-size: var(--text-xs);
}

.path {
  word-break: break-all;
}

:deep(.el-table__row) {
  cursor: pointer;
}
</style>
