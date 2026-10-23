<script setup lang="ts">
import { ref } from 'vue'
import PageHeader from '@/components/common/PageHeader.vue'

const nickname = ref('MDX')
const density = ref('compact')
</script>

<template>
  <div class="page">
    <PageHeader
      title="设置"
      subtitle="原型阶段只展示身份与密度两项；账号、密码与暗色模式留到后续阶段。"
    />
    <div class="panel settings-panel">
      <el-form label-position="top">
        <el-form-item label="显示名">
          <el-input v-model="nickname" style="width: 280px" />
        </el-form-item>
        <el-form-item label="界面密度">
          <el-radio-group v-model="density">
            <el-radio-button value="compact">紧凑</el-radio-button>
            <el-radio-button value="comfortable">舒适</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item label="旧版入口">
          <el-link href="/" target="_blank">打开旧版控制台</el-link>
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
</style>
