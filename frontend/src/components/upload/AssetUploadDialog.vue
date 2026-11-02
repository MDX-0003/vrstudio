<script setup lang="ts">
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Delete, UploadFilled } from '@element-plus/icons-vue'
import { addAssetsByUploads, setRelightSlot, uploadMergeInput } from '@/api/case'
import {
  cancelUpload,
  cancelUploadKeepalive,
  completeUpload,
  createUpload,
  getUpload,
  putChunk,
} from '@/api/uploads'
import { defaultRole } from '@/constants/assetRoles'
import { formatSize } from '@/utils/format'
import type { CaseInfo } from '@/types/case-info'

type RowStatus = 'queued' | 'uploading' | 'done' | 'error' | 'canceled'

interface Row {
  key: string
  file: File
  kind: 'video' | 'image'
  role: string
  uploadId: string | null
  received: number
  status: RowStatus
  error: string
}

const props = defineProps<{
  modelValue: boolean
  caseName?: string
  /**
   * assets = 加入素材库（默认，需 caseName）；
   * source = 用作新建 case 的源视频/源图片；
   * slot = 放进 relight 的一个固定槽位（需 caseName 与 slotRole）；
   * merge-input = 上传并选作 fullRedrawImg 的拼接输入（入库后自动选中）。
   */
  destination?: 'assets' | 'source' | 'slot' | 'merge-input'
  /** 源文件类型：视频流程收视频，图片流程收图片；加入素材库时忽略。 */
  sourceKind?: 'video' | 'image'
  /** slot 目的地的角色：relight_gt 收视频或图片，relight_atmo 只收图片。 */
  slotRole?: 'relight_gt' | 'relight_atmo'
}>()
const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  added: [data: CaseInfo]
  uploaded: [file: { uploadId: string; name: string }]
}>()

const isSource = computed(() => props.destination === 'source')
const isSlot = computed(() => props.destination === 'slot')
const isMergeInput = computed(() => props.destination === 'merge-input')
/** 槽位、源文件与拼接输入都一次只收一个文件：都只有一个位置。 */
const singleFile = computed(() => isSource.value || isSlot.value || isMergeInput.value)
const slotAcceptsVideo = computed(() => props.slotRole === 'relight_gt')
const slotTitle = computed(() =>
  props.slotRole === 'relight_atmo' ? '上传氛围图' : '上传重打光底版',
)
const dialogTitle = computed(() => {
  if (isMergeInput.value) return '上传拼接输入'
  return isSlot.value ? slotTitle.value : ''
})
const sourceKind = computed<'video' | 'image'>(() => props.sourceKind ?? 'video')
const sourceAccept = computed(() =>
  sourceKind.value === 'image'
    ? '.png,.jpg,.jpeg,.webp'
    : '.mp4,.mov,.mkv,.avi',
)
const sourceTitle = computed(() => (sourceKind.value === 'image' ? '上传源图片' : '上传源视频'))
const sourceButton = computed(() => (sourceKind.value === 'image' ? '用作源图片' : '用作源视频'))

/** 对话框收哪些扩展名：槽位按角色收，源文件按流程收，素材库两种都收。 */
const accept = computed(() => {
  if (isMergeInput.value) return '.png,.jpg,.jpeg,.webp'
  if (isSlot.value) {
    return slotAcceptsVideo.value ? '.mp4,.mov,.mkv,.avi,.png,.jpg,.jpeg,.webp' : '.png,.jpg,.jpeg,.webp'
  }
  return isSource.value ? sourceAccept.value : '.mp4,.mov,.mkv,.avi,.png,.jpg,.jpeg,.webp'
})

const visible = computed({
  get: () => props.modelValue,
  set: (value: boolean) => emit('update:modelValue', value),
})

const rows = ref<Row[]>([])
const adding = ref(false)
const dragOver = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)
let disposed = false
const canceledUploads = new Set<string>()

const completed = computed(() => rows.value.filter((row) => row.status === 'done' && row.uploadId))
const uploading = computed(() => rows.value.some((row) => row.status === 'uploading'))
const canAdd = computed(() => completed.value.length > 0 && !uploading.value && !adding.value)

const VIDEO_EXTS = ['.mp4', '.mov', '.mkv', '.avi']
const IMAGE_EXTS = ['.png', '.jpg', '.jpeg', '.webp']

function kindOf(name: string): 'video' | 'image' | null {
  const lower = name.toLowerCase()
  if (VIDEO_EXTS.some((ext) => lower.endsWith(ext))) return 'video'
  if (IMAGE_EXTS.some((ext) => lower.endsWith(ext))) return 'image'
  return null
}

function storageKey(file: File): string {
  return `vrstudio-upload:${file.name}:${file.size}:${file.lastModified}`
}

function progressOf(row: Row): number {
  if (!row.file.size) return 0
  return Math.min(100, Math.round((row.received / row.file.size) * 100))
}

function statusText(row: Row): string {
  if (row.status === 'uploading') return `上传中 ${progressOf(row)}%`
  if (row.status === 'done') return '已上传'
  if (row.status === 'error') return row.error || '上传失败'
  if (row.status === 'canceled') return '已取消'
  return '等待上传'
}

async function sleep(ms: number): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, ms))
}

async function uploadRow(row: Row): Promise<void> {
  row.status = 'uploading'
  row.error = ''
  if (disposed) {
    row.status = 'canceled'
    return
  }
  const key = storageKey(row.file)
  try {
    if (!row.uploadId) {
      const saved = localStorage.getItem(key)
      if (saved) {
        try {
          const status = await getUpload(saved)
          if (status.name === row.file.name && status.size === row.file.size) {
            row.uploadId = saved
            row.received = status.received
            if (status.status === 'complete') {
              row.received = status.size
              row.status = 'done'
              return
            }
          } else {
            localStorage.removeItem(key)
          }
        } catch {
          localStorage.removeItem(key)
        }
      }
    }
    if (!row.uploadId) {
      const session = await createUpload(row.file.name, row.file.size)
      row.uploadId = session.upload_id
      row.received = 0
      localStorage.setItem(key, session.upload_id)
      if (disposed || (row.status as RowStatus) === 'canceled') {
        if (!canceledUploads.has(session.upload_id)) {
          canceledUploads.add(session.upload_id)
          cancelUploadKeepalive(session.upload_id)
        }
        return
      }
    }
    const uploadId = row.uploadId
    const chunkSize = 8 * 1024 * 1024
    while (row.received < row.file.size) {
      if (disposed || (row.status as RowStatus) === 'canceled') return
      const end = Math.min(row.received + chunkSize, row.file.size)
      const chunk = row.file.slice(row.received, end)
      let attempt = 0
      for (;;) {
        if (disposed || (row.status as RowStatus) === 'canceled') return
        try {
          const result = await putChunk(uploadId, row.received, chunk)
          row.received = result.received
          break
        } catch (err) {
          attempt += 1
          if (attempt >= 3) throw err
          await sleep(400 * attempt)
        }
      }
    }
    if (disposed) return
    await completeUpload(uploadId)
    localStorage.removeItem(key)
    row.status = 'done'
  } catch (err) {
    if (disposed || (row.status as RowStatus) === 'canceled') return
    row.status = 'error'
    row.error = err instanceof Error ? err.message : '上传失败'
    if (row.uploadId) localStorage.setItem(key, row.uploadId)
  }
}

function addFiles(files: File[]): void {
  for (const file of files) {
    const kind = kindOf(file.name)
    if (!kind) {
      ElMessage.warning(`${file.name}：只支持 mp4/mov/mkv/avi 与 png/jpg/webp`)
      continue
    }
    if (isSource.value && kind !== sourceKind.value) {
      ElMessage.warning(
        sourceKind.value === 'image'
          ? `${file.name}：源图片只支持 png/jpg/webp`
          : `${file.name}：源视频只支持 mp4/mov/mkv/avi`,
      )
      continue
    }
    if (isSlot.value && kind === 'video' && !slotAcceptsVideo.value) {
      ElMessage.warning(`${file.name}：氛围图只支持 png/jpg/webp`)
      continue
    }
    if (isMergeInput.value && kind === 'video') {
      ElMessage.warning(`${file.name}：拼接输入只支持 png/jpg/webp（21:9 画布图）`)
      continue
    }
    if (rows.value.some((row) => row.key === storageKey(file))) continue
    if (singleFile.value) {
      // 源文件与槽位都一次一个：替换列表（取消未完成的上传）。
      cancelUploadingRows(false)
      rows.value = []
    }
    const row = reactive<Row>({
      key: storageKey(file),
      file,
      kind,
      role: defaultRole(kind),
      uploadId: null,
      received: 0,
      status: 'queued',
      error: '',
    })
    rows.value.push(row)
    void uploadRow(row)
  }
}

function onPick(event: Event): void {
  const input = event.target as HTMLInputElement
  addFiles(Array.from(input.files ?? []))
  input.value = ''
}

function onDrop(event: DragEvent): void {
  dragOver.value = false
  addFiles(Array.from(event.dataTransfer?.files ?? []))
}

function cancelUploadingRows(keepalive: boolean): void {
  for (const row of rows.value) {
    if (row.status !== 'uploading') continue
    row.status = 'canceled'
    localStorage.removeItem(storageKey(row.file))
    if (!row.uploadId || canceledUploads.has(row.uploadId)) continue
    canceledUploads.add(row.uploadId)
    if (keepalive) {
      cancelUploadKeepalive(row.uploadId)
    } else {
      void cancelUpload(row.uploadId).catch(() => undefined)
    }
  }
}

function onPageHide(): void {
  disposed = true
  cancelUploadingRows(true)
}

async function removeRow(row: Row): Promise<void> {
  if (row.status === 'uploading' && row.uploadId) {
    canceledUploads.add(row.uploadId)
    try {
      await cancelUpload(row.uploadId)
    } catch {
      // 取消失败不阻止从列表移除；服务端会按 24 小时清理。
    }
  }
  row.status = 'canceled'
  localStorage.removeItem(storageKey(row.file))
  rows.value = rows.value.filter((item) => item !== row)
}

function retry(row: Row): void {
  if (row.status !== 'error') return
  row.error = ''
  void uploadRow(row)
}

async function confirmAdd(): Promise<void> {
  if (!canAdd.value) return
  adding.value = true
  try {
    if (isSource.value) {
      const row = completed.value[0]
      emit('uploaded', { uploadId: row.uploadId as string, name: row.file.name })
      rows.value = []
      visible.value = false
      return
    }
    if (isMergeInput.value) {
      const row = completed.value[0]
      const data = await uploadMergeInput(props.caseName as string, row.uploadId as string)
      emit('added', data)
      ElMessage.success('已上传并用作拼接输入')
      rows.value = []
      visible.value = false
      return
    }
    if (isSlot.value) {
      const row = completed.value[0]
      const data = await setRelightSlot(props.caseName as string, props.slotRole!, {
        uploadId: row.uploadId as string,
      })
      emit('added', data)
      ElMessage.success('已放进槽位')
      rows.value = []
      visible.value = false
      return
    }
    const data = await addAssetsByUploads(
      props.caseName as string,
      completed.value.map((row) => row.uploadId as string),
      completed.value.map((row) => row.role),
    )
    emit('added', data)
    ElMessage.success(`已加入 ${completed.value.length} 个素材`)
    rows.value = []
    visible.value = false
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '加入素材失败')
  } finally {
    adding.value = false
  }
}

async function beforeClose(done: () => void): Promise<void> {
  if (!uploading.value) {
    done()
    return
  }
  try {
    await ElMessageBox.confirm('还有文件在上传，关闭会中断这次上传。', '确认关闭', {
      type: 'warning',
      confirmButtonText: '关闭',
      cancelButtonText: '继续上传',
    })
    cancelUploadingRows(false)
    done()
  } catch {
    // 继续上传
  }
}

onMounted(() => {
  window.addEventListener('pagehide', onPageHide)
})

onUnmounted(() => {
  window.removeEventListener('pagehide', onPageHide)
  disposed = true
  cancelUploadingRows(false)
})
</script>

<template>
  <el-dialog
    v-model="visible"
    :title="isSource ? sourceTitle : isSlot || isMergeInput ? dialogTitle : '上传素材'"
    width="720px"
    :close-on-click-modal="false"
    :before-close="beforeClose"
  >
    <div
      class="dropzone"
      :class="{ over: dragOver }"
      @dragover.prevent="dragOver = true"
      @dragleave.prevent="dragOver = false"
      @drop.prevent="onDrop"
      @click="fileInput?.click()"
    >
      <el-icon :size="30"><UploadFilled /></el-icon>
      <p>
        把{{ isMergeInput ? '图片（21:9 画布图）' : '视频或图片' }}拖到这里，或点击选择文件
      </p>
      <p class="muted">单文件最大 10 GB；分片 8 MB，失败自动重试三次，刷新后可续传。</p>
      <input
        ref="fileInput"
        type="file"
        :multiple="!singleFile"
        :accept="accept"
        class="file-input"
        @change="onPick"
      />
    </div>

    <div v-if="rows.length" class="upload-list">
      <div v-for="row in rows" :key="row.key" class="upload-row">
        <div class="upload-main">
          <div class="upload-name">{{ row.file.name }}</div>
          <div class="upload-meta">
            <span>{{ formatSize(row.file.size) }}</span>
            <span :class="{ bad: row.status === 'error' }">{{ statusText(row) }}</span>
          </div>
          <el-progress
            :percentage="progressOf(row)"
            :status="
              row.status === 'error' ? 'exception' : row.status === 'done' ? 'success' : undefined
            "
            :stroke-width="6"
          />
        </div>
        <el-button v-if="row.status === 'error'" link type="primary" @click="retry(row)"
          >重试</el-button
        >
        <el-button link type="danger" :icon="Delete" aria-label="移除" @click="removeRow(row)" />
      </div>
    </div>

    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button
        type="primary"
        :loading="adding"
        :disabled="!canAdd"
        @click="confirmAdd"
      >
        {{
          isSource
            ? `${sourceButton}（${completed.length}）`
            : isSlot
              ? `放进槽位（${completed.length}）`
              : isMergeInput
                ? `上传并用作拼接输入（${completed.length}）`
                : `加入素材库（${completed.length}）`
        }}
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.dropzone {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-1);
  padding: var(--space-6);
  border: 1px dashed var(--color-border-strong);
  border-radius: var(--radius-lg);
  background: var(--color-surface-muted);
  color: var(--color-text-muted);
  cursor: pointer;
  transition:
    border-color var(--duration-fast) ease,
    background var(--duration-fast) ease;
}

.dropzone:hover,
.dropzone.over {
  border-color: var(--color-primary);
  background: var(--color-primary-soft);
}

.dropzone p {
  margin: 0;
  font-size: var(--text-sm);
}

.dropzone p.muted {
  font-size: var(--text-xs);
}

.file-input {
  display: none;
}

.upload-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  margin-top: var(--space-4);
}

.upload-row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-2) var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}

.upload-main {
  flex: 1;
  min-width: 0;
}

.upload-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 600;
}

.upload-meta {
  display: flex;
  justify-content: space-between;
  gap: var(--space-2);
  margin: var(--space-1) 0;
  color: var(--color-text-muted);
  font-size: var(--text-xs);
}

.upload-meta .bad {
  color: var(--color-danger);
}
</style>
