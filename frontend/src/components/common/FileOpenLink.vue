<script setup lang="ts">
import { computed } from 'vue'
import { View } from '@element-plus/icons-vue'

interface OpenableFile {
  name?: string
  rel?: string | null
  path?: string
  exists?: boolean
  open_url?: string | null
}

const props = defineProps<{ file?: OpenableFile | null; label?: string }>()

const title = computed(() => {
  const file = props.file
  if (!file) return ''
  return [file.name, file.rel || file.path].filter(Boolean).join('\n')
})
</script>

<template>
  <el-link
    v-if="file?.exists && file.open_url"
    :href="file.open_url"
    target="_blank"
    class="file-open"
    :title="title"
    :aria-label="label || '打开原图'"
  >
    <el-icon><View /></el-icon>
  </el-link>
</template>

<style scoped>
.file-open {
  align-items: center;
  line-height: 1;
}
</style>
