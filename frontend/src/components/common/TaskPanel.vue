<script setup lang="ts">
import { ref, watch } from 'vue'
import { taskStatusMeta } from '@/constants/taskStatus'
import type { TaskView } from '@/types/task'

/**
 * 任务摘要 + 进程日志尾巴。生成页的视频与图片两条路径共用：
 * 两条路径的差别只在「最近任务」这个标题，内容与交互（技术详情折叠、
 * 失败原因、tail）不再各写一份。
 */
const props = withDefaults(
  defineProps<{
    task: TaskView | null
    title?: string
    emptyText?: string
  }>(),
  { title: '最近任务', emptyText: '这个 case 还没有任务。' },
)

const showTail = ref(false)

watch(
  () => props.task?.id,
  () => {
    showTail.value = false
  },
)
</script>

<template>
  <div class="panel">
    <div class="panel-head">
      <h2 class="panel-title">{{ title }}</h2>
      <el-tag
        v-if="task"
        size="small"
        effect="light"
        :type="taskStatusMeta(task.status).type"
      >
        {{ task.kind }} · {{ taskStatusMeta(task.status).text }}
      </el-tag>
      <span v-else class="muted">无</span>
    </div>
    <div class="panel-body">
      <div v-if="!task" class="muted task-empty">{{ emptyText }}</div>
      <template v-else>
        <div class="task-summary">
          <span>{{ task.kind }}</span>
          <span class="muted mono">{{ task.id }}</span>
          <span v-if="task.queue_position" class="muted"
            >排队第 {{ task.queue_position }} 位</span
          >
        </div>
        <el-alert
          v-if="task.reused"
          type="info"
          :closable="false"
          :title="`未提交、未花费：复用了已有轮次 ${task.reused.round}`"
          class="task-reused"
        />
        <el-alert
          v-if="task.error"
          type="error"
          :closable="false"
          :title="task.error"
          class="task-error"
        />
        <el-button
          v-if="task.tail.length"
          link
          size="small"
          @click="showTail = !showTail"
        >
          {{ showTail ? '收起技术详情' : '查看技术详情' }}
        </el-button>
        <pre v-if="showTail && task.tail.length" class="task-tail">{{
          task.tail.join('\n')
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

.task-reused {
  margin-top: var(--space-2);
}
</style>
