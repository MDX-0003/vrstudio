<script setup lang="ts">
import { computed } from 'vue'
import AssetReferenceList from '@/components/assets/AssetReferenceList.vue'
import { optionsFor } from '@/utils/roles'
import type { RoleOption } from '@/constants/assetRoles'
import type { RoleSpec } from '@/types/case-info'
import type { ReferenceRow } from '@/types/reference'

const props = withDefaults(
  defineProps<{
    caseName: string
    rows: ReferenceRow[]
    mode: string
    roles: RoleSpec[]
    roleHelp?: Record<string, string>
    selectedCount: number
    disabled?: boolean
    /** 面板标题；图片流程用「本次图片提交」。 */
    title?: string
    /** 装配器自动附带的角色，不出现在下拉里（图片流程的基图）。 */
    excludeRoles?: readonly string[]
  }>(),
  { roleHelp: () => ({}), disabled: false, title: '本次视频提交' },
)

const emit = defineEmits<{
  add: []
  remove: [asset: string]
  move: [asset: string, delta: number]
  role: [asset: string, role: string]
}>()

const roleOptions = computed(() =>
  optionsFor(props.roles, { mode: props.mode, exclude: props.excludeRoles }),
)

function roleOptionsFor(row: ReferenceRow): RoleOption[] {
  return optionsFor(
    props.roles,
    { kind: row.kind, mode: props.mode, exclude: props.excludeRoles },
    row.role,
  )
}
</script>

<template>
  <section class="panel">
    <div class="panel-head">
      <h2 class="panel-title">{{ title }}</h2>
      <div class="head-actions">
        <span class="muted">{{ selectedCount }} 项已选</span>
      </div>
    </div>
    <div class="panel-body">
      <AssetReferenceList
        :rows="rows"
        :role-options="roleOptions"
        :role-options-for="roleOptionsFor"
        :role-help="roleHelp"
        :disabled="disabled"
        compact
        add-label="添加素材"
        empty-text="还没有选择素材；点「添加素材」从素材库挑选"
        @add="emit('add')"
        @remove="(asset) => emit('remove', asset)"
        @move="(asset, delta) => emit('move', asset, delta)"
        @role="(asset, role) => emit('role', asset, role)"
      />
      <p class="note muted">
        清单里的角色决定本次提交的类型；同一素材可以同时被图片步引用，两边角色互不影响。
        上下移动决定提示词里的 @VideoN / @ImageN 顺序。
      </p>
    </div>
  </section>
</template>

<style scoped>
.head-actions {
  display: inline-flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
  margin-left: auto;
}

.note {
  margin: var(--space-3) 0 0;
  font-size: var(--text-xs);
  line-height: 1.6;
}
</style>
