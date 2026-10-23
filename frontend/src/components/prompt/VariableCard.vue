<script setup lang="ts">
import type { PromptVariable } from '@/types/case'

defineProps<{ variable: PromptVariable }>()
const emit = defineEmits<{ 'update:value': [value: string] }>()

function onInput(value: string): void {
  emit('update:value', value)
}
</script>

<template>
  <div class="var-card">
    <div class="var-head">
      <span class="var-label">{{ variable.label }}</span>
      <span class="var-key mono">{{ variable.key }}</span>
    </div>
    <p v-if="variable.help" class="var-help">{{ variable.help }}</p>
    <el-input
      :model-value="variable.value"
      type="textarea"
      :autosize="{ minRows: 3, maxRows: 8 }"
      @update:model-value="onInput"
    />
  </div>
</template>

<style scoped>
.var-card {
  padding: var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}

.var-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-2);
}

.var-label {
  font-weight: 600;
}

.var-key {
  color: var(--color-text-faint);
  font-size: var(--text-xs);
}

.var-help {
  margin: var(--space-1) 0 var(--space-2);
  color: var(--color-text-muted);
  font-size: var(--text-xs);
}
</style>
