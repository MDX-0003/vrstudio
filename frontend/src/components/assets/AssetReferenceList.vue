<script setup lang="ts">
import { computed } from 'vue'
import { ArrowDown, ArrowUp, Delete, Picture, Plus } from '@element-plus/icons-vue'
import { legacyRoleOption, ASSET_ROLE_LABELS, type RoleOption } from '@/constants/assetRoles'
import type { ReferenceRow } from '@/types/reference'

const props = withDefaults(
  defineProps<{
    rows: ReferenceRow[]
    roleOptions: RoleOption[]
    roleHelp?: Record<string, string>
    showNote?: boolean
    compact?: boolean
    addLabel?: string
    emptyText?: string
    disabled?: boolean
  }>(),
  {
    roleHelp: () => ({}),
    showNote: false,
    compact: false,
    addLabel: '添加素材',
    emptyText: '还没有引用条目',
    disabled: false,
  },
)

const emit = defineEmits<{
  add: []
  remove: [asset: string]
  move: [asset: string, delta: number]
  role: [asset: string, role: string]
  note: [asset: string, note: string]
}>()

function optionsFor(row: ReferenceRow): RoleOption[] {
  const options = [...props.roleOptions]
  if (row.role && !options.some((item) => item.value === row.role)) {
    const legacy = legacyRoleOption(row.role)
    if (legacy) options.push(legacy)
  }
  return options
}

function labelFor(row: ReferenceRow): string {
  return (
    row.roleLabel ||
    props.roleOptions.find((item) => item.value === row.role)?.label ||
    ASSET_ROLE_LABELS[row.role] ||
    row.role
  )
}

function helpFor(role: string): string {
  const option = props.roleOptions.find((item) => item.value === role)
  return props.roleHelp[role] || option?.help || ''
}

function canUp(row: ReferenceRow, index: number): boolean {
  return !row.locked && index > 0 && !props.rows[index - 1]?.locked
}

function canDown(row: ReferenceRow, index: number): boolean {
  return !row.locked && index < props.rows.length - 1
}

const total = computed(() => props.rows.length)
</script>

<template>
  <div class="ref-list" :class="{ compact }">
    <div v-if="!rows.length" class="ref-empty">
      <el-empty :description="emptyText" :image-size="64" />
    </div>
    <div
      v-for="(row, index) in rows"
      :key="row.asset"
      class="ref-row"
      :class="{ fixed: row.roleFixed, locked: row.locked }"
    >
      <span class="ref-no">{{ row.number ?? index + 1 }}</span>
      <div class="ref-thumb">
        <img v-if="row.url && row.exists !== false" :src="row.url" alt="" />
        <el-icon v-else :size="18"><Picture /></el-icon>
      </div>
      <div class="ref-main">
        <div class="ref-name">{{ row.file?.name || row.asset }}</div>
        <div class="ref-file">
          <span class="mono">{{ row.asset }}</span>
          <span v-if="row.exists === false" class="ref-missing">文件缺失</span>
        </div>
      </div>
      <div v-if="row.roleFixed || row.locked" class="role-static">
        <el-tag size="small" effect="light">{{ labelFor(row) }}</el-tag>
      </div>
      <el-select
        v-else
        :model-value="row.role"
        :disabled="disabled || row.locked"
        :title="helpFor(row.role)"
        size="small"
        class="role-select"
        @change="(value: string | number | boolean | undefined) => emit('role', row.asset, String(value))"
      >
        <el-option
          v-for="option in optionsFor(row)"
          :key="option.value"
          :label="option.label"
          :value="option.value"
        >
          <span :title="helpFor(option.value)">{{ option.label }}</span>
        </el-option>
      </el-select>
      <el-input
        v-if="showNote && !row.locked"
        :model-value="row.note || ''"
        :disabled="disabled || row.roleFixed"
        size="small"
        class="ref-note"
        placeholder="写这张图负责的细节"
        @input="(value: string) => emit('note', row.asset, String(value))"
      />
      <span v-else-if="!compact" class="ref-note-static">
        {{ row.noteLabel ?? (row.locked ? '锁定' : '—') }}
      </span>
      <div class="ref-actions">
        <el-button
          link
          size="small"
          :icon="ArrowUp"
          :disabled="disabled || !canUp(row, index)"
          aria-label="上移"
          @click="emit('move', row.asset, -1)"
        />
        <el-button
          link
          size="small"
          :icon="ArrowDown"
          :disabled="disabled || !canDown(row, index)"
          aria-label="下移"
          @click="emit('move', row.asset, 1)"
        />
        <el-button
          v-if="!row.locked"
          link
          size="small"
          type="danger"
          :icon="Delete"
          :disabled="disabled"
          aria-label="从清单移除"
          @click="emit('remove', row.asset)"
        />
      </div>
    </div>
    <div class="ref-foot">
      <el-button size="small" :icon="Plus" :disabled="disabled" @click="emit('add')">
        {{ addLabel }}
      </el-button>
      <span class="muted">共 {{ total }} 项；悬停角色可看说明</span>
    </div>
  </div>
</template>

<style scoped>
.ref-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.ref-row {
  display: grid;
  grid-template-columns: 32px 64px minmax(0, 1fr) 132px minmax(150px, 1.2fr) 96px;
  gap: var(--space-2);
  align-items: center;
  padding: var(--space-2);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
}

.ref-row.fixed {
  border-color: var(--color-primary);
  background: var(--color-primary-soft);
}

.ref-row.locked {
  border-style: dashed;
  background: var(--color-surface-muted);
}

.ref-list.compact .ref-row {
  grid-template-columns: 32px 64px minmax(0, 1fr) 140px 96px;
}

.ref-no {
  display: grid;
  place-items: center;
  width: 28px;
  height: 28px;
  border-radius: 50%;
  background: var(--color-primary-soft);
  color: var(--color-primary);
  font-size: 11px;
  font-weight: 700;
}

.ref-thumb {
  display: grid;
  place-items: center;
  width: 64px;
  height: 44px;
  overflow: hidden;
  border-radius: var(--radius-sm);
  background: var(--color-surface-muted);
  color: var(--color-text-faint);
}

.ref-thumb img {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.ref-main {
  min-width: 0;
}

.ref-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: var(--text-sm);
  font-weight: 600;
}

.ref-file {
  display: flex;
  gap: var(--space-2);
  overflow: hidden;
  color: var(--color-text-muted);
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ref-missing {
  color: var(--color-danger);
}

.role-select {
  width: 100%;
}

.role-static {
  display: flex;
  align-items: center;
}

.ref-note {
  width: 100%;
}

.ref-note-static {
  overflow: hidden;
  color: var(--color-text-faint);
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ref-actions {
  display: inline-flex;
  align-items: center;
  justify-content: flex-end;
  gap: 2px;
}

.ref-actions :deep(.el-button + .el-button) {
  margin-left: 0;
}

.ref-empty {
  padding: var(--space-2);
}

.ref-foot {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--text-xs);
}

@media (max-width: 1280px) {
  .ref-row {
    grid-template-columns: 28px 56px minmax(0, 1fr) 128px;
  }

  .ref-row .ref-note,
  .ref-row .ref-note-static,
  .ref-row .ref-actions {
    grid-column: 3 / 5;
  }

  .ref-list.compact .ref-row {
    grid-template-columns: 28px 56px minmax(0, 1fr) 132px;
  }

  .ref-list.compact .ref-actions {
    grid-column: auto;
  }
}
</style>
