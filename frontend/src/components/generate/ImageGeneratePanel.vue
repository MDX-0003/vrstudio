<script setup lang="ts">
import { computed } from 'vue'
import { Refresh, VideoPlay } from '@element-plus/icons-vue'
import { latestImageRound } from '@/utils/rounds'
import type { Gate } from '@/types/case'
import type { CaseInfo, ModelOption } from '@/types/case-info'
import type { EpisodeOption, ProjectOption, SessionOption } from '@/types/session'

const props = defineProps<{
  caseName: string
  info: CaseInfo
  resolutionOptions: string[]
  imageModelOptions: ModelOption[]
  imageModelsError: string
  projects: ProjectOption[]
  episodes: EpisodeOption[]
  sessions: SessionOption[]
  sessionsLoading: boolean
  sessionError: string
  extractGate: Gate | undefined
  generateGate: Gate | undefined
  extracting: boolean
  running: boolean
  status: string
  statusDetail: string
  runBlockReason: string
  sessionReady: boolean
  adoptingKey: string | null
}>()

const resolution = defineModel<string>('resolution', { required: true })
const num = defineModel<number>('num', { required: true })
const imageModelId = defineModel<number | null>('imageModelId', { required: true })
const selectedProject = defineModel<string>('selectedProject', { required: true })
const selectedEpisode = defineModel<string>('selectedEpisode', { required: true })
const selectedSession = defineModel<string>('selectedSession', { required: true })

const emit = defineEmits<{
  extract: []
  'dry-run': []
  submit: []
  adopt: [round: string, index: number]
  'refresh-sessions': []
}>()

const NUM_OPTIONS = [1, 2, 3, 4]
const round = computed(() => latestImageRound(props.info.image_rounds))

function isAdopted(index: number): boolean {
  const current = round.value
  return Boolean(current?.adopted && props.info.adopt_index === index)
}

function adoptKey(index: number): string {
  return `${round.value?.round ?? ''}:${index}`
}

const extractLabel = computed(() => (props.info.flow === 'patchimg' ? '运行抽取（局部裁剪）' : '运行抽取（整幅扩充）'))
</script>

<template>
  <div class="panel">
    <div class="panel-head">
      <h2 class="panel-title">图片生成参数</h2>
      <span class="muted">
        <span class="status-dot" :class="running ? 'queued' : status === '已完成' ? 'done' : ''" />
        {{ status }}
        <span v-if="statusDetail" class="run-detail">{{ statusDetail }}</span>
      </span>
    </div>
    <div class="panel-body">
      <div class="image-params">
        <label class="ctl">
          <span>分辨率</span>
          <el-select v-model="resolution" size="small" class="w-res">
            <el-option
              v-for="item in resolutionOptions"
              :key="item"
              :label="item"
              :value="item"
            />
            <el-option v-if="!resolutionOptions.length" label="暂无档位" value="" disabled />
          </el-select>
        </label>
        <label class="ctl">
          <span>候选张数</span>
          <el-select v-model="num" size="small" class="w-num">
            <el-option
              v-for="item in NUM_OPTIONS"
              :key="item"
              :label="`${item} 张`"
              :value="item"
            />
          </el-select>
        </label>
        <label class="ctl">
          <span>图片模型</span>
          <el-select
            v-model="imageModelId"
            size="small"
            class="w-model"
            placeholder="选择图片模型"
            :title="imageModelsError || undefined"
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
      </div>

      <div class="image-params">
        <label class="ctl">
          <span>项目</span>
          <el-select
            v-model="selectedProject"
            size="small"
            class="w-project"
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
          <el-select v-model="selectedEpisode" size="small" class="w-episode" :disabled="!episodes.length">
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
        <el-button size="small" :icon="Refresh" :loading="sessionsLoading" @click="emit('refresh-sessions')">
          刷新
        </el-button>
      </div>
      <p :class="sessionReady ? 'muted quota-hint' : 'session-hint'">
        {{
          sessionReady
            ? '图片生成按项目会话计费；预演不扣额度。'
            : '图片生成按项目会话计费：先在项目与剧集下选择会话，才能提交生成。'
        }}
      </p>
      <span v-if="sessionError" class="session-error">{{ sessionError }}</span>

      <div class="image-actions">
        <el-button
          v-if="!extractGate?.ok"
          size="small"
          :icon="VideoPlay"
          :loading="extracting"
          :disabled="Boolean(extractGate && extractGate.ready === false)"
          @click="emit('extract')"
        >
          {{ extractLabel }}
        </el-button>
        <el-button
          size="small"
          :disabled="running || Boolean(runBlockReason)"
          @click="emit('dry-run')"
        >
          预演检查（不花额度）
        </el-button>
        <el-button
          type="primary"
          size="small"
          :disabled="running || !sessionReady || Boolean(runBlockReason)"
          @click="emit('submit')"
        >
          提交生成
        </el-button>
      </div>
      <el-alert
        v-if="runBlockReason"
        type="warning"
        :closable="false"
        :title="runBlockReason"
        class="gate-alert"
      />
    </div>
  </div>

  <div class="panel">
    <div class="panel-head">
      <h2 class="panel-title">候选</h2>
      <span class="muted">
        <template v-if="round">
          {{ round.round }}<template v-if="round.gate"> · gate={{ round.gate }}</template>
        </template>
        <template v-else>还没有生成轮次</template>
      </span>
    </div>
    <div class="panel-body">
      <div v-if="!round || !round.results.length" class="candidate-empty">
        <el-empty description="还没有候选图；先提交生成" :image-size="72" />
      </div>
      <div v-else class="candidate-grid">
        <div v-for="item in round.results" :key="item.index" class="candidate-card">
          <a
            v-if="item.exists"
            :href="item.url"
            target="_blank"
            class="candidate-thumb"
            :aria-label="`打开候选 ${item.index}`"
          >
            <img :src="item.url" :alt="`候选 ${item.index}`" />
          </a>
          <div v-else class="candidate-thumb missing">文件缺失</div>
          <div class="candidate-foot">
            <span class="mono">#{{ item.index }} · {{ item.name }}</span>
            <el-tag v-if="isAdopted(item.index)" size="small" type="success" effect="light">
              已采用
            </el-tag>
            <el-button
              v-else
              type="primary"
              plain
              size="small"
              :disabled="!item.exists || adoptingKey !== null"
              :loading="adoptingKey === adoptKey(item.index)"
              @click="emit('adopt', round.round, item.index)"
            >
              选用这张
            </el-button>
          </div>
        </div>
      </div>
      <p v-if="round && round.adopted" class="muted adopt-hint">
        当前采用：{{ round.round }} 第 {{ info.adopt_index ?? '—' }} 张
      </p>
    </div>
  </div>
</template>

<style scoped>
.image-params {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-3);
  margin-bottom: var(--space-2);
}

.ctl {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  color: var(--color-text-muted);
  font-size: var(--text-xs);
}

.w-res {
  width: 90px;
}

.w-num {
  width: 90px;
}

.w-model {
  width: 180px;
}

.w-project {
  width: 150px;
}

.w-episode {
  width: 150px;
}

.w-session {
  width: 200px;
}

.quota-hint,
.session-hint {
  margin: var(--space-1) 0;
  font-size: var(--text-xs);
}

.session-hint {
  color: var(--color-warning);
}

.session-error {
  color: var(--color-danger);
  font-size: var(--text-xs);
}

.image-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
  margin-top: var(--space-2);
}

.gate-alert {
  margin-top: var(--space-2);
}

.candidate-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: var(--space-3);
}

.candidate-card {
  display: flex;
  flex-direction: column;
  overflow: hidden;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}

.candidate-thumb {
  display: grid;
  place-items: center;
  aspect-ratio: 2 / 1;
  overflow: hidden;
  background: var(--color-video-bg);
  color: var(--color-text-faint);
  font-size: var(--text-xs);
  text-decoration: none;
}

.candidate-thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.candidate-foot {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-1);
  padding: var(--space-2);
  font-size: var(--text-xs);
}

.candidate-empty {
  padding: var(--space-2);
}

.adopt-hint {
  margin: var(--space-2) 0 0;
  font-size: var(--text-xs);
}

.run-detail {
  margin-left: var(--space-1);
  font-size: var(--text-xs);
}
</style>
