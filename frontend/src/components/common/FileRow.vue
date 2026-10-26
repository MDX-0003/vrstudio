<script setup lang="ts">
import { computed } from 'vue'
import { VideoPlay, View } from '@element-plus/icons-vue'
import { formatSize } from '@/utils/format'
import type { FileInfo } from '@/types/case-info'

const props = defineProps<{ entry: FileInfo }>()

const href = computed(() => props.entry.open_url ?? '')
const isVideo = computed(() => /\.(mp4|m4v|mov|webm|mkv|avi)$/i.test(props.entry.name))
</script>

<template>
  <div class="file-row" :class="{ missing: !entry.exists }">
    <div class="file-main">
      <div class="file-name mono">{{ entry.name }}</div>
      <div class="file-meta">
        <span>{{ entry.rel || entry.path }}</span>
        <span>{{ formatSize(entry.size) }}</span>
        <span v-if="!entry.exists" class="bad">文件缺失</span>
      </div>
    </div>
    <el-link
      v-if="entry.exists && href"
      :href="href"
      target="_blank"
      type="primary"
      underline="never"
    >
      <el-icon><VideoPlay v-if="isVideo" /><View v-else /></el-icon>
      {{ isVideo ? '播放' : '打开' }}
    </el-link>
  </div>
</template>

<style scoped>
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
</style>
