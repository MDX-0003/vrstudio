<script setup lang="ts">
import { computed, ref } from 'vue'
import type { PromptVariable } from '@/types/case'

const props = defineProps<{
  variables: PromptVariable[]
  rendered: string
  renderError: string
  saving: boolean
  adding: boolean
}>()

const open = defineModel<boolean>('open', { required: true })

const emit = defineEmits<{
  save: []
  input: [key: string, value: string]
  'add-var': [key: string, value: string]
}>()

const newKey = ref('')
const newValue = ref('')

const newKeyUsable = computed(() => {
  const key = newKey.value.trim()
  return /^[A-Za-z_][A-Za-z0-9_]*$/.test(key)
    && !props.variables.some((item) => item.key === key)
})

function submitNewVar(): void {
  if (!newKeyUsable.value) return
  emit('add-var', newKey.value.trim(), newValue.value)
  newKey.value = ''
  newValue.value = ''
}
</script>

<template>
  <div class="panel">
    <div class="panel-head">
      <h2 class="panel-title">提示词</h2>
      <el-button link type="primary" @click="open = true">展开编辑</el-button>
    </div>
    <div class="panel-body">
      <el-alert
        v-if="renderError"
        type="warning"
        :closable="false"
        :title="`提示词暂时无法完整展开：${renderError}`"
        class="prompt-error"
      />
      <div v-if="variables.length" class="prompt-summary">
        <div v-for="item in variables" :key="item.key" class="prompt-line">
          <span class="prompt-label">{{ item.label }}</span>
          <span class="prompt-value">{{ item.value || '未填写' }}</span>
        </div>
      </div>
      <div v-else class="muted">没有可编辑的提示词变量。</div>
      <div class="prompt-note muted">模板正文只读；变量改动会自动保存，提交与预演前会先写入。</div>
    </div>
  </div>

  <el-drawer v-model="open" title="编辑提示词" size="min(1100px, 92vw)">
    <div class="prompt-editor">
      <section class="template-block">
        <h3>完整提示词（只读）</h3>
        <el-alert
          v-if="renderError"
          type="warning"
          :closable="false"
          :title="`提示词暂时无法完整展开：${renderError}`"
          class="prompt-error"
        />
        <pre>{{ rendered || '还没有提示词内容' }}</pre>
      </section>
      <section class="vars-block">
        <el-empty v-if="!variables.length" description="没有可编辑的提示词变量" :image-size="72" />
        <div v-for="item in variables" :key="item.key" class="var-card">
          <div class="var-head">
            <span class="var-label" :title="`内部键名：${item.key}`">{{ item.label }}</span>
          </div>
          <p v-if="item.help" class="var-help">{{ item.help }}</p>
          <el-input
            v-model="item.value"
            type="textarea"
            :autosize="{ minRows: 3 }"
            class="prompt-var-input"
            @input="emit('input', item.key, item.value)"
          />
        </div>
        <div class="var-card add-var">
          <div class="var-head">
            <span class="var-label">添加变量</span>
          </div>
          <p class="var-help">
            模板没覆盖的补充约定可以做成变量再写进正文；素材清单等自动变量由系统生成，不能手工添加。
          </p>
          <div class="add-var-row">
            <el-input
              v-model="newKey"
              placeholder="变量名（英文）"
              class="add-var-key"
            />
            <el-input
              v-model="newValue"
              type="textarea"
              :autosize="{ minRows: 1 }"
              placeholder="变量的值（可留空，之后再填）"
            />
            <el-button
              size="small"
              :disabled="!newKeyUsable"
              :loading="adding"
              @click="submitNewVar"
            >
              添加
            </el-button>
          </div>
          <p v-if="newKey.trim() && !newKeyUsable" class="var-help">
            变量名只支持英文字母、数字与下划线，且不能与现有变量重名。
          </p>
        </div>
      </section>
    </div>
    <template #footer>
      <el-button @click="open = false">关闭</el-button>
      <el-button type="primary" :loading="saving" @click="emit('save')">保存并关闭</el-button>
    </template>
  </el-drawer>
</template>

<style scoped>
.prompt-summary {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.prompt-line {
  display: grid;
  grid-template-columns: minmax(88px, max-content) minmax(0, 1fr);
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

.prompt-editor {
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
}

.prompt-error {
  margin-bottom: var(--space-3);
}

.template-block h3,
.vars-block h3 {
  margin: 0 0 var(--space-2);
  font-size: var(--text-base);
}

.template-block pre {
  max-height: 65vh;
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

.vars-block :deep(textarea) {
  max-height: 40vh;
  overflow-y: auto;
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

.var-help {
  margin: var(--space-1) 0 var(--space-2);
  color: var(--color-text-muted);
  font-size: var(--text-xs);
}

.add-var {
  border-style: dashed;
}

.add-var-row {
  display: grid;
  grid-template-columns: minmax(140px, max-content) minmax(0, 1fr) auto;
  align-items: start;
  gap: var(--space-2);
}

.add-var-row :deep(textarea) {
  min-height: 32px;
}
</style>
