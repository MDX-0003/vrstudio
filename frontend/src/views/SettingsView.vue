<script setup lang="ts">
import { ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import PageHeader from '@/components/common/PageHeader.vue'
import { useSessionStore } from '@/stores/session'

const session = useSessionStore()
const nickname = ref('')
const saving = ref(false)

watch(
  () => session.user?.display_name,
  (name) => {
    if (name && name !== '访客') nickname.value = name
  },
  { immediate: true },
)

async function saveNickname(): Promise<void> {
  const name = nickname.value.trim()
  if (!name) {
    ElMessage.warning('显示名不能为空')
    return
  }
  saving.value = true
  try {
    await session.saveName(name)
    ElMessage.success('显示名已保存')
  } catch (err) {
    ElMessage.error(err instanceof Error ? err.message : '保存显示名失败')
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div class="page">
    <PageHeader
      title="设置"
      subtitle="显示名会保存到当前使用者；界面密度、暗色模式与账号密码留到后续阶段。"
    />
    <div class="panel settings-panel">
      <el-form label-position="top">
        <el-form-item label="显示名">
          <div class="nickname-row">
            <el-input v-model="nickname" style="width: 280px" />
            <el-button type="primary" :loading="saving" @click="saveNickname">保存</el-button>
          </div>
        </el-form-item>
      </el-form>
    </div>
  </div>
</template>

<style scoped>
.settings-panel {
  max-width: 560px;
  padding: var(--space-5);
}

.nickname-row {
  display: flex;
  gap: var(--space-2);
}
</style>
