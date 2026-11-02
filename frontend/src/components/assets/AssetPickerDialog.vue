<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { Picture, Upload, VideoCamera } from '@element-plus/icons-vue'
import AssetUploadDialog from '@/components/upload/AssetUploadDialog.vue'
import type { AssetDetail, CaseInfo } from '@/types/case-info'

const props = defineProps<{
  modelValue: boolean
  caseName: string
  info: CaseInfo
  mode: 'video' | 'keyframe' | 'image'
  /** 覆盖标题：relight 的槽位选择不是「加入提交」，默认文案会误导。 */
  title?: string
  /** 覆盖副标题。 */
  hint?: string
  /** 覆盖确认按钮文案。 */
  confirmText?: string
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  confirm: [ids: string[]]
  updated: [info: CaseInfo]
}>()

const search = ref('')
const kind = ref<'all' | 'image' | 'video'>('all')
const hideJoined = ref(false)
const selected = ref<string[]>([])
const uploadOpen = ref(false)

const joinedIds = computed(() => {
  if (props.mode === 'keyframe') {
    return new Set((props.info.keyframe?.refs ?? []).map((item) => item.asset))
  }
  return new Set((props.info.submit_entries ?? []).map((item) => item.asset))
})

const rows = computed(() => {
  const query = search.value.trim().toLowerCase()
  return (props.info.assets ?? []).filter((asset) => {
    if (props.mode !== 'video' && asset.kind !== 'image') return false
    if (kind.value !== 'all' && asset.kind !== kind.value) return false
    if (hideJoined.value && joinedIds.value.has(asset.id)) return false
    if (query) {
      const text = `${asset.id} ${asset.name} ${asset.label}`.toLowerCase()
      if (!text.includes(query)) return false
    }
    return true
  })
})

const selectedCount = computed(() => selected.value.length)

function isJoined(asset: AssetDetail): boolean {
  return joinedIds.value.has(asset.id)
}

function orderOf(asset: AssetDetail): number {
  return selected.value.indexOf(asset.id)
}

function usageCount(asset: AssetDetail): number {
  let count = 0
  if (props.info.submit_entries?.some((item) => item.asset === asset.id)) count += 1
  if (props.info.keyframe?.refs?.some((item) => item.asset === asset.id)) count += 1
  if (props.info.keyframe?.first?.asset === asset.id) count += 1
  if (props.info.keyframe?.last?.asset === asset.id) count += 1
  return count
}

function toggle(asset: AssetDetail): void {
  if (isJoined(asset)) return
  const index = selected.value.indexOf(asset.id)
  if (index >= 0) selected.value.splice(index, 1)
  else selected.value.push(asset.id)
}

function close(): void {
  emit('update:modelValue', false)
}

function confirm(): void {
  if (!selected.value.length) return
  emit('confirm', [...selected.value])
  close()
}

function onUploaded(data: CaseInfo): void {
  emit('updated', data)
}

watch(
  () => props.modelValue,
  (open) => {
    if (!open) return
    search.value = ''
    kind.value = props.mode === 'video' ? 'all' : 'image'
    hideJoined.value = false
    selected.value = []
  },
)
</script>

<template>
  <el-dialog
    :model-value="modelValue"
    width="min(980px, 94vw)"
    :close-on-click-modal="true"
    @update:model-value="
      (value: string | number | boolean | undefined) => emit('update:modelValue', Boolean(value))
    "
  >
    <template #header>
      <div>
        <h3 class="picker-title">
          {{
            title ??
            (mode === 'video'
              ? '添加素材到本次视频提交'
              : mode === 'image'
                ? '添加素材到本次图片提交'
                : '添加参考图到首尾帧')
          }}
        </h3>
        <p class="picker-sub muted">
          {{
            hint ??
            (mode === 'video'
              ? '从素材库选择；勾选顺序就是加入顺序。加入后在提交清单里设置角色。'
              : mode === 'image'
                ? '图片流程只引用图片素材；勾选顺序就是提示词里 @ImageN 的顺序。'
                : '从素材库选择图片；第一张在缺少人物三视图时成为主三视图，其余默认细节参考。')
          }}
        </p>
      </div>
    </template>

    <div class="picker-toolbar">
      <el-input v-model="search" size="small" placeholder="搜索 id 或文件名" clearable />
      <el-select
        v-model="kind"
        size="small"
        :disabled="mode !== 'video'"
        style="width: 110px"
      >
        <el-option label="全部类型" value="all" />
        <el-option label="图片" value="image" />
        <el-option label="视频" value="video" />
      </el-select>
      <el-checkbox v-model="hideJoined">只看未加入</el-checkbox>
      <span class="spacer" />
      <el-button size="small" :icon="Upload" @click="uploadOpen = true">上传素材</el-button>
    </div>

    <div v-if="!rows.length" class="empty">
      <el-empty description="没有符合筛选条件的素材" :image-size="64" />
    </div>
    <div v-else class="picker-list">
      <div
        v-for="asset in rows"
        :key="asset.id"
        class="picker-row"
        :class="{ joined: isJoined(asset) }"
        @click="toggle(asset)"
      >
        <el-checkbox
          :model-value="selected.includes(asset.id)"
          :disabled="isJoined(asset)"
          @click.stop
        />
        <div class="picker-thumb">
          <img v-if="asset.url && asset.exists" :src="asset.url" alt="" />
          <el-icon v-else :size="18">
            <VideoCamera v-if="asset.kind === 'video'" />
            <Picture v-else />
          </el-icon>
        </div>
        <div class="picker-main">
          <div class="picker-name">{{ asset.name }}</div>
          <div class="picker-meta">
            <span class="mono">{{ asset.id }}</span>
            <span v-if="asset.width && asset.height">{{ asset.width }}×{{ asset.height }}</span>
            <span>{{ asset.kind === 'image' ? '图片' : '视频' }}</span>
          </div>
        </div>
        <div class="picker-usage">
          <el-tag size="small" effect="light">
            {{ usageCount(asset) ? `引用 ${usageCount(asset)} 处` : '未引用' }}
          </el-tag>
          <el-tag v-if="isJoined(asset)" size="small" type="info" effect="light">已加入</el-tag>
          <span v-else-if="orderOf(asset) >= 0" class="picker-order">
            {{ orderOf(asset) + 1 }}
          </span>
        </div>
      </div>
    </div>

    <template #footer>
      <div class="picker-foot">
        <span class="muted">已选 {{ selectedCount }} 项</span>
        <span class="spacer" />
        <el-button size="small" @click="close">取消</el-button>
        <el-button size="small" type="primary" :disabled="!selectedCount" @click="confirm">
          {{ confirmText ?? '添加' }}
        </el-button>
      </div>
    </template>

    <AssetUploadDialog v-model="uploadOpen" :case-name="caseName" @added="onUploaded" />
  </el-dialog>
</template>

<style scoped>
.picker-title {
  margin: 0;
  font-size: var(--text-lg);
}

.picker-sub {
  margin: var(--space-1) 0 0;
  font-size: var(--text-xs);
}

.picker-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
  margin-bottom: var(--space-3);
}

.picker-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  max-height: 56vh;
  overflow: auto;
}

.picker-row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-2);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  cursor: pointer;
}

.picker-row:hover {
  background: var(--color-surface-muted);
}

.picker-row.joined {
  opacity: 0.6;
  cursor: not-allowed;
  background: var(--color-surface-muted);
}

.picker-thumb {
  display: grid;
  place-items: center;
  width: 56px;
  height: 40px;
  flex: 0 0 auto;
  overflow: hidden;
  border-radius: var(--radius-sm);
  background: var(--color-video-bg);
  color: var(--color-text-faint);
}

.picker-thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.picker-main {
  flex: 1;
  min-width: 0;
}

.picker-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: var(--text-sm);
  font-weight: 600;
}

.picker-meta {
  display: flex;
  gap: var(--space-2);
  color: var(--color-text-muted);
  font-size: 11px;
}

.picker-usage {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
}

.picker-order {
  display: grid;
  place-items: center;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: var(--color-primary);
  color: #fff;
  font-size: 11px;
  font-weight: 700;
}

.picker-foot {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.spacer {
  flex: 1;
}

.empty {
  padding: var(--space-3);
}
</style>
