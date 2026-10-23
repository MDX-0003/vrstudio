<script setup lang="ts">
import { ref } from 'vue'
import PageHeader from '@/components/common/PageHeader.vue'

interface TaskRow {
  kind: string
  case: string
  owner: string
  status: 'running' | 'queued' | 'done' | 'failed'
  position: string
  started: string
}

const tasks = ref<TaskRow[]>([
  { kind: '空间合并', case: 'dacne', owner: 'MDX', status: 'running', position: '运行中', started: '16:38' },
  { kind: '抽取', case: 'walk_02', owner: 'LQ', status: 'queued', position: '第 2 位', started: '—' },
  { kind: '提交生成', case: 'ship_01', owner: 'MDX', status: 'failed', position: '—', started: '15:02' },
  { kind: '首尾帧图片', case: 'walk_4k', owner: 'MDX', status: 'done', position: '—', started: '14:41' },
])

const statusText: Record<TaskRow['status'], string> = {
  running: '进行中',
  queued: '排队中',
  done: '已完成',
  failed: '失败',
}

const statusType: Record<TaskRow['status'], 'primary' | 'warning' | 'success' | 'danger'> = {
  running: 'primary',
  queued: 'warning',
  done: 'success',
  failed: 'danger',
}

function statusTextOf(status: string): string {
  return statusText[status as TaskRow['status']]
}

function statusTypeOf(status: string): 'primary' | 'warning' | 'success' | 'danger' {
  return statusType[status as TaskRow['status']]
}
</script>

<template>
  <div class="page">
    <PageHeader
      title="任务中心"
      subtitle="原型阶段展示任务视图的字段；Phase 1 接入 /api/tasks，Phase 4 增加我的/全部筛选。"
    />
    <div class="panel">
      <el-table :data="tasks" row-key="kind">
        <el-table-column prop="kind" label="任务" width="150" />
        <el-table-column prop="case" label="Case" width="150" />
        <el-table-column prop="owner" label="负责人" width="100" />
        <el-table-column label="状态" width="110">
          <template #default="{ row }">
            <el-tag :type="statusTypeOf(row.status)" effect="light" size="small">
              {{ statusTextOf(row.status) }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="position" label="排队位置" width="110" />
        <el-table-column prop="started" label="开始时间" width="110" />
        <el-table-column label="" min-width="120" align="right">
          <template #default>
            <el-button link type="primary">查看日志</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>
  </div>
</template>
