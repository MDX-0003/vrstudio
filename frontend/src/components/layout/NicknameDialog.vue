<script setup lang="ts">
import { ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { useSessionStore } from '@/stores/session'

const props = defineProps<{ modelValue: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [boolean] }>()

const session = useSessionStore()
const name = ref('')
const saving = ref(false)
const error = ref('')

watch(
  () => props.modelValue,
  (open) => {
    if (!open) return
    name.value = session.displayName === '访客' ? '' : session.displayName
    error.value = ''
    saving.value = false
  },
)

async function save(): Promise<void> {
  const value = name.value.trim()
  if (!value) {
    error.value = '先填一个昵称'
    return
  }
  if (value.length > 40) {
    error.value = '昵称不能超过 40 个字符'
    return
  }
  saving.value = true
  error.value = ''
  try {
    await session.saveName(value)
    emit('update:modelValue', false)
    ElMessage.success('昵称已保存')
  } catch (err) {
    error.value = err instanceof Error ? err.message : '保存昵称失败'
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <el-dialog
    :model-value="modelValue"
    title="设置显示名"
    width="380px"
    :close-on-click-modal="false"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <p class="hint muted">昵称只用于显示任务归属与 case 占用者，不做登录。</p>
    <el-input
      v-model="name"
      maxlength="40"
      show-word-limit
      placeholder="例如 MDX、LQ"
      @keyup.enter="save"
    />
    <p v-if="error" class="error">{{ error }}</p>
    <template #footer>
      <el-button @click="emit('update:modelValue', false)">稍后</el-button>
      <el-button type="primary" :loading="saving" @click="save">保存</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.hint {
  margin: 0 0 var(--space-3);
  font-size: var(--text-sm);
}

.error {
  margin: var(--space-2) 0 0;
  color: var(--color-danger);
  font-size: var(--text-sm);
}
</style>
