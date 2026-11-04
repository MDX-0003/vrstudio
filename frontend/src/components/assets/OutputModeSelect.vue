<script setup lang="ts">
import type { OutputMode, OutputOption } from '@/types/case-info'

defineProps<{
  modelValue: OutputMode
  options: OutputOption[]
}>()
const emit = defineEmits<{ 'update:modelValue': [value: OutputMode] }>()

function label(item: OutputOption): string {
  const prefix = item.mode === 'case' ? '案例分辨率' : '跟随输入'
  return `${prefix} ${item.width}×${item.height}`
}

function onChange(value: string | number | boolean | undefined): void {
  emit('update:modelValue', value === 'input' ? 'input' : 'case')
}
</script>

<template>
  <el-select
    :model-value="modelValue"
    size="small"
    style="width: 210px"
    aria-label="输出分辨率"
    @update:model-value="onChange"
  >
    <el-option v-for="item in options" :key="item.mode" :value="item.mode" :label="label(item)" />
  </el-select>
</template>
