<script setup lang="ts">
import { computed } from 'vue'
import AssetReferenceList from '@/components/assets/AssetReferenceList.vue'
import { FIRST_LAST_ROLE_OPTIONS, VIDEO_ROLE_OPTIONS } from '@/constants/assetRoles'
import type { ReferenceRow } from '@/types/reference'

const props = withDefaults(
  defineProps<{
    caseName: string
    rows: ReferenceRow[]
    mode: string
    roleHelp?: Record<string, string>
    selectedCount: number
    disabled?: boolean
  }>(),
  { roleHelp: () => ({}), disabled: false },
)

const emit = defineEmits<{
  add: []
  remove: [asset: string]
  move: [asset: string, delta: number]
  role: [asset: string, role: string]
}>()

const roleOptions = computed(() =>
  props.mode === 'firstLastFrame' ? FIRST_LAST_ROLE_OPTIONS : VIDEO_ROLE_OPTIONS,
)
</script>

<template>
  <section class="panel">
    <div class="panel-head">
      <h2 class="panel-title">本次视频提交</h2>
      <div class="head-actions">
        <span class="muted">{{ selectedCount }} 项已选</span>
      </div>
    </div>
    <div class="panel-body">
      <AssetReferenceList
        :rows="rows"
        :role-options="roleOptions"
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
