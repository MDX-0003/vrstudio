<script setup lang="ts">
import type { RangeTag } from './types'

defineProps<{
  time: string
  geometry: string
  meta?: string
  tags?: RangeTag[]
  active?: boolean
  selected?: boolean
  selectable?: boolean
  activatable?: boolean
  deletable?: boolean
}>()

const emit = defineEmits<{
  select: []
  activate: []
  delete: []
}>()
</script>

<template>
  <div
    class="range-row"
    :class="{ active, selected, selectable }"
    @click="selectable && emit('select')"
    @dblclick="activatable && emit('activate')"
  >
    <div class="range-main">
      <div class="range-title">
        <span class="range-time mono">{{ time }}</span>
        <span
          v-for="tag in tags ?? []"
          :key="tag.text"
          class="tag"
          :class="tag.kind ? `tag-${tag.kind}` : ''"
        >
          {{ tag.text }}
        </span>
      </div>
      <div class="range-geo mono">{{ geometry }}</div>
      <div v-if="meta" class="range-updated">{{ meta }}</div>
    </div>
    <el-button
      v-if="deletable"
      link
      type="danger"
      class="range-delete"
      @click.stop="emit('delete')"
    >
      删除
    </el-button>
  </div>
</template>

<style scoped>
.range-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-2);
  padding: var(--space-2);
  border: 1px solid transparent;
  border-radius: var(--radius-md);
  user-select: none;
}

.range-row.selectable {
  cursor: pointer;
}

.range-row:hover {
  background: var(--color-surface-muted);
}

.range-row.selected {
  border-color: var(--color-border-strong);
  background: var(--color-surface-muted);
}

.range-row.active {
  border-color: #bfdbfe;
  background: var(--color-primary-soft);
}

.range-main {
  min-width: 0;
}

.range-title {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  font-size: var(--text-sm);
  font-weight: 600;
}

.range-time {
  font-family: var(--font-mono);
}

.range-geo {
  margin-top: 1px;
  color: var(--color-text-muted);
  font-size: 11px;
  line-height: 1.5;
}

.range-updated {
  margin-top: 1px;
  color: var(--color-text-faint);
  font-size: 11px;
}

.range-delete {
  opacity: 0.55;
}

.range-row:hover .range-delete,
.range-row.active .range-delete {
  opacity: 1;
}

.tag {
  display: inline-flex;
  align-items: center;
  height: 20px;
  padding: 0 6px;
  border-radius: 999px;
  background: var(--color-surface-muted);
  color: var(--color-text-muted);
  font-size: 11px;
}

.tag-current {
  background: var(--color-primary-soft);
  color: var(--color-primary);
}

.tag-ok {
  background: var(--color-success-soft);
  color: var(--color-success);
}

.tag-warn {
  background: var(--color-warning-soft);
  color: var(--color-warning);
}
</style>
