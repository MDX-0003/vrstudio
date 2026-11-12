<script setup lang="ts">
import { Refresh } from '@element-plus/icons-vue'
import type { EpisodeOption, ProjectOption, SessionOption } from '@/types/session'

defineProps<{
  projects: ProjectOption[]
  episodes: EpisodeOption[]
  sessions: SessionOption[]
  loading: boolean
  error: string
  lastRefreshed: string
  saving?: boolean
}>()

const selectedProject = defineModel<string>('selectedProject', { required: true })
const selectedEpisode = defineModel<string>('selectedEpisode', { required: true })
const selectedSession = defineModel<string>('selectedSession', { required: true })

const emit = defineEmits<{ refresh: [] }>()
</script>

<template>
  <label class="ctl">
    <span>项目</span>
    <el-select v-model="selectedProject" size="small" class="w-project" :loading="loading">
      <el-option
        v-for="item in projects"
        :key="item.id"
        :label="item.name"
        :value="String(item.id)"
      />
    </el-select>
  </label>
  <label class="ctl">
    <span>剧集</span>
    <el-select
      v-model="selectedEpisode"
      size="small"
      class="w-episode"
      :disabled="!episodes.length"
    >
      <el-option
        v-for="item in episodes"
        :key="item.no"
        :label="`第 ${item.no} 集 · ${item.name}`"
        :value="String(item.no)"
      />
    </el-select>
  </label>
  <label class="ctl">
    <span>会话</span>
    <el-select v-model="selectedSession" size="small" class="w-session">
      <el-option label="个人额度（不使用项目会话）" value="0" />
      <el-option
        v-for="item in sessions"
        :key="item.id"
        :label="item.name || '未命名'"
        :value="String(item.id)"
      />
    </el-select>
  </label>
  <el-button size="small" :icon="Refresh" :loading="loading" @click="emit('refresh')">
    刷新
  </el-button>
  <span class="muted quota-hint">
    已获取 {{ projects.length }} 个项目<template v-if="lastRefreshed">
      · 刷新于 {{ lastRefreshed }}</template
    >
    <template v-if="saving"> · 参数保存中</template>
  </span>
  <span v-if="error" class="session-error">{{ error }}</span>
</template>

<style scoped>
.w-project {
  width: 170px;
}

.w-episode {
  width: 160px;
}

.w-session {
  width: 220px;
}

.quota-hint {
  font-size: var(--text-xs);
  white-space: nowrap;
}

.session-error {
  color: var(--color-danger);
  font-size: var(--text-xs);
}
</style>
