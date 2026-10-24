<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Bell, ArrowLeft } from '@element-plus/icons-vue'
import { stepLabel } from '@/constants/steps'
import { useSessionStore } from '@/stores/session'
import { useUiStore } from '@/stores/ui'

const route = useRoute()
const router = useRouter()
const session = useSessionStore()
const ui = useUiStore()

const isCasePage = computed(() => route.name === 'case')
const caseName = computed(() => String(route.params.name ?? ''))
const step = computed(() => String(route.params.step ?? 'generate'))

function goBack(): void {
  router.push('/cases')
}
</script>

<template>
  <header class="top-bar">
    <div class="top-left">
      <template v-if="isCasePage">
        <el-button text :icon="ArrowLeft" @click="goBack">Case 列表</el-button>
        <span class="title">{{ caseName }} · {{ stepLabel(step) }}</span>
      </template>
    </div>
    <div class="top-right">
      <el-button text :icon="Bell" @click="router.push('/tasks')">任务</el-button>
      <button
        class="user-chip"
        type="button"
        :title="`修改昵称（当前：${session.displayName}）`"
        @click="ui.openNicknameDialog()"
      >
        {{ session.initials }}
      </button>
    </div>
  </header>
</template>

<style scoped>
.top-bar {
  position: sticky;
  top: 0;
  z-index: 10;
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: var(--topbar-height);
  padding: 0 var(--space-6);
  background: rgba(255, 255, 255, 0.92);
  border-bottom: 1px solid var(--color-border);
  backdrop-filter: blur(8px);
}

.top-left,
.top-right {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  min-width: 0;
}

.title {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: var(--text-lg);
  font-weight: 600;
}

.user-chip {
  display: grid;
  place-items: center;
  width: 32px;
  height: 32px;
  padding: 0;
  border-radius: 50%;
  background: var(--color-surface-muted);
  border: 1px solid var(--color-border);
  color: var(--color-text-muted);
  font-family: inherit;
  font-size: var(--text-xs);
  font-weight: 600;
  cursor: pointer;
}
</style>
