<script setup lang="ts">
import { computed, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Plus, Search } from '@element-plus/icons-vue'
import PageHeader from '@/components/common/PageHeader.vue'
import { flowMeta, mockCases } from '@/mocks/case'
import type { CaseRow, Flow } from '@/types/case'

const router = useRouter()

const rows = ref<CaseRow[]>(mockCases.map((item) => ({ ...item })))
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

const createOpen = ref(false)
const form = reactive<{ name: string; flow: Flow; mode: string; source: string }>({
  name: '',
  flow: 'patch',
  mode: 'videoEdit',
  source: 'Mono_dance_4k.mp4',
})

function openCase(row: CaseRow): void {
  router.push({ name: 'case', params: { name: row.name, step: 'generate' } })
}

function openCreate(): void {
  form.name = ''
  form.flow = 'patch'
  form.mode = 'videoEdit'
  form.source = 'Mono_dance_4k.mp4'
  createOpen.value = true
}

function createCase(): void {
  const name = form.name.trim()
  if (!name) {
    ElMessage.warning('先给 case 取一个名字')
    return
  }
  if (rows.value.some((row) => row.name === name)) {
    ElMessage.warning('已经有同名 case')
    return
  }
  rows.value.unshift({
    name,
    flow: form.flow,
    owner: 'MDX',
    updatedAt: '刚刚',
    taskStatus: 'idle',
    taskLabel: '等待下一步',
    outputs: 0,
  })
  createOpen.value = false
  ElMessage.success(`已创建 ${name}，原型阶段只写入页面`)
}

function statusClass(status: CaseRow['taskStatus']): string {
  return status === 'idle' ? '' : status
}

function flowInfo(flow: string): (typeof flowMeta)[Flow] {
  return flowMeta[flow as Flow]
}
</script>

<template>
  <div class="page">
    <PageHeader
      title="Case 列表"
      subtitle="每个 case 是一段素材加一条流程。原型阶段使用页面内数据，Phase 1 接入真实接口。"
    >
      <el-button type="primary" :icon="Plus" @click="openCreate">新建 case</el-button>
    </PageHeader>

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
      <el-table :data="filteredRows" row-key="name" @row-click="openCase">
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
        <el-table-column label="当前任务" min-width="200">
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
          <el-empty description="没有匹配的 case" />
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
        <el-form-item v-if="form.flow !== 'gen'" label="源视频">
          <el-select v-model="form.source" placeholder="选择源视频" style="width: 100%">
            <el-option label="Mono_dance_4k.mp4" value="Mono_dance_4k.mp4" />
            <el-option label="ship_ue.mp4" value="ship_ue.mp4" />
          </el-select>
        </el-form-item>
        <el-form-item v-else label="生成模式">
          <el-select v-model="form.mode" style="width: 100%">
            <el-option label="VideoEdit · 结构锁定编辑" value="videoEdit" />
            <el-option label="allMediaRef · 多素材参考" value="allMediaRef" />
            <el-option label="firstLastFrame · 首尾帧" value="firstLastFrame" />
          </el-select>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createOpen = false">取消</el-button>
        <el-button type="primary" @click="createCase">创建</el-button>
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

:deep(.el-table__row) {
  cursor: pointer;
}
</style>
