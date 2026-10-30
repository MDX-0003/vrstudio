<script setup lang="ts">
import { Refresh } from '@element-plus/icons-vue'
import type { InputOption, ModeInfo, OutputOption } from '@/types/case-info'
import type { EpisodeOption, ProjectOption, SessionOption } from '@/types/session'

const mode = defineModel<string>('mode', { required: true })
const ratio = defineModel<string>('ratio', { required: true })
const resolution = defineModel<string>('resolution', { required: true })
const scale = defineModel<string>('scale', { required: true })
const duration = defineModel<string>('duration', { required: true })
const selectedProject = defineModel<string>('selectedProject', { required: true })
const selectedEpisode = defineModel<string>('selectedEpisode', { required: true })
const selectedSession = defineModel<string>('selectedSession', { required: true })

defineProps<{
  modeOptions: ModeInfo[]
  ratioOptions: string[]
  resolutionOptions: OutputOption[]
  scaleOptions: InputOption[]
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
  showPresets: boolean
}>()

const emit = defineEmits<{
  'refresh-sessions': []
  'preset-4k': []
  'preset-1080p': []
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
  <div class="submit-bar">
    <div class="submit-inner">
      <div class="submit-row">
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
        <div v-if="showPresets" class="preset-actions">
          <el-button
            size="small"
            :loading="paramsSaving"
            title="Seedance 2.0 · firstLastFrame / allMediaRef · 4K"
            @click="emit('preset-4k')"
          >
            切到 4K
          </el-button>
          <el-button
            size="small"
            :loading="paramsSaving"
            title="Seedance 2.5 · videoEdit · 1080p"
            @click="emit('preset-1080p')"
          >
            切回 1080p
          </el-button>
        </div>
        <span v-if="paramsError" class="session-error">{{ paramsError }}</span>
      </div>
      <div class="submit-row">
        <label class="ctl">
          <span>项目</span>
          <el-select
            v-model="selectedProject"
            size="small"
            class="w-resolution"
            :loading="sessionsLoading"
          >
            <el-option
              v-for="item in projects"
              :key="item.id"
              :label="item.name"
              :value="String(item.id)"
            />
          </el-select>
        </label>
        <label class="ctl">
          <span>剧集</span>
          <el-select
            v-model="selectedEpisode"
            size="small"
            class="w-episode"
            :disabled="!episodes.length"
          >
            <el-option
              v-for="item in episodes"
              :key="item.no"
              :label="`第 ${item.no} 集 · ${item.name}`"
              :value="String(item.no)"
            />
          </el-select>
        </label>
        <label class="ctl">
          <span>会话</span>
          <el-select v-model="selectedSession" size="small" class="w-session">
            <el-option label="个人额度（不使用项目会话）" value="0" />
            <el-option
              v-for="item in sessions"
              :key="item.id"
              :label="item.name || '未命名'"
              :value="String(item.id)"
            />
          </el-select>
        </label>
        <el-button
          size="small"
          :icon="Refresh"
          :loading="sessionsLoading"
          @click="emit('refresh-sessions')"
        >
          刷新
        </el-button>
        <span class="muted quota-hint">
          已获取 {{ projects.length }} 个项目<template v-if="lastRefreshed">
            · 刷新于 {{ lastRefreshed }}</template
          >
          <template v-if="paramsSaving"> · 参数保存中</template>
        </span>
        <span v-if="sessionError" class="session-error">{{ sessionError }}</span>
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
    </div>
  </div>
</template>

<style scoped>
.submit-bar {
  position: fixed;
  left: var(--nav-current-width, var(--nav-width));
  right: 0;
  bottom: 0;
  z-index: 20;
  background: var(--color-overlay);
  border-top: 1px solid var(--color-border);
  box-shadow: var(--shadow-submitbar);
  backdrop-filter: blur(8px);
  transition: left var(--duration-base) ease;
}

.submit-inner {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  max-width: 1600px;
  margin: 0 auto;
  padding: var(--space-3) var(--space-6);
}

.submit-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-3);
}

.quota-hint {
  font-size: var(--text-xs);
  white-space: nowrap;
}

.session-error {
  color: var(--color-danger);
  font-size: var(--text-xs);
}

.submit-actions {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  margin-left: auto;
}

.run-state {
  display: inline-flex;
  align-items: center;
  color: var(--color-text-muted);
  font-size: var(--text-sm);
  white-space: nowrap;
}

.w-mode {
  width: 180px;
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

.preset-actions {
  display: flex;
  gap: var(--space-1);
}

.w-project {
  width: 170px;
}

.w-episode {
  width: 160px;
}

.w-session {
  width: 220px;
}

.run-detail {
  margin-left: var(--space-2);
  color: var(--color-text-muted);
  font-size: var(--text-xs);
}

.gate-reason {
  padding: var(--space-1) 0 var(--space-2);
  color: var(--color-warning);
  font-size: var(--text-xs);
}

.status-dot.stale {
  background: var(--color-warning);
}

@media (max-width: 1024px) {
  .submit-inner {
    padding: var(--space-2) var(--space-4);
  }
}
</style>
