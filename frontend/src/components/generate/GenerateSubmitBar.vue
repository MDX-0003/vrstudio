<script setup lang="ts">
import SessionQuotaRow from '@/components/generate/SessionQuotaRow.vue'
import SubmitBar from '@/components/common/SubmitBar.vue'
import type { InputOption, ModeInfo, ModelOption, OutputOption } from '@/types/case-info'
import type { EpisodeOption, ProjectOption, SessionOption } from '@/types/session'

const mode = defineModel<string>('mode', { required: true })
const ratio = defineModel<string>('ratio', { required: true })
const resolution = defineModel<string>('resolution', { required: true })
const scale = defineModel<string>('scale', { required: true })
const duration = defineModel<string>('duration', { required: true })
const audio = defineModel<'keep' | 'none'>('audio', { required: true })
const videoModelId = defineModel<number | null>('videoModelId', { required: true })
const selectedProject = defineModel<string>('selectedProject', { required: true })
const selectedEpisode = defineModel<string>('selectedEpisode', { required: true })
const selectedSession = defineModel<string>('selectedSession', { required: true })
/** 一次性开关：勾上再提交会另开一轮并重新计费，提交后由页面清空。 */
const resubmit = defineModel<boolean>('resubmit', { required: true })

defineProps<{
  modeOptions: ModeInfo[]
  ratioOptions: string[]
  resolutionOptions: OutputOption[]
  scaleOptions: InputOption[]
  videoModelOptions: ModelOption[]
  durationOptions: { value: string; label: string }[]
  projects: ProjectOption[]
  episodes: EpisodeOption[]
  sessions: SessionOption[]
  sessionsLoading: boolean
  sessionError: string
  paramsError: string
  paramsSaving: boolean
  lastRefreshed: string
  status: string
  statusDetail: string
  tone: string
  running: boolean
  activeKind: 'dry_run' | 'submit' | 'adopt' | null
  runBlockReason: string
}>()

const emit = defineEmits<{
  'refresh-sessions': []
  'dry-run': []
  submit: []
}>()

function ratioLabel(value: string): string {
  return value === 'auto' ? '跟随输入' : value
}

function scaleLabel(option: InputOption): string {
  const base =
    option.value === 'auto'
      ? '自动'
      : option.value === 'full'
        ? '原始尺寸'
        : option.value === 'half'
          ? '一半'
          : option.value
  const size = option.width && option.height ? `${option.width}×${option.height}` : ''
  if (!option.ok) {
    return `${base}${size ? `（${size}）` : ''}：${option.reason || '不可用'}`
  }
  return size ? `${base}（${size}）` : base
}
</script>

<template>
  <SubmitBar>
    <div class="submit-row">
      <label class="ctl">
        <span>视频模型</span>
        <el-select
          v-model="videoModelId"
          size="small"
          class="w-video-model"
          placeholder="选择视频模型"
        >
          <el-option
            v-for="item in videoModelOptions"
            :key="item.id"
            :label="item.name"
            :value="item.id"
            :title="`${item.name}（#${item.id}）${item.description ? '：' + item.description : ''}`"
          />
          <el-option v-if="!videoModelOptions.length" label="清单不可用" :value="-1" disabled />
        </el-select>
      </label>
      <label class="ctl">
        <span>模式</span>
        <el-select v-model="mode" size="small" class="w-mode">
          <el-option
            v-for="item in modeOptions"
            :key="item.value"
            :label="item.label"
            :value="item.value"
          />
          <el-option v-if="!modeOptions.length" label="暂无模式" value="" disabled />
        </el-select>
      </label>
      <label class="ctl">
        <span>比例</span>
        <el-select v-model="ratio" size="small" class="w-ratio">
          <el-option
            v-for="value in ratioOptions"
            :key="value"
            :label="ratioLabel(value)"
            :value="value"
          />
        </el-select>
      </label>
      <label class="ctl">
        <span>输出分辨率</span>
        <el-select v-model="resolution" size="small" class="w-resolution">
          <el-option
            v-for="item in resolutionOptions"
            :key="item.value"
            :label="`${item.value}（${item.width}×${item.height}）`"
            :value="item.value"
          />
        </el-select>
      </label>
      <label class="ctl">
        <span>输入分辨率</span>
        <el-select v-model="scale" size="small" class="w-scale">
          <el-option
            v-for="item in scaleOptions"
            :key="item.value"
            :label="scaleLabel(item)"
            :value="item.value"
            :disabled="!item.ok"
          />
        </el-select>
      </label>
      <span v-if="paramsError" class="session-error">{{ paramsError }}</span>
    </div>
    <div class="submit-row">
      <label class="ctl">
        <span>时长</span>
        <el-select v-model="duration" size="small" class="w-duration">
          <el-option
            v-for="item in durationOptions"
            :key="item.value"
            :label="item.label"
            :value="item.value"
          />
        </el-select>
      </label>
      <label class="ctl" title="平台没有「不生成声音」的提交参数；选关闭时在结果下载后去掉音轨">
        <span>声音</span>
        <el-select v-model="audio" size="small" class="w-audio">
          <el-option label="开启（保留音轨）" value="keep" />
          <el-option label="关闭（下载后去音轨）" value="none" />
        </el-select>
      </label>
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
        <span class="run-state">
          <span class="status-dot" :class="tone" />
          {{ status }}
          <span v-if="statusDetail" class="run-detail">{{ statusDetail }}</span>
        </span>
        <el-button
          size="small"
          :loading="activeKind === 'dry_run'"
          :disabled="(running && activeKind !== 'dry_run') || Boolean(runBlockReason)"
          @click="emit('dry-run')"
        >
          预演检查（不扣费）
        </el-button>
        <el-checkbox
          v-model="resubmit"
          size="small"
          class="resubmit-switch"
          title="同一提示词再开一轮：不复用已有结果，会重新扣额度"
        >
          再抽一版（重新计费）
        </el-checkbox>
        <el-button
          type="primary"
          size="small"
          :loading="activeKind === 'submit'"
          :disabled="(running && activeKind !== 'submit') || Boolean(runBlockReason)"
          @click="emit('submit')"
        >
          提交生成
        </el-button>
      </div>
    </div>
    <div v-if="runBlockReason" class="gate-reason">提交条件未满足：{{ runBlockReason }}</div>
  </SubmitBar>
</template>

<style scoped>
.w-mode {
  width: 180px;
}

.w-video-model {
  width: 220px;
}

.w-ratio {
  width: 110px;
}

.w-resolution {
  width: 170px;
}

.w-scale {
  width: 190px;
}

.w-duration {
  width: 150px;
}

.w-audio {
  width: 190px;
}
</style>
