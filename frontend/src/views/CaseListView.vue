<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Plus, Refresh, Search } from '@element-plus/icons-vue'
import PageHeader from '@/components/common/PageHeader.vue'
import { createCaseRequest, fetchCases, fetchModes, fetchSources } from '@/api/cases'
import { flowMeta } from '@/constants/flow'
import type { CaseBrief, CaseRow, Flow, ModeOption } from '@/types/case'

const router = useRouter()

const rows = ref<CaseRow[]>([])
const loading = ref(false)
const error = ref('')
const sources = ref<string[]>([])
const modes = ref<ModeOption[]>([])

const keyword = ref('')
const flowFilter = ref<Flow | 'all'>('all')
const ownerFilter = ref('all')

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

function nextStepLabel(brief: CaseBrief): string {
  const gates = brief.gates ?? {}
  if (brief.flow === 'patch') {
    if (!gates.pick?.ok) return '等待选区'
    if (!gates.extract?.ok) return '等待抽取'
    if (!gates.generate?.ok) return '等待生成'
    if (!gates.merge?.ok) return '等待合并'
    return '等待对比'
  }
  if (brief.flow === 'fullredraw') {
    if (!gates.pad?.ok) return '等待扩充画面'
    if (!gates.generate?.ok) return '等待生成'
    if (!gates.recompose?.ok) return '等待裁回尺寸'
    return '等待对比'
  }
  return gates.generate?.ok ? '可以提交' : '等待生成'
}

function briefToRow(brief: CaseBrief): CaseRow {
  return {
    name: brief.name,
    flow: brief.flow,
    owner: brief.owner ?? '未归属',
    updatedAt: brief.updated_at ?? '—',
    taskStatus: 'idle',
    taskLabel: nextStepLabel(brief),
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

async function loadCreateOptions(): Promise<void> {
  try {
    const [sourceData, modeData] = await Promise.all([fetchSources(), fetchModes()])
    sources.value = sourceData.files ?? []
    modes.value = modeData.modes ?? []
    if (!form.source && sources.value.length) form.source = sources.value[0]
    if (!form.mode && modes.value.length) form.mode = modes.value[0].value
  } catch (err) {
    ElMessage.warning(err instanceof Error ? err.message : '读取源视频或模式失败')
  }
}

function openCase(row: CaseRow): void {
  router.push({ name: 'case', params: { name: row.name, step: 'generate' } })
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
  if (form.flow !== 'gen' && !form.source) {
    ElMessage.warning('sources/ 里还没有可选视频')
    return
  }
  if (form.flow === 'gen' && !form.mode) {
    ElMessage.warning('先选择生成模式')
    return
  }
  creating.value = true
  try {
    const result = await createCaseRequest({
      flow: form.flow,
      name,
      ...(form.flow === 'gen' ? { mode: form.mode } : { video: form.source }),
    })
    createOpen.value = false
    ElMessage.success(`已创建 ${result.name}`)
    await loadCases()
    router.push({ name: 'case', params: { name: result.name, step: 'generate' } })
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '创建 case 失败')
  } finally {
    creating.value = false
  }
}

function statusClass(status: CaseRow['taskStatus']): string {
  return status === 'idle' ? '' : status
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
      subtitle="每个 case 是一段素材加一条流程。列表读取真实 /api/cases，任务与负责人字段等 Phase 4 接入。"
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
      <el-select v-model="flowFilter" style="width: 150px">
        <el-option label="全部流程" value="all" />
        <el-option label="局部重绘" value="patch" />
        <el-option label="全流程重绘" value="fullredraw" />
        <el-option label="简单生成" value="gen" />
      </el-select>
      <el-select v-model="ownerFilter" style="width: 130px">
        <el-option label="全部负责人" value="all" />
        <el-option v-for="owner in owners" :key="owner" :label="owner" :value="owner" />
      </el-select>
      <span class="spacer" />
      <span class="muted">{{ filteredRows.length }} 个 case</span>
    </div>

    <div class="panel">
      <el-table
        v-loading="loading"
        :data="filteredRows"
        row-key="name"
        @row-click="openCase"
      >
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
        <el-table-column label="" width="90" align="right">
          <template #default="{ row }">
            <el-button link type="primary" @click.stop="openCase(row)">打开</el-button>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty
            :description="error ? '读取失败，请点上方重试' : '还没有 case，先新建一个'"
          />
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
            <el-radio-button value="patch">局部重绘</el-radio-button>
            <el-radio-button value="fullredraw">全流程重绘</el-radio-button>
            <el-radio-button value="gen">简单生成</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="form.flow !== 'gen'" label="源视频" required>
          <el-select
            v-model="form.source"
            placeholder="选择 sources/ 里的视频"
            style="width: 100%"
            :disabled="!sources.length"
          >
            <el-option v-for="file in sources" :key="file" :label="file" :value="file" />
          </el-select>
          <div v-if="!sources.length" class="field-hint">
            sources/ 目录里还没有视频。
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
          <div v-if="!modes.length" class="field-hint">
            模型能力里没有可选模式。
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createOpen = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="createCase">创建</el-button>
      </template>
    </el-drawer>
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

.load-error {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-4);
  margin-bottom: var(--space-4);
  border: 1px solid var(--color-danger);
  border-radius: var(--radius-md);
  background: var(--color-danger-soft);
  color: var(--color-danger);
}

.field-hint {
  margin-top: var(--space-1);
  color: var(--color-text-muted);
  font-size: var(--text-xs);
}

:deep(.el-table__row) {
  cursor: pointer;
}
</style>
