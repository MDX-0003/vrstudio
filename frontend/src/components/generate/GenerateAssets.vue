<script setup lang="ts">
import { Bottom, Delete, Picture, Plus, Top, VideoCamera } from '@element-plus/icons-vue'
import { ASSET_ROLE_OPTIONS } from '@/constants/assetRoles'
import type { AssetRow } from '@/types/case'

defineProps<{
  assets: AssetRow[]
  selectedCount: number
  saving: boolean
}>()

const emit = defineEmits<{
  toggle: [asset: AssetRow, selected: boolean]
  role: [asset: AssetRow, role: string]
  move: [asset: AssetRow, delta: number]
  remove: [asset: AssetRow]
  upload: []
}>()
</script>

<template>
  <div class="panel">
    <div class="panel-head">
      <h2 class="panel-title">素材与本次提交</h2>
      <div class="panel-head-actions">
        <span class="muted">{{ selectedCount }} 项已勾选</span>
        <el-button size="small" :icon="Plus" @click="emit('upload')">上传素材</el-button>
      </div>
    </div>
    <div class="panel-body asset-list">
      <div v-if="!assets.length" class="asset-empty">
        <el-empty description="还没有素材" :image-size="72" />
      </div>
      <div
        v-for="(asset, index) in assets"
        :key="asset.id"
        class="asset-row"
        :class="{ dim: !asset.selected }"
      >
        <el-checkbox
          :model-value="asset.selected"
          :disabled="saving"
          aria-label="参与本次提交"
          @change="emit('toggle', asset, Boolean($event))"
        />
        <div class="asset-thumb" :style="{ background: asset.tone }">
          <img v-if="asset.url && asset.exists" :src="asset.url" alt="" class="asset-thumb-img" />
          <el-icon v-else :size="20">
            <VideoCamera v-if="asset.kind === 'video'" />
            <Picture v-else />
          </el-icon>
        </div>
        <div class="asset-main">
          <div class="asset-name">{{ asset.name }}</div>
          <div class="asset-meta">{{ asset.meta }}</div>
        </div>
        <el-select
          v-if="!asset.roleFixed"
          :model-value="asset.roleRaw || asset.role"
          size="small"
          style="width: 128px"
          @change="emit('role', asset, String($event))"
        >
          <el-option
            v-for="role in ASSET_ROLE_OPTIONS"
            :key="role.value"
            :label="role.label"
            :value="role.value"
          />
        </el-select>
        <span v-else class="role-fixed">{{ asset.role }}</span>
        <div class="asset-actions">
          <el-button
            link
            :icon="Top"
            :disabled="index === 0"
            aria-label="上移素材"
            @click="emit('move', asset, -1)"
          />
          <el-button
            link
            :icon="Bottom"
            :disabled="index === assets.length - 1 || asset.roleRaw === 'edit_master'"
            aria-label="下移素材"
            @click="emit('move', asset, 1)"
          />
          <el-button
            link
            type="danger"
            :icon="Delete"
            aria-label="删除素材"
            @click="emit('remove', asset)"
          />
        </div>
      </div>
      <div class="asset-note muted">
        勾选决定本次提交哪些素材；排序会影响提示词里的 @Video1 / @Image1 编号。
      </div>
    </div>
  </div>
</template>

<style scoped>
.panel-head-actions {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
}

.asset-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.asset-row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-2) var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  transition:
    background var(--duration-fast) ease,
    opacity var(--duration-fast) ease;
}

.asset-row:hover {
  background: var(--color-surface-muted);
}

.asset-row.dim {
  opacity: 0.55;
}

.asset-thumb {
  display: grid;
  place-items: center;
  width: 56px;
  height: 40px;
  flex: 0 0 auto;
  overflow: hidden;
  border-radius: var(--radius-sm);
  color: rgba(255, 255, 255, 0.9);
}

.asset-thumb-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.role-fixed {
  width: 128px;
  color: var(--color-text-muted);
  font-size: var(--text-xs);
  text-align: right;
}

.asset-empty {
  padding: var(--space-3);
}

.asset-note {
  margin-top: var(--space-2);
  font-size: var(--text-xs);
}

.asset-main {
  flex: 1;
  min-width: 0;
}

.asset-actions {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  margin-left: var(--space-1);
}

.asset-actions .el-button + .el-button {
  margin-left: 0;
}

.asset-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 600;
}

.asset-meta {
  color: var(--color-text-muted);
  font-size: var(--text-xs);
}
</style>
