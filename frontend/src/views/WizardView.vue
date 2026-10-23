<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { Check, Picture, Plus, Refresh, VideoCamera, VideoPlay } from '@element-plus/icons-vue'
import { flowMeta, mockAssets, mockCandidates, mockPromptVariables } from '@/mocks/case'
import type { AssetRow, CandidateRound, Flow, PromptVariable } from '@/types/case'

const route = useRoute()
const router = useRouter()

const caseName = computed(() => String(route.params.name ?? 'dacne'))
const activeStep = computed(() => String(route.params.step ?? 'generate'))
const flow = ref<Flow>('patch')

const steps = [
  { key: 'pick', label: '选区' },
  { key: 'extract', label: '抽取' },
  { key: 'keyframe', label: '首尾帧' },
  { key: 'generate', label: '生成' },
  { key: 'merge', label: '合并' },
  { key: 'compare', label: '对比' },
]

const activeIndex = computed(() => {
  const index = steps.findIndex((step) => step.key === activeStep.value)
  return index < 0 ? 3 : index
})

const assets = ref<AssetRow[]>(mockAssets.map((asset) => ({ ...asset })))
const promptVars = ref<PromptVariable[]>(mockPromptVariables.map((item) => ({ ...item })))
const candidates = ref<CandidateRound[]>(mockCandidates.map((item) => ({ ...item })))

const selectedCount = computed(() => assets.value.filter((asset) => asset.selected).length)
const promptOpen = ref(false)
const status = ref<'未预演' | '预演通过' | '已提交'>('未预演')

const mode = ref('firstLastFrame')
const ratio = ref('16:9')
const resolution = ref('1080p')
const scale = ref('auto')
const duration = ref('auto')
const project = ref('2026大秦寻仙VR')
const episode = ref('第 1 集 · 姜宣夜')
const session = ref('cli会话_测试')

const templateText = computed(() =>
  [
    '【素材】<Video1> 是本次唯一的编辑母版；<Image1> 提供人物外观；mask 由装配器自动附带。',
    '【任务】只编辑 <Video1> 中指定的主体，其余画面保持原样。',
    `【主体】${promptVars.value[0]?.value ?? ''}`,
    `【重绘结果】${promptVars.value[1]?.value ?? ''}`,
    `【情节与动作】${promptVars.value[2]?.value ?? ''}`,
    `【必须保持】${promptVars.value[4]?.value ?? ''}`,
    `【允许变化】${promptVars.value[5]?.value ?? ''}`,
  ].join('\n\n'),
)

function selectStep(key: string): void {
  router.push({ name: 'case', params: { name: caseName.value, step: key } })
}

function openPrompt(): void {
  promptOpen.value = true
}

function savePrompt(): void {
  promptOpen.value = false
  status.value = '未预演'
  ElMessage.success('提示词已保存，预演状态回到未预演')
}

function dryRun(): void {
  status.value = '预演通过'
  ElMessage.success('预演通过 · 当前选择未改动')
}

function submit(): void {
  status.value = '已提交'
  ElMessage.success('已提交生成，排队第 1 位')
}

function refreshSessions(): void {
  ElMessage.success('会话列表已刷新（原型阶段，Phase 1 接 /api/sessions）')
}

function statusTone(): string {
  if (status.value === '预演通过') return 'done'
  if (status.value === '已提交') return 'queued'
  return ''
}
</script>

<template>
  <div class="page wizard-page">
    <div class="wizard-head">
      <div>
        <div class="crumbs muted">Case / {{ caseName }}</div>
        <h1>
          {{ caseName }}
          <el-tag :type="flowMeta[flow].tag" effect="light">{{ flowMeta[flow].label }}</el-tag>
        </h1>
      </div>
      <div class="save-state">
        <span class="status-dot done" />
        已保存
      </div>
    </div>

    <nav class="step-nav" aria-label="流程步骤">
      <button
        v-for="(step, index) in steps"
        :key="step.key"
        type="button"
        class="step"
        :class="{ active: step.key === activeStep, done: index < activeIndex }"
        @click="selectStep(step.key)"
      >
        <span class="step-index">
          <el-icon v-if="index < activeIndex" :size="12"><Check /></el-icon>
          <template v-else>{{ index + 1 }}</template>
        </span>
        {{ step.label }}
      </button>
    </nav>

    <div v-if="activeStep !== 'generate'" class="panel placeholder-panel">
      <el-empty description="Phase 0 原型只实现生成页；选区、抽取、首尾帧、合并与对比在 Phase 2–3 迁移" />
    </div>

    <template v-else>
      <div class="gen-layout">
        <section class="gen-main">
          <div class="panel">
            <div class="panel-head">
              <h2 class="panel-title">素材与本次提交</h2>
              <span class="muted">{{ selectedCount }} 项已勾选</span>
            </div>
            <div class="panel-body asset-list">
              <div
                v-for="asset in assets"
                :key="asset.id"
                class="asset-row"
                :class="{ dim: !asset.selected }"
              >
                <el-checkbox v-model="asset.selected" aria-label="参与本次提交" />
                <div class="asset-thumb" :style="{ background: asset.tone }">
                  <el-icon :size="20">
                    <VideoCamera v-if="asset.kind === 'video'" />
                    <Picture v-else />
                  </el-icon>
                </div>
                <div class="asset-main">
                  <div class="asset-name">{{ asset.name }}</div>
                  <div class="asset-meta">{{ asset.meta }}</div>
                </div>
                <el-select v-model="asset.role" size="small" style="width: 128px">
                  <el-option label="编辑母版" value="编辑母版" />
                  <el-option label="主体参考" value="主体参考" />
                  <el-option label="场景参考" value="场景参考" />
                </el-select>
              </div>
              <el-button class="add-asset" text :icon="Plus">添加视频或图片</el-button>
            </div>
          </div>

          <div class="panel">
            <div class="panel-head">
              <h2 class="panel-title">提示词</h2>
              <el-button link type="primary" @click="openPrompt">展开编辑</el-button>
            </div>
            <div class="panel-body">
              <div class="prompt-chips">
                <span v-for="item in promptVars" :key="item.key" class="prompt-chip">
                  {{ item.label }}
                </span>
              </div>
              <div class="prompt-summary">
                <div class="prompt-line">
                  <span class="prompt-label">{{ promptVars[0]?.label }}</span>
                  <span>{{ promptVars[0]?.value }}</span>
                </div>
                <div class="prompt-line">
                  <span class="prompt-label">{{ promptVars[2]?.label }}</span>
                  <span>{{ promptVars[2]?.value }}</span>
                </div>
              </div>
              <div class="prompt-note muted">
                模板正文只读；变量改动会自动保存，提交与预演前会先写入。
              </div>
            </div>
          </div>
        </section>

        <aside class="gen-side">
          <div class="panel">
            <div class="panel-head">
              <h2 class="panel-title">最近成片</h2>
              <el-tag type="success" effect="light" size="small">可用</el-tag>
            </div>
            <div class="panel-body">
              <div class="output-frame">
                <el-icon :size="34"><VideoPlay /></el-icon>
                <span>16:31 · 1920×1080 · 9.83 秒</span>
              </div>
              <div class="output-actions">
                <el-button type="primary" plain size="small">选用成片</el-button>
                <span class="muted">采用后结果写回 case</span>
              </div>
            </div>
          </div>

          <div class="panel">
            <div class="panel-head">
              <h2 class="panel-title">候选</h2>
              <span class="muted">{{ candidates.length }} 批</span>
            </div>
            <div class="panel-body candidate-list">
              <div v-for="round in candidates" :key="round.id" class="candidate">
                <div class="candidate-head">
                  <span>{{ round.title }}</span>
                  <span class="muted mono">{{ round.time }}</span>
                </div>
                <div class="candidate-grid">
                  <div
                    v-for="n in round.images"
                    :key="n"
                    class="candidate-tile"
                    :class="{ picked: round.status === 'picked' && n === 1 }"
                  >
                    <el-icon :size="18"><Picture /></el-icon>
                  </div>
                </div>
                <div class="candidate-foot">
                  <el-tag
                    :type="
                      round.status === 'failed'
                        ? 'danger'
                        : round.status === 'picked'
                          ? 'success'
                          : 'info'
                    "
                    effect="light"
                    size="small"
                  >
                    {{ round.status === 'failed' ? '失败' : round.status === 'picked' ? '已定稿' : '待挑选' }}
                  </el-tag>
                  <el-button link type="primary" size="small">查看</el-button>
                </div>
              </div>
            </div>
          </div>
        </aside>
      </div>

      <div class="submit-bar">
        <div class="submit-inner">
          <div class="submit-row">
            <label class="ctl">
              <span>模式</span>
              <el-select v-model="mode" size="small" style="width: 168px">
                <el-option label="firstLastFrame · 首尾帧" value="firstLastFrame" />
                <el-option label="VideoEdit · 结构锁定编辑" value="videoEdit" />
                <el-option label="allMediaRef · 多素材参考" value="allMediaRef" />
              </el-select>
            </label>
            <label class="ctl">
              <span>比例</span>
              <el-select v-model="ratio" size="small" style="width: 100px">
                <el-option label="16:9" value="16:9" />
                <el-option label="21:9" value="21:9" />
                <el-option label="跟随输入" value="auto" />
              </el-select>
            </label>
            <label class="ctl">
              <span>输出分辨率</span>
              <el-select v-model="resolution" size="small" style="width: 110px">
                <el-option label="480p" value="480p" />
                <el-option label="720p" value="720p" />
                <el-option label="1080p" value="1080p" />
              </el-select>
            </label>
            <label class="ctl">
              <span>输入分辨率</span>
              <el-select v-model="scale" size="small" style="width: 100px">
                <el-option label="自动" value="auto" />
                <el-option label="原始尺寸" value="full" />
                <el-option label="一半" value="half" />
              </el-select>
            </label>
            <label class="ctl">
              <span>时长</span>
              <el-select v-model="duration" size="small" style="width: 140px">
                <el-option label="自动（约 10 秒）" value="auto" />
                <el-option label="4 秒" value="4" />
                <el-option label="10 秒" value="10" />
              </el-select>
            </label>
          </div>
          <div class="submit-row">
            <label class="ctl">
              <span>项目</span>
              <el-select v-model="project" size="small" style="width: 170px">
                <el-option label="2026大秦寻仙VR" value="2026大秦寻仙VR" />
              </el-select>
            </label>
            <label class="ctl">
              <span>剧集</span>
              <el-select v-model="episode" size="small" style="width: 160px">
                <el-option label="第 1 集 · 姜宣夜" value="第 1 集 · 姜宣夜" />
              </el-select>
            </label>
            <label class="ctl">
              <span>会话</span>
              <el-select v-model="session" size="small" style="width: 150px">
                <el-option label="cli会话_测试" value="cli会话_测试" />
              </el-select>
            </label>
            <el-button size="small" :icon="Refresh" @click="refreshSessions">刷新</el-button>
            <span class="muted quota-hint">已获取 1 个项目</span>
            <div class="submit-actions">
              <span class="run-state">
                <span class="status-dot" :class="statusTone()" />
                {{ status }}
              </span>
              <el-button size="small" @click="dryRun">预演检查（不扣费）</el-button>
              <el-button type="primary" size="small" @click="submit">提交生成</el-button>
            </div>
          </div>
        </div>
      </div>
    </template>

    <el-drawer v-model="promptOpen" title="编辑提示词" size="760px">
      <div class="prompt-editor">
        <section class="template-block">
          <h3>模板正文（只读）</h3>
          <pre>{{ templateText }}</pre>
        </section>
        <section class="vars-block">
          <div v-for="item in promptVars" :key="item.key" class="var-card">
            <div class="var-head">
              <span class="var-label">{{ item.label }}</span>
              <span class="var-key mono">{{ item.key }}</span>
            </div>
            <p class="var-help">{{ item.help }}</p>
            <el-input
              v-model="item.value"
              type="textarea"
              :autosize="{ minRows: 3, maxRows: 8 }"
            />
          </div>
        </section>
      </div>
      <template #footer>
        <el-button @click="promptOpen = false">取消</el-button>
        <el-button type="primary" @click="savePrompt">保存并关闭</el-button>
      </template>
    </el-drawer>
  </div>
</template>

<style scoped>
.wizard-page {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  padding-bottom: 160px;
}

.wizard-head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: var(--space-4);
}

.crumbs {
  font-size: var(--text-sm);
}

.wizard-head h1 {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  margin: var(--space-1) 0 0;
  font-size: var(--text-2xl);
  font-weight: 650;
  letter-spacing: -0.01em;
}

.save-state {
  display: inline-flex;
  align-items: center;
  color: var(--color-text-muted);
  font-size: var(--text-sm);
}

.step-nav {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-1);
  padding: var(--space-1);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
}

.step {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  height: 36px;
  padding: 0 var(--space-3);
  border: 0;
  border-radius: var(--radius-md);
  background: transparent;
  color: var(--color-text-muted);
  font-size: var(--text-sm);
  cursor: pointer;
}

.step:hover {
  background: var(--color-surface-muted);
  color: var(--color-text);
}

.step.done {
  color: var(--color-success);
}

.step.active {
  background: var(--color-primary-soft);
  color: var(--color-primary);
  font-weight: 600;
}

.step-index {
  display: grid;
  place-items: center;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: var(--color-surface-muted);
  font-size: 11px;
  font-weight: 600;
}

.step.active .step-index {
  background: var(--color-primary);
  color: #fff;
}

.step.done .step-index {
  background: var(--color-success-soft);
  color: var(--color-success);
}

.placeholder-panel {
  padding: var(--space-8);
}

.gen-layout {
  display: grid;
  grid-template-columns: minmax(0, 1.55fr) minmax(320px, 1fr);
  gap: var(--space-4);
  align-items: start;
}

.gen-main,
.gen-side {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  min-width: 0;
}

.asset-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.asset-row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-2) var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  transition: background var(--duration-fast) ease, opacity var(--duration-fast) ease;
}

.asset-row:hover {
  background: var(--color-surface-muted);
}

.asset-row.dim {
  opacity: 0.55;
}

.asset-thumb {
  display: grid;
  place-items: center;
  width: 56px;
  height: 40px;
  flex: 0 0 auto;
  border-radius: var(--radius-sm);
  color: rgba(255, 255, 255, 0.9);
}

.asset-main {
  flex: 1;
  min-width: 0;
}

.asset-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 600;
}

.asset-meta {
  color: var(--color-text-muted);
  font-size: var(--text-xs);
}

.add-asset {
  align-self: flex-start;
  margin-top: var(--space-1);
}

.prompt-chips {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-1);
  margin-bottom: var(--space-3);
}

.prompt-chip {
  padding: 2px 8px;
  border-radius: 999px;
  background: var(--color-surface-muted);
  color: var(--color-text-muted);
  font-size: var(--text-xs);
}

.prompt-summary {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.prompt-line {
  display: grid;
  grid-template-columns: 76px minmax(0, 1fr);
  gap: var(--space-2);
  color: var(--color-text);
  font-size: var(--text-sm);
  line-height: 1.55;
}

.prompt-line > span:last-child {
  display: -webkit-box;
  overflow: hidden;
  -webkit-box-orient: vertical;
  -webkit-line-clamp: 2;
}

.prompt-label {
  color: var(--color-text-muted);
}

.prompt-note {
  margin-top: var(--space-3);
  font-size: var(--text-xs);
}

.output-frame {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-2);
  width: 100%;
  max-width: 560px;
  aspect-ratio: 16 / 9;
  border-radius: var(--radius-md);
  background:
    radial-gradient(circle at 30% 25%, rgba(37, 99, 235, 0.35), transparent 55%),
    linear-gradient(135deg, #0f172a, #334155);
  color: rgba(255, 255, 255, 0.86);
  font-size: var(--text-xs);
}

.output-actions {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  max-width: 560px;
  margin-top: var(--space-3);
  font-size: var(--text-xs);
}

.candidate-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.candidate {
  padding: var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}

.candidate-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  font-size: var(--text-sm);
  font-weight: 600;
}

.candidate-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: var(--space-2);
  margin: var(--space-2) 0;
}

.candidate-tile {
  display: grid;
  place-items: center;
  aspect-ratio: 16 / 9;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-surface-muted);
  color: var(--color-text-faint);
}

.candidate-tile.picked {
  border-color: var(--color-success);
  background: var(--color-success-soft);
  color: var(--color-success);
}

.candidate-foot {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.submit-bar {
  position: fixed;
  left: var(--nav-current-width, var(--nav-width));
  right: 0;
  bottom: 0;
  z-index: 20;
  background: rgba(255, 255, 255, 0.97);
  border-top: 1px solid var(--color-border);
  box-shadow: 0 -8px 24px rgba(15, 23, 42, 0.08);
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

.submit-actions {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  margin-left: auto;
}

.ctl {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  color: var(--color-text-muted);
  font-size: var(--text-xs);
  white-space: nowrap;
}

.run-state {
  display: inline-flex;
  align-items: center;
  color: var(--color-text-muted);
  font-size: var(--text-sm);
  white-space: nowrap;
}

.prompt-editor {
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
}

.template-block h3,
.vars-block h3 {
  margin: 0 0 var(--space-2);
  font-size: var(--text-base);
}

.template-block pre {
  max-height: 260px;
  margin: 0;
  padding: var(--space-3);
  overflow: auto;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface-muted);
  color: var(--color-text-muted);
  font-family: var(--font-sans);
  font-size: var(--text-sm);
  line-height: 1.65;
  white-space: pre-wrap;
}

.vars-block {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.var-card {
  padding: var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}

.var-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-2);
}

.var-label {
  font-weight: 600;
}

.var-key {
  color: var(--color-text-faint);
  font-size: var(--text-xs);
}

.var-help {
  margin: var(--space-1) 0 var(--space-2);
  color: var(--color-text-muted);
  font-size: var(--text-xs);
}

@media (max-width: 1280px) {
  .gen-layout {
    grid-template-columns: minmax(0, 1fr);
  }
}

@media (max-width: 1024px) {
  .submit-inner {
    padding: var(--space-2) var(--space-4);
  }
}
</style>
