<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Refresh, VideoPlay } from '@element-plus/icons-vue'
import { fetchCase, runOperation } from '@/api/case'
import { deleteRangePreset } from '@/api/workflow'
import FileRow from '@/components/common/FileRow.vue'
import RangeRow from '@/components/range/RangeRow.vue'
import type { RangeTag } from '@/components/range/types'
import { useCaseTasks } from '@/composables/useCaseTasks'
import { taskStatusMeta } from '@/constants/taskStatus'
import { formatSize } from '@/utils/format'
import { formatRangeTime, presetSummary, sameGeometry } from '@/utils/range'
import type { CaseInfo, ExtractVersion, FileInfo, RangePreset } from '@/types/case-info'

const props = defineProps<{ caseName: string; info: CaseInfo }>()
const emit = defineEmits<{ updated: [data: CaseInfo] }>()

const { latest, start } = useCaseTasks(
  computed(() => props.caseName),
  ['extract'],
)
// 进行中从任务状态推导，而不是“本页是否点过按钮”：中途刷新/切页回来后
// 按钮加载态与完成刷新都还能工作（2026-10-26 用户反馈的陈旧“文件缺失”）。
const submitting = ref(false)
const running = computed(() => {
  const task = latest.value
  return (
    submitting.value ||
    Boolean(
      task && task.kind === 'extract' && (task.status === 'queued' || task.status === 'running'),
    )
  )
})
const mountedAt = Date.now()

const pickGate = computed(() => props.info.gates.pick)
const outputs = computed<FileInfo[]>(() => props.info.step_files.extract?.outputs ?? [])
const current = computed(() => props.info.extract_current)
const presets = computed(() => props.info.pick?.presets ?? [])
const selectedPresetId = ref<string | null>(
  props.info.pick?.active ?? props.info.pick?.presets[0]?.id ?? null,
)
const selectedPreset = computed(
  () => presets.value.find((item) => item.id === selectedPresetId.value) ?? null,
)
const selectedSummary = computed(() =>
  selectedPreset.value ? presetSummary(selectedPreset.value) : '',
)

function presetTags(preset: RangePreset): RangeTag[] {
  return preset.id === selectedPresetId.value ? [{ text: '本次抽取', kind: 'current' }] : []
}

function matchingPresets(item: ExtractVersion): RangePreset[] {
  if (!item.viewport || !item.inner) return []
  return presets.value.filter((preset) =>
    sameGeometry(
      preset.viewport,
      preset.inner,
      preset.frames,
      item.viewport!,
      item.inner!,
      item.frames ?? null,
    ),
  )
}

/** 多条 preset 几何相同时，取最近保存的那条做展示。 */
function matchingPreset(item: ExtractVersion): RangePreset | null {
  return (
    [...matchingPresets(item)].sort(
      (a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime(),
    )[0] ?? null
  )
}

function versionPresetText(item: ExtractVersion): string {
  if (!item.viewport || !item.inner) return '仅抽取记录'
  const matches = matchingPresets(item)
  const match = matchingPreset(item)
  if (!match) return '未匹配到当前保存的范围（可能已修改或删除）'
  const suffix = matches.length > 1 ? `（同几何 ${matches.length} 条）` : ''
  return `${formatRangeTime(match.updated_at)} 最近保存${suffix}`
}

function versionViewport(item: ExtractVersion): string {
  const vp = item.viewport
  if (!vp) return '—'
  return (
    `yaw ${vp.yaw_deg.toFixed(1)}° · pitch ${vp.pitch_deg.toFixed(1)}° · ` +
    `fov ${vp.fov_h_deg.toFixed(1)}°`
  )
}

function versionInner(item: ExtractVersion): string {
  const inner = item.inner
  if (!inner) return '—'
  return `x${inner.x} y${inner.y} · ${inner.width}×${inner.height}`
}

function versionFrames(item: ExtractVersion): string {
  return item.frames ? `帧 ${item.frames.start}–${item.frames.end}` : '无时间范围'
}

function versionExtractedAt(item: ExtractVersion): string {
  return item.extracted_at ? formatRangeTime(item.extracted_at) : '未知'
}

function versionTags(item: ExtractVersion): RangeTag[] {
  const tags: RangeTag[] = []
  if (item.version === current.value) tags.push({ text: '最新', kind: 'current' })
  tags.push(item.has_result ? { text: '有结果', kind: 'ok' } : { text: '无结果', kind: 'warn' })
  if (item.viewport && item.inner && !matchingPreset(item)) {
    tags.push({ text: '范围已变', kind: 'warn' })
  }
  return tags
}

async function onDeletePreset(preset: RangePreset): Promise<void> {
  try {
    await ElMessageBox.confirm('删除这条范围？已抽取的版本仍然保留，不受影响。', '删除范围', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  try {
    const data = await deleteRangePreset(props.caseName, preset.id)
    const next = data.pick?.active ?? data.pick?.presets[0]?.id ?? null
    selectedPresetId.value = next
    emit('updated', data)
    ElMessage.success('已删除范围')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '删除范围失败')
  }
}

/** 图片流程的抽取产物是 PNG 与 JSON：没有 clip.mp4 与首尾帧卡片。 */
const isImageFlow = computed(
  () => props.info.flow === 'patchimg' || props.info.flow === 'fullRedrawImg',
)

function output(name: string): FileInfo | undefined {
  return outputs.value.find((file) => file.name === name)
}

const clip = computed(() => output('clip.mp4'))
const mask = computed(() => output('clip_mask.png'))
const sidecar = computed(() => output('clip.json'))
const rawFirstFile = computed(() => output('first_frame_raw.png'))
const rawLastFile = computed(() => output('last_frame_raw.png'))

function sizeText(file: FileInfo | undefined): string {
  if (!file || !file.exists) return '文件缺失'
  return formatSize(file.size)
}

async function runExtract(): Promise<void> {
  if (running.value) return
  if (!selectedPresetId.value) {
    ElMessage.warning('先选一条保存的范围')
    return
  }
  if (pickGate.value && !pickGate.value.ok) {
    ElMessage.warning(pickGate.value.reason || '先完成选区')
    return
  }
  submitting.value = true
  try {
    await runOperation(props.caseName, 'extract', { preset: selectedPresetId.value })
    start()
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '启动抽取失败')
  } finally {
    submitting.value = false
  }
}

// 挂载即开始轮询：任务从哪里发起（本页、别的页、重启前的旧任务）都看得到，
// 完成后的自动刷新才有数据源。
onMounted(() => start())

watch(latest, async (task) => {
  if (!task || task.kind !== 'extract') return
  const created = new Date(task.created_at).getTime()
  // 挂载之前就完成的旧任务不触发刷新：挂载时拿到的 info 已是最新。
  if (Number.isNaN(created) || created < mountedAt - 5000) return
  if (task.status === 'done') {
    const data = await fetchCase(props.caseName)
    emit('updated', data)
    ElMessage.success('抽取完成')
  } else if (task.status === 'failed') {
    ElMessage.error(task.error || '抽取失败，原因见下方任务日志')
  }
})

watch(
  () => props.info.pick,
  (pick) => {
    if (!pick) {
      selectedPresetId.value = null
      return
    }
    const ids = pick.presets.map((item) => item.id)
    if (selectedPresetId.value && ids.includes(selectedPresetId.value)) return
    selectedPresetId.value = pick.active ?? ids[0] ?? null
  },
)
</script>

<template>
  <div class="ex-page">
    <section class="panel">
      <div class="panel-head">
        <div>
          <h2 class="panel-title">保存的范围</h2>
          <span class="muted">点击选择本次抽取的范围</span>
        </div>
        <el-button
          type="primary"
          :icon="VideoPlay"
          :loading="running"
          :disabled="!selectedPresetId || Boolean(pickGate && !pickGate.ok)"
          @click="runExtract"
        >
          抽取选中范围
        </el-button>
      </div>
      <div class="panel-body">
        <div v-if="!presets.length" class="muted">先在选区页保存一条范围。</div>
        <div v-else class="range-list">
          <RangeRow
            v-for="preset in presets"
            :key="preset.id"
            :time="formatRangeTime(preset.created_at)"
            :geometry="presetSummary(preset)"
            :meta="`最近保存 ${formatRangeTime(preset.updated_at)}`"
            :tags="presetTags(preset)"
            :selected="preset.id === selectedPresetId"
            selectable
            deletable
            @select="selectedPresetId = preset.id"
            @delete="onDeletePreset(preset)"
          />
        </div>
        <p class="range-summary muted">本次抽取：{{ selectedSummary || '未选择' }}</p>
        <el-alert
          v-if="pickGate && !pickGate.ok"
          type="warning"
          :closable="false"
          :title="pickGate.reason || '先保存一条完整范围'"
        />
        <el-alert
          v-if="info.pick?.dirty"
          type="warning"
          :closable="false"
          title="当前草稿有未保存改动；抽取只使用已保存的范围。"
        />
      </div>
    </section>

    <section class="panel ex-products-panel">
      <div class="panel-head">
        <h2 class="panel-title">最新抽取产物</h2>
        <span class="muted">
          {{ current ? `版本 ${current}` : '还没有' }} · 版本目录保留旧结果，不覆盖
        </span>
      </div>
      <div class="panel-body">
        <div v-if="outputs.length" class="ex-products">
          <template v-if="isImageFlow">
            <FileRow v-for="file in outputs" :key="file.path" :entry="file" />
          </template>
          <template v-else>
            <div class="ex-prod">
              <a
                v-if="clip?.exists && clip.open_url"
                :href="clip.open_url"
                target="_blank"
                class="ex-prod-media ex-prod-video"
                :aria-label="`播放 ${clip.name}`"
              >
                <img
                  v-if="rawFirstFile?.exists && rawFirstFile.open_url"
                  :src="rawFirstFile.open_url"
                  alt="clip.mp4 首帧"
                  class="ex-poster"
                />
                <span class="ex-scrim" />
                <span class="ex-play">▶</span>
                <span class="ex-prod-hint">播放</span>
              </a>
              <div v-else class="ex-prod-media ex-prod-empty">文件缺失</div>
              <div class="ex-prod-body">
                <div class="ex-prod-name mono">clip.mp4</div>
                <div class="ex-prod-meta">{{ sizeText(clip) }}</div>
              </div>
            </div>

            <div class="ex-prod">
              <a
                v-if="mask?.exists && mask.open_url"
                :href="mask.open_url"
                target="_blank"
                class="ex-prod-media"
                :aria-label="`打开 ${mask.name}`"
              >
                <img :src="mask.open_url" alt="clip_mask.png" />
              </a>
              <div v-else class="ex-prod-media ex-prod-empty">文件缺失</div>
              <div class="ex-prod-body">
                <div class="ex-prod-name mono">clip_mask.png</div>
                <div class="ex-prod-meta">区域遮罩 · {{ sizeText(mask) }}</div>
              </div>
            </div>

            <div class="ex-prod">
              <a
                v-if="sidecar?.exists && sidecar.open_url"
                :href="sidecar.open_url"
                target="_blank"
                class="ex-prod-media ex-prod-json"
                :aria-label="`打开 ${sidecar.name}`"
              >
                { case, viewport, frames, … }
              </a>
              <div v-else class="ex-prod-media ex-prod-empty">文件缺失</div>
              <div class="ex-prod-body">
                <div class="ex-prod-name mono">clip.json</div>
                <div class="ex-prod-meta">片段台账 · {{ sizeText(sidecar) }}</div>
              </div>
            </div>

            <div class="ex-prod">
              <div v-if="rawFirstFile?.exists && rawLastFile?.exists" class="ex-pair">
                <a
                  v-if="rawFirstFile.open_url"
                  :href="rawFirstFile.open_url"
                  target="_blank"
                  :aria-label="`打开 ${rawFirstFile.name}`"
                >
                  <img :src="rawFirstFile.open_url" alt="首帧原始图" />
                </a>
                <a
                  v-if="rawLastFile.open_url"
                  :href="rawLastFile.open_url"
                  target="_blank"
                  :aria-label="`打开 ${rawLastFile.name}`"
                >
                  <img :src="rawLastFile.open_url" alt="尾帧原始图" />
                </a>
              </div>
              <div v-else class="ex-prod-media ex-prod-empty">文件缺失</div>
              <div class="ex-prod-body">
                <div class="ex-prod-name mono">首尾原始帧</div>
                <div class="ex-prod-meta">png × 2 · 工作台输入底图</div>
              </div>
            </div>
          </template>
        </div>
        <p v-if="!outputs.length" class="muted ex-empty-note">
          {{
            isImageFlow
              ? '还没有抽取结果。运行抽取会生成 PNG 与清单文件。'
              : '还没有抽取结果。运行抽取会生成 clip.mp4、clip_mask.png、clip.json 与首尾原始帧。'
          }}
        </p>
      </div>
    </section>

    <div class="ex-cols">
      <section class="panel">
        <div class="panel-head">
          <h2 class="panel-title">抽取版本</h2>
          <span class="muted">{{ info.extracts.length }} 个版本 · 只读</span>
        </div>
        <div class="panel-body version-list">
          <div v-if="!info.extracts.length" class="muted">还没有可用版本。</div>
          <article v-for="item in info.extracts" :key="item.version" class="version-card">
            <div class="version-head">
              <span class="version-id mono">{{ item.version }}</span>
              <span class="version-tags">
                <el-tag
                  v-for="tag in versionTags(item)"
                  :key="tag.text"
                  size="small"
                  effect="light"
                  :type="
                    tag.kind === 'ok' ? 'success' : tag.kind === 'warn' ? 'warning' : 'primary'
                  "
                >
                  {{ tag.text }}
                </el-tag>
              </span>
            </div>
            <dl class="version-facts">
              <div>
                <dt>抽取于</dt>
                <dd class="mono">{{ versionExtractedAt(item) }}</dd>
              </div>
              <div>
                <dt>对应范围</dt>
                <dd>{{ versionPresetText(item) }}</dd>
              </div>
              <div>
                <dt>视口</dt>
                <dd class="mono">{{ versionViewport(item) }}</dd>
              </div>
              <div>
                <dt>选区</dt>
                <dd class="mono">{{ versionInner(item) }} · {{ versionFrames(item) }}</dd>
              </div>
            </dl>
          </article>
          <p class="muted ex-note">
            列表只读；最新一版仍供生成与首尾帧使用，旧版本不清除。合并页可单独选择本次使用的版本。
          </p>
        </div>
      </section>

      <section class="panel">
        <div class="panel-head">
          <h2 class="panel-title">最近一次抽取任务</h2>
          <el-button link type="primary" :icon="Refresh" @click="start()">刷新</el-button>
        </div>
        <div class="panel-body">
          <div v-if="!latest" class="muted">还没有抽取任务。</div>
          <template v-else>
            <div class="task-summary">
              <el-tag size="small" effect="light" :type="taskStatusMeta(latest.status).type">
                {{ taskStatusMeta(latest.status).text }}
              </el-tag>
              <span class="mono">{{ latest.id }}</span>
              <span v-if="latest.queue_position" class="muted"
                >排队第 {{ latest.queue_position }} 位</span
              >
            </div>
            <el-alert
              v-if="latest.error"
              type="error"
              :closable="false"
              :title="latest.error"
              class="ex-alert"
            />
            <pre v-if="latest.tail.length" class="task-tail">{{ latest.tail.join('\n') }}</pre>
            <p class="muted ex-note">全部任务在任务中心。</p>
          </template>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.ex-page {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.ex-alert {
  margin-top: var(--space-3);
}

.ex-products {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--space-3);
}

.ex-prod {
  display: flex;
  flex-direction: column;
  overflow: hidden;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}

.ex-prod-media {
  position: relative;
  display: grid;
  place-items: center;
  overflow: hidden;
  aspect-ratio: 16 / 9;
  background: var(--color-surface-muted);
  color: var(--color-text-muted);
  text-decoration: none;
}

.ex-prod-media img {
  width: 100%;
  height: 100%;
  object-fit: contain;
}

.ex-prod-video {
  gap: var(--space-1);
  background: linear-gradient(135deg, #1e3a8a, #3b82f6);
  color: #e2e8f0;
}

.ex-poster {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.ex-scrim {
  position: absolute;
  inset: 0;
  background: rgba(15, 23, 42, 0.35);
}

.ex-play {
  position: relative;
  z-index: 1;
  display: grid;
  place-items: center;
  width: 40px;
  height: 40px;
  border-radius: 50%;
  background: rgba(15, 23, 42, 0.45);
  font-size: 14px;
}

.ex-prod-hint {
  position: relative;
  z-index: 1;
  font-size: var(--text-xs);
}

.ex-prod-json {
  font-family: var(--font-mono);
  font-size: var(--text-xs);
}

.ex-prod-empty {
  border-bottom: 1px dashed var(--color-border-strong);
  color: var(--color-text-faint);
  font-size: var(--text-xs);
}

.ex-pair {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 2px;
  aspect-ratio: 16 / 9;
  overflow: hidden;
  background: var(--color-video-bg);
}

.ex-pair img {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.ex-prod-body {
  padding: var(--space-2) var(--space-3);
}

.ex-prod-name {
  font-size: var(--text-xs);
  font-weight: 600;
}

.ex-prod-meta {
  overflow: hidden;
  color: var(--color-text-muted);
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ex-cols {
  order: 1;
  display: grid;
  grid-template-columns: minmax(0, 1.2fr) minmax(0, 1fr);
  gap: var(--space-4);
  align-items: start;
}

.ex-products-panel {
  order: 2;
}

.range-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
  max-height: 240px;
  overflow: auto;
}

.range-summary {
  margin: var(--space-2) 0 0;
  font-size: var(--text-xs);
}

.version-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.version-card {
  padding: var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}

.version-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
}

.version-id {
  font-size: var(--text-sm);
  font-weight: 600;
}

.version-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

.version-facts {
  display: grid;
  gap: 4px;
  margin: var(--space-2) 0 0;
}

.version-facts > div {
  display: grid;
  grid-template-columns: 72px minmax(0, 1fr);
  gap: var(--space-2);
  font-size: var(--text-xs);
}

.version-facts dt {
  color: var(--color-text-muted);
}

.version-facts dd {
  margin: 0;
  color: var(--color-text);
  overflow-wrap: anywhere;
}

.ex-note {
  margin: var(--space-2) 0 0;
  font-size: var(--text-xs);
}

.ex-empty-note {
  margin: 0;
  font-size: var(--text-xs);
}

@media (max-width: 1280px) {
  .ex-products {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .ex-cols {
    grid-template-columns: minmax(0, 1fr);
  }
}
</style>
