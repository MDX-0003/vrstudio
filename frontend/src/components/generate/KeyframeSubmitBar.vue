<script setup lang="ts">
import SessionQuotaRow from '@/components/generate/SessionQuotaRow.vue'
import SubmitBar from '@/components/common/SubmitBar.vue'
import { KEYFRAME_MAX_CANDIDATES, KEYFRAME_RESOLUTIONS } from '@/constants/keyframe'
import type { ModelOption } from '@/types/case-info'
import type { EpisodeOption, ProjectOption, SessionOption } from '@/types/session'

const resolution = defineModel<string>('resolution', { required: true })
const num = defineModel<number>('num', { required: true })
const imageModelId = defineModel<number | null>('imageModelId', { required: true })
const selectedProject = defineModel<string>('selectedProject', { required: true })
const selectedEpisode = defineModel<string>('selectedEpisode', { required: true })
const selectedSession = defineModel<string>('selectedSession', { required: true })

defineProps<{
  step: 'first' | 'last'
  imageModelOptions: ModelOption[]
  imageModelsError: string
  modelSaving: boolean
  projects: ProjectOption[]
  episodes: EpisodeOption[]
  sessions: SessionOption[]
  sessionsLoading: boolean
  sessionError: string
  paramsSaving: boolean
  lastRefreshed: string
  status: string
  statusTone: string
  running: boolean
  canRun: boolean
  runReason: string
}>()

const emit = defineEmits<{
  'model-change': [value: number | null]
  run: []
  'refresh-sessions': []
}>()

const NUM_OPTIONS = Array.from({ length: KEYFRAME_MAX_CANDIDATES }, (_, index) => index + 1)
</script>

<template>
  <SubmitBar>
    <div class="submit-row">
      <label class="ctl">
        <span>图片模型</span>
        <el-select
          v-model="imageModelId"
          size="small"
          class="kf-image-model"
          placeholder="选择图片模型"
          :loading="modelSaving"
          :title="imageModelsError || '首尾帧两步共用这一个图片模型，切换后写入 generation.toml'"
          @change="(value: number) => emit('model-change', value)"
        >
          <el-option
            v-for="item in imageModelOptions"
            :key="item.id"
            :label="item.name"
            :value="item.id"
            :title="`${item.name}（#${item.id}）${item.description ? '：' + item.description : ''}`"
          />
          <el-option v-if="!imageModelOptions.length" label="清单不可用" :value="-1" disabled />
        </el-select>
      </label>
      <label class="ctl">
        <span>分辨率</span>
        <el-select v-model="resolution" size="small" class="w-resolution">
          <el-option
            v-for="item in KEYFRAME_RESOLUTIONS"
            :key="item.value"
            :label="item.label"
            :value="item.value"
          />
        </el-select>
      </label>
      <label class="ctl">
        <span>候选张数</span>
        <el-select v-model="num" size="small" class="w-num">
          <el-option v-for="item in NUM_OPTIONS" :key="item" :label="`${item} 张`" :value="item" />
        </el-select>
      </label>
      <span class="run-state bar-status">
        <span class="status-dot" :class="statusTone" />
        {{ status }}
      </span>
    </div>
    <div class="submit-row">
      <SessionQuotaRow
        v-model:selected-project="selectedProject"
        v-model:selected-episode="selectedEpisode"
        v-model:selected-session="selectedSession"
        :projects="projects"
        :episodes="episodes"
        :sessions="sessions"
        :loading="sessionsLoading"
        :error="sessionError"
        :last-refreshed="lastRefreshed"
        :saving="paramsSaving"
        @refresh="emit('refresh-sessions')"
      />
      <div class="submit-actions">
        <el-button
          type="primary"
          size="small"
          :loading="running"
          :disabled="!canRun"
          @click="emit('run')"
        >
          {{ step === 'first' ? '生成首帧候选' : '生成尾帧候选' }}
        </el-button>
      </div>
    </div>
    <div v-if="runReason" class="gate-reason">生成条件未满足：{{ runReason }}</div>
  </SubmitBar>
</template>

<style scoped>
.kf-image-model {
  width: 190px;
}

.w-resolution {
  width: 90px;
}

.w-num {
  width: 90px;
}

.bar-status {
  margin-left: auto;
}
</style>
