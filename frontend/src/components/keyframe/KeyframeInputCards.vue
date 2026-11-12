<script setup lang="ts">
import { computed } from 'vue'
import type { KeyframePick, KeyframeRef, RawFrame } from '@/types/case-info'
import { KEYFRAME_ROLE_OPTIONS } from '@/constants/assetRoles'
import type { RoleOption } from '@/constants/assetRoles'
import { KEYFRAME_MAX_IMAGES } from '@/constants/keyframe'
import FileOpenLink from '@/components/common/FileOpenLink.vue'

const props = withDefaults(
  defineProps<{
    caseName: string
    step: 'first' | 'last'
    rawFrame: RawFrame | null
    refs: KeyframeRef[]
    firstPick: KeyframePick | null
    targetUrl: string
    targetName: string
    targetIsRaw: boolean
    maxRefs: number
    submittedCount: number
    overLimit: boolean
    disabled?: boolean
  }>(),
  { disabled: false },
)

const emit = defineEmits<{
  add: []
  remove: [asset: string]
  move: [asset: string, delta: number]
  role: [asset: string, role: string]
  note: [asset: string, note: string]
  useRaw: []
  focusCandidates: []
}>()

const roleOptions: RoleOption[] = KEYFRAME_ROLE_OPTIONS
const anchorNumber = computed(() => props.refs.length + 2)
const atCap = computed(() => props.refs.length >= props.maxRefs)
const hasSubject = computed(() => props.refs.some((ref) => ref.role === 'subject_ref'))
const hasAnchor = computed(() => props.step === 'last' && Boolean(props.firstPick))
const anchorOver = computed(() => hasAnchor.value && anchorNumber.value > KEYFRAME_MAX_IMAGES)
const rawName = computed(() => props.rawFrame?.name || '—')
const rawSize = computed(() => formatSize(props.rawFrame?.size))
const rawDims = computed(() =>
  props.rawFrame?.width && props.rawFrame?.height
    ? `${props.rawFrame.width}×${props.rawFrame.height}`
    : '',
)
const summaryItems = computed(() => {
  const items = ['图1 底图']
  props.refs.forEach((ref, index) => items.push(`图${index + 2} ${roleLabel(ref.role)}`))
  if (hasAnchor.value) items.push(`图${anchorNumber.value} 首帧定稿`)
  return items
})
const overText = computed(() => {
  const extra = props.refs.length - props.maxRefs
  if (props.step === 'last') {
    return `尾帧要同时带上图1 底图与首帧定稿，参考图最多 ${props.maxRefs} 张。当前 ${props.refs.length} 张参考图，共 ${props.submittedCount} 张，超过 ${KEYFRAME_MAX_IMAGES} 张上限；先移除 ${extra} 张。`
  }
  return `首帧要带上图1 底图，参考图最多 ${props.maxRefs} 张。当前 ${props.refs.length} 张参考图，共 ${props.submittedCount} 张，超过 ${KEYFRAME_MAX_IMAGES} 张上限；先移除 ${extra} 张。`
})
const noSubjectText = computed(() =>
  props.step === 'last' && hasAnchor.value
    ? '没有人物三视图：尾帧会以首帧定稿作为身份锚点；细节只能靠提示词描述。仍然可以生成。'
    : '没有人物三视图：人物外观只能靠提示词描述，跨批次可能不是同一个人。仍然可以生成。',
)

function assetUrl(asset: string | null | undefined): string {
  if (!asset) return ''
  return `/img/asset/${encodeURIComponent(props.caseName)}/${encodeURIComponent(asset)}`
}

function refUrl(ref: KeyframeRef): string {
  return ref.url || assetUrl(ref.asset)
}

function anchorUrl(): string {
  return assetUrl(props.firstPick?.asset)
}

function roleLabel(role: string): string {
  return roleOptions.find((item) => item.value === role)?.label || role
}

function roleHelp(role: string): string {
  return roleOptions.find((item) => item.value === role)?.help || ''
}

function formatSize(size: number | null | undefined): string {
  if (!size) return ''
  return `${(size / 1048576).toFixed(1)} MB`
}

function over(number: number): boolean {
  return number > KEYFRAME_MAX_IMAGES
}
</script>

<template>
  <section class="panel">
    <div class="panel-head">
      <h2 class="panel-title">图片输入</h2>
      <span class="panel-sub">
        编号就是提示词里的图N；底图固定图1，参考图按顺序排，目标不是输入。
      </span>
    </div>
    <div class="panel-body">
      <div class="summary">
        <span>
          本次提交
          <b :class="{ over: overLimit }">{{ submittedCount }}</b>
          / {{ KEYFRAME_MAX_IMAGES }} 张：
        </span>
        <span v-for="item in summaryItems" :key="item" class="chip">{{ item }}</span>
        <span class="spacer" />
        <span class="muted">本步参考图 {{ refs.length }} / {{ maxRefs }}</span>
      </div>

      <el-alert
        v-if="overLimit"
        type="error"
        :closable="false"
        :title="overText"
        class="stack-alert"
      />
      <el-alert
        v-if="!hasSubject"
        type="warning"
        :closable="false"
        :title="noSubjectText"
        class="stack-alert"
      />

      <div class="cards">
        <article class="card locked">
          <div class="card-head">
            <span class="no">图1</span>
            <span class="title">{{ step === 'first' ? '原始首帧 · 底图' : '原始尾帧 · 底图' }}</span>
            <span class="tag">锁定</span>
          </div>
          <div class="card-media">
            <img v-if="rawFrame?.exists" :src="rawFrame.url" :alt="rawName" />
            <div v-else class="card-empty">缺少原始帧</div>
          </div>
          <div class="card-name mono">{{ rawName }}</div>
          <div class="card-note">
            <template v-if="rawDims">{{ rawDims }}<template v-if="rawSize"> · {{ rawSize }}</template></template>
            场景、机位与遮挡以它为准；不可移除。
          </div>
          <FileOpenLink v-if="rawFrame" :file="rawFrame" label="打开底图" class="card-open" />
        </article>

        <article
          v-for="(ref, index) in refs"
          :key="ref.asset"
          class="card"
          :class="{ over: over(index + 2) }"
        >
          <div class="card-head">
            <span class="no">图{{ index + 2 }}</span>
            <span class="title">{{ roleLabel(ref.role) }}</span>
            <span class="tag" :class="{ 'over-tag': over(index + 2) }">
              {{ over(index + 2) ? '超出上限' : roleLabel(ref.role) === '人物三视图' ? '唯一外观依据' : roleLabel(ref.role) === '氛围光影' ? '只读光线' : '只读细节' }}
            </span>
          </div>
          <div class="card-media" :class="{ contain: ref.role === 'subject_ref' }">
            <img v-if="ref.exists" :src="refUrl(ref)" :alt="ref.label" />
            <div v-else class="card-empty">文件缺失</div>
          </div>
          <div class="card-name mono">
            {{ ref.file?.name || ref.asset }}
            <template v-if="ref.file?.size"> · {{ formatSize(ref.file.size) }}</template>
          </div>
          <el-select
            :model-value="ref.role"
            size="small"
            :disabled="disabled"
            :title="roleHelp(ref.role)"
            class="role-select"
            @change="(value: string) => emit('role', ref.asset, value)"
          >
            <el-option
              v-for="option in roleOptions"
              :key="option.value"
              :label="option.label"
              :value="option.value"
              :title="option.help"
            />
          </el-select>
          <el-input
            v-if="ref.role !== 'subject_ref'"
            :model-value="ref.note"
            size="small"
            :disabled="disabled"
            :placeholder="ref.role === 'lighting_ref' ? '写光源位置、色温或明暗对比' : '写这张图负责的细节'"
            @input="(value: string) => emit('note', ref.asset, value)"
          />
          <div v-else class="card-note">主三视图不需要用途说明。</div>
          <div class="card-actions">
            <el-button
              link
              size="small"
              :disabled="disabled || index === 0"
              @click="emit('move', ref.asset, -1)"
            >
              ↑
            </el-button>
            <el-button
              link
              size="small"
              :disabled="disabled || index === refs.length - 1"
              @click="emit('move', ref.asset, 1)"
            >
              ↓
            </el-button>
            <el-button link type="danger" size="small" :disabled="disabled" @click="emit('remove', ref.asset)">
              移除
            </el-button>
            <span class="spacer" />
            <FileOpenLink :file="ref.file" label="打开原图" />
          </div>
        </article>

        <button
          class="card add"
          :class="{ disabled: atCap }"
          :disabled="atCap || disabled"
          @click="emit('add')"
        >
          <span class="plus">{{ atCap ? '×' : '＋' }}</span>
          <strong>{{ atCap ? `已达上限 ${maxRefs} 张` : '添加参考图' }}</strong>
          <span class="faint small">{{ atCap ? '移除一张后才能再加' : `本步最多 ${maxRefs} 张` }}</span>
        </button>

        <article v-if="step === 'last'" class="card locked" :class="{ over: anchorOver }">
          <div class="card-head">
            <span class="no">图{{ anchorNumber }}</span>
            <span class="title">首帧定稿 · 身份锚点</span>
            <span class="tag" :class="{ 'over-tag': anchorOver }">
              {{ anchorOver ? '超出上限' : '自动追加' }}
            </span>
          </div>
          <div class="card-media">
            <img v-if="firstPick" :src="anchorUrl()" alt="首帧定稿" />
            <div v-else class="card-empty">生成并定稿首帧后填入</div>
          </div>
          <div class="card-name mono">{{ firstPick?.file?.name || '待定稿' }}</div>
          <div class="card-note">
            {{
              anchorOver
                ? `首帧定稿是图${anchorNumber}，加入它以后总数为 ${submittedCount} 张，超过 ${KEYFRAME_MAX_IMAGES} 张上限；先移除参考图。`
                : '尾帧批次自动追加，不可移除；参考图编号因此不变。'
            }}
          </div>
        </article>
      </div>

      <div class="output-row">
        <div class="output-label">输出<span>不占图号</span></div>
        <article class="card output target-wide">
          <div class="card-media">
            <img :src="targetUrl" :alt="targetName" />
          </div>
          <div class="target-meta">
            <div class="card-head">
              <span class="no">目标</span>
              <span class="title">{{ step === 'first' ? '首帧定稿' : '尾帧定稿' }}</span>
            </div>
            <div class="card-name mono">
              {{ targetName }}<template v-if="targetIsRaw">（原始帧，未重绘）</template>
            </div>
            <div class="card-note">当前步骤要定稿的那一帧；定稿后交接给视频步。</div>
          </div>
          <div class="card-actions">
            <el-button size="small" :disabled="targetIsRaw" @click="emit('useRaw')">
              直接用原始帧
            </el-button>
            <el-button link type="primary" size="small" @click="emit('focusCandidates')">
              从下方候选里挑
            </el-button>
          </div>
        </article>
      </div>
    </div>
  </section>
</template>

<style scoped>
.panel {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-1);
}

.panel-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-4);
  border-bottom: 1px solid var(--color-border);
}

.panel-title {
  margin: 0;
  font-size: var(--text-lg);
}

.panel-sub {
  color: var(--color-text-muted);
  font-size: var(--text-xs);
}

.panel-body {
  padding: var(--space-4);
}

.summary {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
  margin-bottom: var(--space-3);
  font-size: var(--text-xs);
}

.summary b {
  color: var(--color-primary);
}

.summary b.over {
  color: var(--color-danger);
}

.chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px;
  border: 1px solid var(--color-border);
  border-radius: 999px;
  background: var(--color-surface-muted);
  font-size: 11px;
}

.stack-alert {
  margin-bottom: var(--space-3);
}

.cards {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-3);
}

.card {
  display: flex;
  flex: 1 1 200px;
  max-width: 320px;
  flex-direction: column;
  gap: var(--space-2);
  min-width: 0;
  padding: var(--space-2);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
  position: relative;
}

.card.locked {
  border-style: dashed;
  background: var(--color-surface-muted);
}

.card.output {
  border-color: var(--color-primary);
  background: var(--color-primary-soft);
}

.card.over {
  border-color: var(--color-danger);
  background: var(--color-danger-soft);
}

.card.add {
  align-items: center;
  justify-content: center;
  min-height: 220px;
  border: 1px dashed var(--color-border-strong);
  background: var(--color-surface-muted);
  color: var(--color-text-muted);
  cursor: pointer;
  text-align: center;
}

.card.add:disabled {
  cursor: not-allowed;
  opacity: 0.72;
}

.card.add .plus {
  color: var(--color-primary);
  font-size: 26px;
  line-height: 1;
}

.card-head {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-height: 24px;
  font-size: var(--text-xs);
}

.card-head .title {
  overflow: hidden;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.card-head .tag {
  flex: 0 0 auto;
  margin-left: auto;
  color: var(--color-text-muted);
  font-size: 10px;
}

.over-tag {
  color: var(--color-danger) !important;
  font-weight: 700;
}

.no {
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  justify-content: center;
  min-width: 28px;
  height: 18px;
  padding: 0 6px;
  border-radius: var(--radius-sm);
  background: var(--color-primary-soft);
  color: var(--color-primary);
  font-size: 11px;
  font-weight: 700;
}

.card-media {
  position: relative;
  overflow: hidden;
  border-radius: var(--radius-sm);
  background: var(--color-video-bg);
  aspect-ratio: 16 / 9;
}

.card-media img {
  display: block;
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.card-media.contain {
  background: var(--color-surface-muted);
}

.card-media.contain img {
  object-fit: contain;
}

.card-empty {
  display: grid;
  place-items: center;
  width: 100%;
  height: 100%;
  min-height: 90px;
  padding: var(--space-2);
  color: var(--color-text-faint);
  font-size: 11px;
  text-align: center;
}

.card-name {
  overflow: hidden;
  color: var(--color-text-muted);
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.card-note {
  color: var(--color-text-faint);
  font-size: 11px;
}

.small {
  font-size: 11px;
}

.role-select {
  width: 100%;
}

.card-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 2px;
  margin-top: auto;
}

.output-row {
  display: flex;
  flex-wrap: wrap;
  align-items: stretch;
  gap: var(--space-3);
  margin-top: var(--space-3);
}

.output-label {
  display: flex;
  flex: 0 0 72px;
  flex-direction: column;
  justify-content: center;
  gap: 2px;
  color: var(--color-primary);
  font-size: var(--text-sm);
  font-weight: 600;
}

.output-label span {
  color: var(--color-text-faint);
  font-size: 10px;
  font-weight: 400;
}

.target-wide {
  display: grid;
  flex: 1 1 480px;
  max-width: none;
  grid-template-columns: 240px minmax(0, 1fr) auto;
  align-items: center;
  gap: var(--space-3);
}

.target-meta {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
}

.target-wide .card-actions {
  justify-content: flex-end;
  margin-top: 0;
}

.card-open {
  position: absolute;
  top: 6px;
  right: 6px;
  padding: 2px 6px;
  border-radius: 999px;
  background: rgba(15, 23, 42, 0.7);
  color: #fff;
}

@media (max-width: 900px) {
  .card {
    flex: 1 1 100%;
    max-width: none;
  }

  .target-wide {
    grid-template-columns: minmax(0, 1fr);
  }

  .output-label {
    flex-basis: 100%;
  }
}
</style>
