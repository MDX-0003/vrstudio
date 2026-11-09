<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Plus, Refresh, Search, UploadFilled } from '@element-plus/icons-vue'
import PageHeader from '@/components/common/PageHeader.vue'
import AssetUploadDialog from '@/components/upload/AssetUploadDialog.vue'
import {
  createCaseRequest,
  deleteCaseRequest,
  fetchCases,
  fetchModes,
  fetchSources,
} from '@/api/cases'
import { flowMeta, flowOptions } from '@/constants/flow'
import { nextStepForFlow } from '@/utils/caseFlow'
import type { CaseBrief, CaseRow, Flow, ModeOption, TaskStatus } from '@/types/case'

const router = useRouter()

const rows = ref<CaseRow[]>([])
const loading = ref(false)
const error = ref('')
const sourceVideos = ref<string[]>([])
const sourceImages = ref<string[]>([])
const modes = ref<ModeOption[]>([])

const IMAGE_FLOWS: Flow[] = ['patchimg', 'fullRedrawImg']

function isImageFlow(flow: Flow): boolean {
  return IMAGE_FLOWS.includes(flow)
}

const keyword = ref('')
const flowFilter = ref<Flow | 'all'>('all')
const ownerFilter = ref('all')
type SortKey = 'activity' | 'name' | 'owner' | 'flow' | 'outputs'
const sortKey = ref<SortKey>('activity')
const sortOrder = ref<'asc' | 'desc'>('desc')

const SORT_OPTIONS: { value: SortKey; label: string }[] = [
  { value: 'activity', label: '最近活动' },
  { value: 'name', label: '名称' },
  { value: 'owner', label: '负责人' },
  { value: 'flow', label: '流程' },
  { value: 'outputs', label: '成片数' },
]

const owners = computed(() => Array.from(new Set(rows.value.map((row) => row.owner))))

const filteredRows = computed(() => {
  const word = keyword.value.trim().toLowerCase()
  return rows.value.filter((row) => {
    const matchesWord = !word || row.name.toLowerCase().includes(word)
    const matchesFlow = flowFilter.value === 'all' || row.flow === flowFilter.value
    const matchesOwner = ownerFilter.value === 'all' || row.owner === ownerFilter.value
    return matchesWord && matchesFlow && matchesOwner
  })
})

function primaryCompare(a: CaseRow, b: CaseRow): number {
  if (sortKey.value === 'activity') return a.updatedTs - b.updatedTs
  if (sortKey.value === 'outputs') return a.outputs - b.outputs
  if (sortKey.value === 'owner') return a.owner.localeCompare(b.owner, 'zh-CN')
  if (sortKey.value === 'flow') {
    return flowInfo(a.flow).label.localeCompare(flowInfo(b.flow).label, 'zh-CN')
  }
  return a.name.localeCompare(b.name, 'zh-CN')
}

/** 排序只影响表格显示，不改 rows 的原始顺序；主键同值时固定按名称升序。 */
const sortedRows = computed(() => {
  const dir = sortOrder.value === 'asc' ? 1 : -1
  return filteredRows.value.slice().sort((a, b) => {
    const primary = primaryCompare(a, b)
    if (primary !== 0) return dir * primary
    return a.name.localeCompare(b.name, 'zh-CN')
  })
})

function taskStatusOf(value: string | null): TaskStatus {
  if (value === 'queued' || value === 'running' || value === 'done' || value === 'failed') {
    return value
  }
  return 'idle'
}

function briefToRow(brief: CaseBrief): CaseRow {
  const next = nextStepForFlow(brief.flow, brief.gates ?? {})
  return {
    name: brief.name,
    flow: brief.flow,
    owner: brief.owner ?? '未归属',
    updatedAt: brief.updated_at ?? '—',
    updatedTs: brief.updated_ts ?? (brief.updated_at ? Date.parse(brief.updated_at) / 1000 : 0),
    taskStatus: taskStatusOf(brief.task_status),
    taskLabel: next.label,
    nextStepKey: next.key,
    outputs: brief.outputs,
  }
}

async function loadCases(): Promise<void> {
  loading.value = true
  error.value = ''
  try {
    const briefs = await fetchCases()
    rows.value = briefs.map(briefToRow)
  } catch (err) {
    error.value = err instanceof Error ? err.message : '读取 case 列表失败'
  } finally {
    loading.value = false
  }
}

const createOpen = ref(false)
const creating = ref(false)
const form = reactive<{ name: string; flow: Flow; mode: string; source: string }>({
  name: '',
  flow: 'patch',
  mode: '',
  source: '',
})
const sourceUploadOpen = ref(false)
const lastUploaded = ref<{ uploadId: string; name: string } | null>(null)

const createImageFlow = computed(() => isImageFlow(form.flow))
/** relight 建 case 不绑源文件：GT 与氛围依据进 case 之后在素材步选定。 */
const createNoSource = computed(() => form.flow === 'gen' || form.flow === 'relight')
/** 弹窗要不要选源文件：gen 与 relight 都不需要。 */
const needsSource = computed(() => !createNoSource.value)
/** 新建弹窗只列当前 flow 能用的源：视频流程看 videos，图片流程看 images。 */
const createSources = computed(() =>
  createImageFlow.value ? sourceImages.value : sourceVideos.value,
)

watch(
  () => form.flow,
  () => {
    form.source = createSources.value[0] ?? ''
  },
)

function onSourceUploaded(file: { uploadId: string; name: string }): void {
  lastUploaded.value = file
  const target = /\.(png|jpe?g|webp)$/i.test(file.name) ? sourceImages : sourceVideos
  if (!target.value.includes(file.name)) target.value = [...target.value, file.name]
  form.source = file.name
}

async function loadCreateOptions(): Promise<void> {
  try {
    const [sourceData, modeData] = await Promise.all([fetchSources(), fetchModes()])
    const files = sourceData.files ?? []
    sourceVideos.value =
      sourceData.videos ?? files.filter((name) => !/\.(png|jpe?g|webp)$/i.test(name))
    sourceImages.value =
      sourceData.images ?? files.filter((name) => /\.(png|jpe?g|webp)$/i.test(name))
    modes.value = modeData.modes ?? []
    form.source = createSources.value[0] ?? ''
    if (!form.mode && modes.value.length) form.mode = modes.value[0].value
  } catch (err) {
    ElMessage.warning(err instanceof Error ? err.message : '读取源文件或模式失败')
  }
}

function openCase(row: CaseRow): void {
  router.push({ name: 'case', params: { name: row.name, step: row.nextStepKey || 'generate' } })
}

async function openCreate(): Promise<void> {
  form.name = ''
  form.flow = 'patch'
  form.mode = ''
  form.source = ''
  createOpen.value = true
  await loadCreateOptions()
}

async function createCase(): Promise<void> {
  const name = form.name.trim()
  if (!name) {
    ElMessage.warning('先给 case 取一个名字')
    return
  }
  if (needsSource.value && !form.source) {
    ElMessage.warning(
      createImageFlow.value ? 'sources/ 里还没有可选图片' : 'sources/ 里还没有可选视频',
    )
    return
  }
  if (form.flow === 'gen' && !form.mode) {
    ElMessage.warning('先选择生成模式')
    return
  }
  creating.value = true
  try {
    const useUpload = lastUploaded.value !== null && form.source === lastUploaded.value.name
    const result = await createCaseRequest({
      flow: form.flow,
      name,
      ...(needsSource.value
        ? useUpload
          ? { upload_id: lastUploaded.value!.uploadId }
          : { video: form.source }
        : form.flow === 'gen'
          ? { mode: form.mode }
          : {}),
    })
    createOpen.value = false
    lastUploaded.value = null
    const source = result.source
    const suffix = source?.reused
      ? `；已复用 sources/ 里内容相同的 ${source.name}`
      : source?.renamed_from
        ? `；${source.renamed_from} 已另存为 ${source.name}`
        : ''
    ElMessage.success(`已创建 ${result.name}${suffix}`)
    await loadCases()
    const next = nextStepForFlow(form.flow, {})
    router.push({ name: 'case', params: { name: result.name, step: next.key } })
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '创建 case 失败')
  } finally {
    creating.value = false
  }
}

function statusClass(status: CaseRow['taskStatus']): string {
  return status === 'idle' ? '' : status
}

const deleting = ref('')

function isTaskActive(row: CaseRow): boolean {
  return row.taskStatus === 'running' || row.taskStatus === 'queued'
}

async function removeCase(row: CaseRow): Promise<void> {
  if (isTaskActive(row)) {
    ElMessage.warning('case 有运行中或排队任务，等任务结束后再删')
    return
  }
  try {
    await ElMessageBox.confirm(
      `将删除 cases/${row.name}/ 与 vrgen_runs/${row.name}/ 下的全部文件，不可恢复。` +
        '源文件若没有其他 case 使用，会移到 sources/.trash/。',
      `删除 case「${row.name}」`,
      {
        type: 'warning',
        confirmButtonText: '删除',
        confirmButtonClass: 'el-button--danger',
        cancelButtonText: '取消',
      },
    )
  } catch {
    return
  }
  deleting.value = row.name
  try {
    const result = await deleteCaseRequest(row.name)
    ElMessage.success(
      result.source_reclaimed
        ? `已删除 ${row.name}；源文件已移入 sources/.trash/`
        : `已删除 ${row.name}`,
    )
    await loadCases()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '删除 case 失败')
  } finally {
    deleting.value = ''
  }
}

function flowInfo(flow: string): (typeof flowMeta)[Flow] {
  return flowMeta[flow as Flow]
}

onMounted(loadCases)
</script>

<template>
  <div class="page">
    <PageHeader
      title="Case 列表"
      subtitle="每个 case 是一段素材加一条流程；点开后会进入当前该做的那一步。"
    >
      <el-button :icon="Refresh" :loading="loading" @click="loadCases">刷新</el-button>
      <el-button type="primary" :icon="Plus" @click="openCreate">新建 case</el-button>
    </PageHeader>

    <div v-if="error" class="load-error">
      <span>{{ error }}</span>
      <el-button link type="primary" @click="loadCases">重试</el-button>
    </div>

    <div class="toolbar">
      <el-input
        v-model="keyword"
        :prefix-icon="Search"
        placeholder="搜索 case 名称"
        clearable
        style="width: 240px"
      />
      <el-select v-model="flowFilter" style="width: 180px">
        <el-option label="全部流程" value="all" />
        <el-option
          v-for="option in flowOptions"
          :key="option.value"
          :label="option.label"
          :value="option.value"
        />
      </el-select>
      <el-select v-model="ownerFilter" style="width: 130px">
        <el-option label="全部负责人" value="all" />
        <el-option v-for="owner in owners" :key="owner" :label="owner" :value="owner" />
      </el-select>
      <el-select v-model="sortKey" style="width: 130px">
        <el-option
          v-for="option in SORT_OPTIONS"
          :key="option.value"
          :label="`按${option.label}`"
          :value="option.value"
        />
      </el-select>
      <el-select v-model="sortOrder" style="width: 96px">
        <el-option label="倒序" value="desc" />
        <el-option label="升序" value="asc" />
      </el-select>
      <span class="spacer" />
      <span class="muted">{{ filteredRows.length }} 个 case</span>
    </div>

    <div class="panel">
      <el-table v-loading="loading" :data="sortedRows" row-key="name" @row-click="openCase">
        <el-table-column label="Case" min-width="180">
          <template #default="{ row }">
            <div class="case-name">{{ row.name }}</div>
            <div class="case-path mono">cases/{{ row.name }}</div>
          </template>
        </el-table-column>
        <el-table-column label="流程" width="130">
          <template #default="{ row }">
            <el-tag :type="flowInfo(row.flow).tag" effect="light">
              {{ flowInfo(row.flow).label }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="owner" label="负责人" width="100" />
        <el-table-column prop="updatedAt" label="最近活动" width="160" />
        <el-table-column label="下一步" min-width="200">
          <template #default="{ row }">
            <span class="task-line">
              <span class="status-dot" :class="statusClass(row.taskStatus)" />
              {{ row.taskLabel }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="可用成片" width="110" align="right">
          <template #default="{ row }">
            <span class="mono">{{ row.outputs }}</span>
          </template>
        </el-table-column>
        <el-table-column label="" width="150" align="right">
          <template #default="{ row }">
            <el-button link type="primary" @click.stop="openCase(row)">打开</el-button>
            <el-button
              link
              type="danger"
              :loading="deleting === row.name"
              :disabled="isTaskActive(row)"
              @click.stop="removeCase(row)"
            >
              删除
            </el-button>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty :description="error ? '读取失败，请点上方重试' : '还没有 case，先新建一个'" />
        </template>
      </el-table>
    </div>

    <el-drawer v-model="createOpen" title="新建 case" size="420px">
      <el-form label-position="top">
        <el-form-item label="Case 名称" required>
          <el-input v-model="form.name" placeholder="例如 dacne、walk_4k" />
        </el-form-item>
        <el-form-item label="流程">
          <el-radio-group v-model="form.flow">
            <el-radio-button value="patch">视频·局部重绘</el-radio-button>
            <el-radio-button value="fullredraw">视频·全画幅重绘</el-radio-button>
            <el-radio-button value="gen">简单生成</el-radio-button>
            <el-radio-button value="patchimg">图片·局部重绘</el-radio-button>
            <el-radio-button value="fullRedrawImg">图片·全幅重绘</el-radio-button>
            <el-radio-button value="relight">重打光</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="needsSource" :label="createImageFlow ? '源图片' : '源视频'" required>
          <div class="source-row">
            <el-select
              v-model="form.source"
              :placeholder="createImageFlow ? '选择 sources/ 里的图片' : '选择 sources/ 里的视频'"
              style="flex: 1"
              :disabled="!createSources.length"
            >
              <el-option v-for="file in createSources" :key="file" :label="file" :value="file" />
            </el-select>
            <el-button :icon="UploadFilled" @click="sourceUploadOpen = true">
              {{ createImageFlow ? '上传图片' : '上传视频' }}
            </el-button>
          </div>
          <div v-if="!createSources.length" class="field-hint">
            sources/ 目录里还没有{{ createImageFlow ? '图片（2:1 ERP）' : '视频' }}；可点击「{{
              createImageFlow ? '上传图片' : '上传视频'
            }}」直接上传本机文件。
          </div>
        </el-form-item>
        <el-form-item v-else-if="form.flow === 'relight'" label="下一步">
          <div class="field-hint">
            重打光 case 建完进入「素材」步：在那里选定底版（视频或图片）与氛围依据（图片或视频），
            再运行重打光。建 case 这一步不绑任何源文件。
          </div>
        </el-form-item>
        <el-form-item v-else label="生成模式" required>
          <el-select
            v-model="form.mode"
            placeholder="选择生成模式"
            style="width: 100%"
            :disabled="!modes.length"
          >
            <el-option
              v-for="mode in modes"
              :key="mode.value"
              :label="mode.label || mode.value"
              :value="mode.value"
            />
          </el-select>
          <div v-if="!modes.length" class="field-hint">模型能力里没有可选模式。</div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createOpen = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="createCase">创建</el-button>
      </template>
    </el-drawer>

    <AssetUploadDialog
      v-model="sourceUploadOpen"
      destination="source"
      :source-kind="createImageFlow ? 'image' : 'video'"
      @uploaded="onSourceUploaded"
    />
  </div>
</template>

<style scoped>
.case-name {
  font-weight: 600;
}

.case-path {
  margin-top: 2px;
  color: var(--color-text-faint);
  font-size: var(--text-xs);
}

.task-line {
  display: inline-flex;
  align-items: center;
}

.field-hint {
  margin-top: var(--space-1);
  color: var(--color-text-muted);
  font-size: var(--text-xs);
}

.source-row {
  display: flex;
  gap: var(--space-2);
  width: 100%;
  align-items: center;
}

:deep(.el-table__row) {
  cursor: pointer;
}
</style>
