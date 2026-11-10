<script setup lang="ts">
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Upload } from '@element-plus/icons-vue'
import { setMergeInput } from '@/api/case'
import AssetUploadDialog from '@/components/upload/AssetUploadDialog.vue'
import type { CaseInfo } from '@/types/case-info'

const props = defineProps<{
  caseName: string
  info: CaseInfo
  /** 图片流程收 21:9 画布图，视频流程收 21:9 画布视频或 patch 的视口视频。 */
  kind: 'image' | 'video'
  /** 提示文案的语境；缺省按视频/图片的旧拼接输入。 */
  purpose?: 'patch' | 'recompose' | 'image'
}>()
const emit = defineEmits<{ updated: [data: CaseInfo] }>()

const uploadOpen = ref(false)
const switching = ref(false)

const current = computed(
  () => (props.info.assets ?? []).find((asset) => asset.id === props.info.merge_input) ?? null,
)
const hint = computed(() => {
  if (props.purpose === 'patch') {
    return (
      '合并输入：外部重绘的视口视频。上传并选中后，合并会用所选抽取版本的 clip.json ' +
      '重建投影；比例不一致会被合并拒绝，除非勾选允许裁切。'
    )
  }
  const source = current.value ? current.value.name : '生成步已采用的生成结果'
  if (props.kind === 'image') {
    return (
      `拼接输入：${source}。外部改完的 21:9 画布图可以直接上传并选中；` +
      '2:1 源图或成片会被拼接拒绝。'
    )
  }
  return (
    `拼接输入：${source}。外部改完的 21:9 画布视频可以直接上传并选中；` +
    '2:1 源片或成片会被裁回拒绝。'
  )
})

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

function onUploaded(data: CaseInfo): void {
  uploadOpen.value = false
  emit('updated', data)
}
</script>

<template>
  <div class="merge-input">
    <div class="merge-input-actions">
      <span class="merge-input-label mono">
        {{ current ? current.name : '已采用的生成结果（该版本 result.mp4）' }}
      </span>
      <el-button size="small" :icon="Upload" @click="uploadOpen = true">
        上传并用作拼接输入
      </el-button>
      <el-button
        v-if="info.merge_input"
        link
        type="primary"
        :loading="switching"
        @click="apply(null, '已切回已采用的生成结果')"
      >
        改用已采用的生成结果
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
  </div>
</template>

<style scoped>
.merge-input-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
}

.merge-input-label {
  font-size: var(--text-sm);
  color: var(--color-text);
}

.merge-input-hint {
  margin: var(--space-2) 0 0;
  font-size: var(--text-xs);
  line-height: 1.55;
}
</style>
