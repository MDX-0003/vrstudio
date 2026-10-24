<script setup lang="ts">
import type { PromptVariable } from '@/types/case'

defineProps<{
  variables: PromptVariable[]
  rendered: string
  renderError: string
  saving: boolean
}>()

const open = defineModel<boolean>('open', { required: true })

const emit = defineEmits<{
  save: []
  input: [key: string, value: string]
}>()
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
        :title="renderError"
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
          :title="renderError"
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
</style>
