<script setup lang="ts">
import { computed, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Delete, Search, Upload } from '@element-plus/icons-vue'
import { removeAsset } from '@/api/case'
import AssetUploadDialog from '@/components/upload/AssetUploadDialog.vue'
import FileOpenLink from '@/components/common/FileOpenLink.vue'
import FileRow from '@/components/common/FileRow.vue'
import type { AssetDetail, CaseInfo, FileInfo } from '@/types/case-info'

const props = defineProps<{ caseName: string; info: CaseInfo }>()
const emit = defineEmits<{ updated: [data: CaseInfo] }>()

const roleLabels = computed<Record<string, string>>(() =>
  Object.fromEntries((props.info.roles ?? []).map((role) => [role.name, role.label])),
)

const search = ref('')
const kind = ref<'all' | 'image' | 'video'>('all')
const usageFilter = ref<'all' | 'referenced' | 'unreferenced'>('all')
const sort = ref<'id' | 'name' | 'size'>('id')
const uploadOpen = ref(false)
const usageOpen = ref(false)
const usageAsset = ref<AssetDetail | null>(null)

interface UsageRow {
  step: string
  role: string
}

function fileInfo(asset: AssetDetail): FileInfo {
  return {
    name: asset.name,
    rel: asset.rel,
    path: asset.path,
    exists: asset.exists,
    mtime: asset.mtime,
    size: asset.size,
    open_url: asset.open_url,
  }
}

function usageList(asset: AssetDetail): UsageRow[] {
  const rows: UsageRow[] = []
  const video = props.info.submit_entries?.find((item) => item.asset === asset.id)
  if (video) {
    rows.push({ step: '视频提交', role: roleLabels.value[video.role] ?? video.role })
  }
  for (const ref of props.info.keyframe.refs ?? []) {
    if (ref.asset !== asset.id) continue
    rows.push({
      step: '图片参考',
      role: ref.role === 'subject_ref' ? '人物三视图' : '细节参考',
    })
  }
  if (props.info.keyframe.first?.asset === asset.id) {
    rows.push({ step: '首尾帧定稿', role: '首帧定稿' })
  }
  if (props.info.keyframe.last?.asset === asset.id) {
    rows.push({ step: '首尾帧定稿', role: '尾帧定稿' })
  }
  return rows
}

function usageCount(asset: AssetDetail): number {
  return usageList(asset).length
}

const assets = computed(() =>
  (props.info.assets ?? []).filter((asset) => asset.source !== 'flow'),
)

// 流程产物（fullredraw 的 pad、patch 的 clip）以 source === 'flow' 标记，后端按当前
// 流程的 manifest 现算；前端不判断 flow 的名字，有就显示。
const flowProducts = computed(() =>
  (props.info.assets ?? []).filter((asset) => asset.source === 'flow'),
)

const imageCount = computed(() => assets.value.filter((item) => item.kind === 'image').length)
const videoCount = computed(() => assets.value.filter((item) => item.kind === 'video').length)
const usedCount = computed(() =>
  [...assets.value, ...flowProducts.value].filter((item) => usageCount(item)).length,
)

// 源视频与流程产物都不写进 [[assets]]（见 docs/case-model.md 的说明），但它们属于
// 这个 case 的文件。两者都按后端给的事实派生：源视频看 source_file，流程母版看
// asset.source === 'flow'，前端不判断 flow 的名字，pad/clip 走同一条路。
interface FlowRow {
  key: string
  tag: string
  entry: FileInfo
  note: string
}

const flowRows = computed<FlowRow[]>(() => {
  const rows: FlowRow[] = []
  if (props.info.source_file) {
    rows.push({
      key: 'source',
      tag: '源视频',
      entry: props.info.source_file,
      note: '流程起点，不作为生成素材',
    })
  }
  for (const asset of flowProducts.value) {
    rows.push({
      key: asset.id,
      tag: '母版',
      entry: fileInfo(asset),
      note: usageCount(asset) ? '已加入本次提交' : '未加入本次提交（在生成页添加）',
    })
  }
  return rows
})

const emptyText = computed(() => {
  if (assets.value.length) return '没有符合筛选条件的素材'
  return flowRows.value.length
    ? '这个 case 还没有素材文件。上方的源视频与母版是流程文件，不作为生成素材；需要参考图或参考视频时点「上传素材」。'
    : '这个 case 还没有素材文件，点「上传素材」添加。'
})

const visibleAssets = computed(() => {
  const query = search.value.trim().toLowerCase()
  const rows = assets.value.filter((asset) => {
    if (kind.value !== 'all' && asset.kind !== kind.value) return false
    const count = usageCount(asset)
    if (usageFilter.value === 'referenced' && !count) return false
    if (usageFilter.value === 'unreferenced' && count) return false
    if (query) {
      const text = `${asset.id} ${asset.name} ${asset.label}`.toLowerCase()
      if (!text.includes(query)) return false
    }
    return true
  })
  return [...rows].sort((left, right) => {
    if (sort.value === 'name') return left.name.localeCompare(right.name)
    if (sort.value === 'size') return (right.size ?? 0) - (left.size ?? 0)
    return left.id.localeCompare(right.id, undefined, { numeric: true })
  })
})

const usageRows = computed(() => (usageAsset.value ? usageList(usageAsset.value) : []))

function assetTitle(asset: AssetDetail): string {
  return `${asset.name} · ${asset.id}`
}

function openUsage(asset: AssetDetail): void {
  usageAsset.value = asset
  usageOpen.value = true
}

async function deleteAsset(asset: AssetDetail): Promise<void> {
  const rows = usageList(asset)
  const detail = rows.length
    ? `以下步骤正在引用：\n${rows.map((row) => `${row.step} · ${row.role}`).join('\n')}\n\n删除后会同步清理这些引用。`
    : '这个素材没有被任何步骤引用，删除不会影响流程。'
  try {
    await ElMessageBox.confirm(detail, `删除「${asset.name}」？`, {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
    const data = await removeAsset(props.caseName, asset.id)
    emit('updated', data)
    ElMessage.success('素材已删除')
  } catch (err) {
    if (err === 'cancel' || err === 'close') return
    ElMessage.error(err instanceof Error ? err.message : '删除素材失败')
  }
}

function onUploaded(data: CaseInfo): void {
  emit('updated', data)
  uploadOpen.value = false
}

function sizeText(size: number | null): string {
  if (!size) return '—'
  const mb = size / 1024 / 1024
  return mb >= 1 ? `${mb.toFixed(1)} MB` : `${Math.round(size / 1024)} KB`
}
</script>

<template>
  <div class="library-page">
    <div class="library-head">
      <div>
        <h2>素材库</h2>
        <p class="muted">
          case 级文件集合。这里只存文件；角色由各步骤的引用清单决定，同一素材可以被多处引用。
        </p>
      </div>
      <div class="library-head-actions">
        <span class="muted">
          素材 {{ assets.length }} · 图片 {{ imageCount }} · 视频 {{ videoCount }} · 流程文件
          {{ flowRows.length }} · 被引用 {{ usedCount }}
        </span>
        <el-button type="primary" :icon="Upload" @click="uploadOpen = true">上传素材</el-button>
      </div>
    </div>

    <div v-if="flowRows.length" class="library-flow">
      <div class="library-flow-head">
        <h3>流程文件</h3>
        <p class="muted">
          源视频是流程起点，母版由流程生成（patch 的 clip、fullredraw 的 pad）；两者都不是素材文件，引用由生成页的提交清单决定，这里不能删除或改角色。
        </p>
      </div>
      <div v-for="row in flowRows" :key="row.key" class="library-flow-row">
        <el-tag size="small" effect="plain" class="library-flow-tag">{{ row.tag }}</el-tag>
        <FileRow :entry="row.entry" class="library-flow-file" />
        <span class="muted library-flow-note">{{ row.note }}</span>
      </div>
    </div>

    <div class="library-toolbar">
      <el-input v-model="search" size="small" :prefix-icon="Search" placeholder="搜索 id 或文件名" clearable />
      <el-select v-model="kind" size="small" style="width: 120px">
        <el-option label="全部类型" value="all" />
        <el-option label="图片" value="image" />
        <el-option label="视频" value="video" />
      </el-select>
      <el-select v-model="usageFilter" size="small" style="width: 140px">
        <el-option label="全部引用状态" value="all" />
        <el-option label="被引用" value="referenced" />
        <el-option label="未引用" value="unreferenced" />
      </el-select>
      <el-select v-model="sort" size="small" style="width: 110px">
        <el-option label="按 id" value="id" />
        <el-option label="按文件名" value="name" />
        <el-option label="按大小" value="size" />
      </el-select>
      <span class="spacer" />
      <span class="muted library-note">库角色已收敛到步骤引用清单；这里没有角色下拉</span>
    </div>

    <div class="library-table">
      <div class="library-row library-header">
        <span>缩略图</span>
        <span>文件名与 id</span>
        <span>文件类型</span>
        <span>尺寸 / 大小</span>
        <span>引用</span>
        <span class="right">操作</span>
      </div>
      <div v-if="!visibleAssets.length" class="library-empty">
        <el-empty :description="emptyText" :image-size="64" />
      </div>
      <div v-for="asset in visibleAssets" :key="asset.id" class="library-row">
        <div class="library-thumb">
          <img v-if="asset.url && asset.exists" :src="asset.url" alt="" />
          <el-icon v-else :size="18"><Delete /></el-icon>
        </div>
        <div class="library-main">
          <div class="library-name">{{ asset.name }}</div>
          <div class="library-sub mono">{{ asset.id }}</div>
        </div>
        <div>
          <el-tag size="small" effect="light">{{ asset.kind === 'image' ? '图片' : '视频' }}</el-tag>
        </div>
        <div class="muted library-meta">
          {{ asset.width && asset.height ? `${asset.width}×${asset.height}` : '—' }}
          <br />
          {{ sizeText(asset.size) }}
        </div>
        <div>
          <el-button link type="primary" size="small" @click="openUsage(asset)">
            {{ usageCount(asset) ? `${usageCount(asset)} 处` : '未引用' }}
          </el-button>
        </div>
        <div class="library-actions">
          <FileOpenLink :file="fileInfo(asset)" label="打开" />
          <el-button
            link
            type="danger"
            size="small"
            :icon="Delete"
            aria-label="删除素材"
            @click="deleteAsset(asset)"
          />
        </div>
      </div>
    </div>
    <p class="muted library-foot">
      删除正在被引用的文件时，确认框会列出引用它的步骤与角色，并在删除后同步清理视频提交清单、图片参考清单与首尾帧定稿指针。
    </p>

    <el-dialog v-model="usageOpen" title="引用明细" width="min(440px, 92vw)">
      <p class="muted">{{ usageAsset ? assetTitle(usageAsset) : '' }}</p>
      <div v-if="usageRows.length" class="usage-list">
        <div v-for="(row, index) in usageRows" :key="index" class="usage-row">
          <el-tag size="small" effect="light">{{ row.step }}</el-tag>
          <span>{{ row.role }}</span>
        </div>
      </div>
      <el-empty v-else description="没有被任何步骤引用" :image-size="56" />
    </el-dialog>

    <AssetUploadDialog v-model="uploadOpen" :case-name="caseName" @added="onUploaded" />
  </div>
</template>

<style scoped>
.library-page {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.library-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-4);
}

.library-head h2 {
  margin: 0;
  font-size: var(--text-xl);
}

.library-head p {
  margin: var(--space-1) 0 0;
  font-size: var(--text-sm);
}

.library-head-actions {
  display: inline-flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--text-xs);
}

.library-toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
}

.library-flow {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-3);
  border: 1px dashed var(--color-border);
  border-radius: var(--radius-lg);
}

.library-flow-head h3 {
  margin: 0;
  font-size: var(--text-sm);
}

.library-flow-head p {
  margin: var(--space-1) 0 0;
  font-size: var(--text-xs);
}

.library-flow-row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.library-flow-tag {
  flex: none;
}

.library-flow-note {
  flex: none;
  font-size: var(--text-xs);
}

.library-flow-file {
  flex: 1;
  min-width: 0;
}

.library-note {
  font-size: var(--text-xs);
}

.library-table {
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  background: var(--color-surface);
  overflow-x: auto;
}

.library-row {
  display: grid;
  grid-template-columns: 56px minmax(220px, 2fr) 90px 130px 80px 100px;
  gap: var(--space-3);
  align-items: center;
  min-width: 860px;
  padding: var(--space-2) var(--space-3);
  border-bottom: 1px solid var(--color-border);
  font-size: var(--text-sm);
}

.library-row:last-child {
  border-bottom: 0;
}

.library-header {
  background: var(--color-surface-muted);
  color: var(--color-text-muted);
  font-size: var(--text-xs);
  font-weight: 600;
}

.library-thumb {
  display: grid;
  place-items: center;
  width: 56px;
  height: 40px;
  overflow: hidden;
  border-radius: var(--radius-sm);
  background: var(--color-video-bg);
  color: var(--color-text-faint);
}

.library-thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.library-main {
  min-width: 0;
}

.library-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 600;
}

.library-sub {
  color: var(--color-text-muted);
  font-size: 11px;
}

.library-meta {
  font-size: var(--text-xs);
}

.library-actions {
  display: inline-flex;
  align-items: center;
  justify-content: flex-end;
  gap: var(--space-1);
}

.library-empty {
  padding: var(--space-4);
}

.library-foot {
  margin: 0;
  font-size: var(--text-xs);
  line-height: 1.6;
}

.usage-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.usage-row {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  font-size: var(--text-sm);
}

.spacer {
  flex: 1;
}

.right {
  text-align: right;
}
</style>
