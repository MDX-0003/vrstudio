<script setup lang="ts">
import { ref, watch } from 'vue'
import { VideoPlay } from '@element-plus/icons-vue'
import FileRow from '@/components/common/FileRow.vue'
import { taskStatusMeta } from '@/constants/taskStatus'
import { roundResultText, roundResultType } from '@/utils/rounds'
import type { OutputFile, RoundSummary } from '@/types/case-info'
import type { TaskView } from '@/types/task'

const props = defineProps<{
  recentOutput: OutputFile | null
  adoptedRound: string
  adoptingRound: string | null
  visibleRoundRows: RoundSummary[]
  roundsCount: number
  roundsHint: string
  activeTask: TaskView | null
}>()

const emit = defineEmits<{ adopt: [round: RoundSummary] }>()
const showTaskTail = ref(false)

function isAdopted(round: RoundSummary): boolean {
  return round.round === props.adoptedRound
}

watch(
  () => props.activeTask?.id,
  () => {
    showTaskTail.value = false
  },
)

function formatRoundTime(value: string): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleString('zh-CN', { hour12: false })
}
</script>

<template>
  <div class="panel">
    <div class="panel-head">
      <h2 class="panel-title">最近成片</h2>
      <el-tag
        v-if="recentOutput"
        :type="recentOutput.partial ? 'warning' : 'success'"
        effect="light"
        size="small"
      >
        {{ recentOutput.partial ? '仅中间段' : '可用' }}
      </el-tag>
      <span v-else class="muted">无</span>
    </div>
    <div class="panel-body">
      <video
        v-if="recentOutput"
        class="output-video"
        :src="
          recentOutput.proxy_state === 'ready'
            ? recentOutput.proxy_url
            : (recentOutput.open_url ?? '')
        "
        controls
        preload="metadata"
      />
      <div v-else class="output-frame">
        <el-icon :size="34"><VideoPlay /></el-icon>
        <span>还没有合并成片</span>
      </div>
      <div v-if="recentOutput" class="output-meta mono">{{ recentOutput.file }}</div>
      <div class="adopt-hint mono">
        {{ adoptedRound ? `当前采用轮次：${adoptedRound}` : '尚未采用任何轮次' }}
      </div>
    </div>
  </div>

  <div class="panel">
    <div class="panel-head">
      <h2 class="panel-title">生成记录</h2>
      <span class="muted">{{ visibleRoundRows.length }} 条记录</span>
    </div>
    <div class="panel-body candidate-list">
      <div v-if="!visibleRoundRows.length" class="candidate-empty">
        <el-empty description="还没有可用成片" :image-size="72" />
        <p v-if="roundsCount" class="muted rounds-hint">预演轮次已隐藏；生成出片后会显示在这里。</p>
        <p v-else-if="roundsHint" class="muted rounds-hint">{{ roundsHint }}</p>
      </div>
      <div v-for="round in visibleRoundRows" :key="round.round" class="candidate">
        <div class="candidate-head">
          <div class="candidate-title">
            <span class="round-name">{{ formatRoundTime(round.updated) }}</span>
            <span class="round-id mono">{{ round.round }}</span>
          </div>
          <el-tag v-if="isAdopted(round)" type="success" size="small" effect="light">
            已采用
          </el-tag>
          <el-button
            v-else-if="round.result === 'yes'"
            type="primary"
            plain
            size="small"
            :loading="adoptingRound === round.round"
            :disabled="adoptingRound !== null"
            @click="emit('adopt', round)"
          >
            选用
          </el-button>
        </div>
        <div class="round-tags">
          <el-tag size="small" effect="light" :type="roundResultType(round)">
            {{ roundResultText(round) }}
          </el-tag>
        </div>
        <div v-if="round.files" class="round-files">
          <FileRow :entry="round.files.result" />
          <FileRow :entry="round.files.run_json" />
        </div>
      </div>
    </div>
  </div>

  <div class="panel">
    <div class="panel-head">
      <h2 class="panel-title">最近任务</h2>
      <el-tag
        v-if="activeTask"
        size="small"
        effect="light"
        :type="taskStatusMeta(activeTask.status).type"
      >
        {{ activeTask.kind }} · {{ taskStatusMeta(activeTask.status).text }}
      </el-tag>
      <span v-else class="muted">无</span>
    </div>
    <div class="panel-body">
      <div v-if="!activeTask" class="muted task-empty">这个 case 还没有任务。</div>
      <template v-else>
        <div class="task-summary">
          <span>{{ activeTask.kind }}</span>
          <span class="muted mono">{{ activeTask.id }}</span>
          <span v-if="activeTask.queue_position" class="muted"
            >排队第 {{ activeTask.queue_position }} 位</span
          >
        </div>
        <el-alert
          v-if="activeTask.error"
          type="error"
          :closable="false"
          :title="activeTask.error"
          class="task-error"
        />
        <el-button
          v-if="activeTask.tail.length"
          link
          size="small"
          @click="showTaskTail = !showTaskTail"
        >
          {{ showTaskTail ? '收起技术详情' : '查看技术详情' }}
        </el-button>
        <pre v-if="showTaskTail && activeTask.tail.length" class="task-tail">{{
          activeTask.tail.join('\n')
        }}</pre>
      </template>
    </div>
  </div>
</template>

<style scoped>
.task-empty {
  padding: var(--space-2) 0;
}

.task-error {
  margin-top: var(--space-2);
}

.output-frame {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  width: 100%;
  max-width: 560px;
  aspect-ratio: 16 / 9;
  border-radius: var(--radius-md);
  background:
    radial-gradient(circle at 30% 25%, rgba(37, 99, 235, 0.35), transparent 55%),
    linear-gradient(135deg, var(--color-video-bg), #334155);
  color: rgba(255, 255, 255, 0.86);
  font-size: var(--text-xs);
}

.output-video {
  display: block;
  width: 100%;
  max-width: 560px;
  aspect-ratio: 16 / 9;
  border-radius: var(--radius-md);
  background: var(--color-video-bg);
}

.output-meta {
  margin-top: var(--space-2);
  color: var(--color-text-muted);
  font-size: var(--text-xs);
  word-break: break-all;
}

.adopt-hint {
  max-width: 560px;
  margin-top: var(--space-2);
  color: var(--color-text-muted);
  font-size: var(--text-xs);
  word-break: break-all;
}

.candidate-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.candidate {
  padding: var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}

.candidate-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  font-size: var(--text-sm);
  font-weight: 600;
}

.candidate-title {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.round-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.round-id {
  color: var(--color-text-muted);
  font-size: var(--text-xs);
  font-weight: 400;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.round-tags {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
  margin-top: var(--space-2);
}

.round-files {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  margin-top: var(--space-2);
}

.candidate-empty {
  padding: var(--space-2);
}

.rounds-hint {
  margin: var(--space-2) 0 0;
  font-size: var(--text-xs);
  word-break: break-all;
}
</style>
