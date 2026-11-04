<script setup lang="ts">
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { FolderOpened, Upload } from '@element-plus/icons-vue'
import { setMergeInput } from '@/api/case'
import AssetPickerDialog from '@/components/assets/AssetPickerDialog.vue'
import AssetUploadDialog from '@/components/upload/AssetUploadDialog.vue'
import type { CaseInfo } from '@/types/case-info'

const props = defineProps<{
  caseName: string
  info: CaseInfo
  /** 图片流程收 21:9 画布图，视频流程收 21:9 画布视频。 */
  kind: 'image' | 'video'
}>()
const emit = defineEmits<{ updated: [data: CaseInfo] }>()

const uploadOpen = ref(false)
const pickerOpen = ref(false)
const switching = ref(false)

const candidates = computed(() =>
  (props.info.assets ?? []).filter((asset) => asset.kind === props.kind),
)
const current = computed(
  () => candidates.value.find((asset) => asset.id === props.info.merge_input) ?? null,
)
const hint = computed(() => {
  const source = current.value ? current.value.name : '生成步采用的结果'
  if (props.kind === 'image') {
    return (
      `拼接输入：${source}。外部改完的 21:9 画布图可以直接上传并选中，` +
      '也可以从素材库挑一张；2:1 源图或成片会被拼接拒绝。'
    )
  }
  return (
    `拼接输入：${source}。外部改完的 21:9 画布视频可以直接上传并选中，` +
    '也可以从素材库挑一段；2:1 源片或成片会被裁回拒绝。'
  )
})
const pickerHint = computed(() =>
  props.kind === 'image'
    ? '从本 case 素材库里选一张 21:9 画布图（外部改完导回的结果）；2:1 源图或成片不能拼接。'
    : '从本 case 素材库里选一段 21:9 画布视频（外部改完导回的结果）；2:1 源片或成片不能裁回。',
)

async function apply(assetId: string | null, message: string): Promise<void> {
  if (switching.value) return
  switching.value = true
  try {
    const data = await setMergeInput(props.caseName, assetId)
    emit('updated', data)
    ElMessage.success(message)
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '切换拼接输入失败')
  } finally {
    switching.value = false
  }
}

function onPicked(ids: string[]): void {
  pickerOpen.value = false
  const id = ids[0]
  if (id) void apply(id, '拼接输入已换成素材库里的这个文件')
}

function useAdoptedResult(): void {
  void apply(null, '已用回生成结果')
}

function onUploaded(data: CaseInfo): void {
  uploadOpen.value = false
  emit('updated', data)
}
</script>

<template>
  <div class="merge-input">
    <div class="merge-input-actions">
      <el-button size="small" :icon="Upload" @click="uploadOpen = true">
        上传并用作拼接输入
      </el-button>
      <el-button
        size="small"
        :icon="FolderOpened"
        :loading="switching"
        :disabled="!candidates.length"
        @click="pickerOpen = true"
      >
        从素材库选择
      </el-button>
      <el-button v-if="info.merge_input" size="small" link @click="useAdoptedResult">
        用回生成结果
      </el-button>
    </div>
    <p class="merge-input-hint muted">{{ hint }}</p>

    <AssetUploadDialog
      v-model="uploadOpen"
      destination="merge-input"
      :case-name="caseName"
      :merge-input-kind="kind"
      @added="onUploaded"
    />
    <AssetPickerDialog
      v-model="pickerOpen"
      :case-name="caseName"
      :info="info"
      :mode="kind === 'video' ? 'video' : 'image'"
      :only-kind="kind"
      title="选择拼接输入"
      :hint="pickerHint"
      confirm-text="用作拼接输入"
      @confirm="onPicked"
      @updated="emit('updated', $event)"
    />
  </div>
</template>

<style scoped>
.merge-input-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-3);
}

.merge-input-hint {
  margin: var(--space-2) 0 0;
  font-size: var(--text-xs);
  line-height: 1.55;
}
</style>
