<script setup lang="ts">
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { Back, Expand, Fold, FolderOpened, List, Setting } from '@element-plus/icons-vue'
import { useUiStore } from '@/stores/ui'

const ui = useUiStore()
const route = useRoute()

const navItems = [
  { path: '/cases', label: 'Case 列表', icon: FolderOpened },
  { path: '/tasks', label: '任务中心', icon: List },
  { path: '/settings', label: '设置', icon: Setting },
]

const collapsed = computed(() => ui.navCollapsed)

function isActive(path: string): boolean {
  return route.path === path || route.path.startsWith(`${path}/`)
}

function goOldConsole(): void {
  window.location.assign('/')
}
</script>

<template>
  <aside class="side-nav" :class="{ collapsed }">
    <div class="brand">
      <span class="brand-mark">vr</span>
      <span v-if="!collapsed" class="brand-name">vrstudio</span>
    </div>

    <nav class="nav-list" aria-label="主导航">
      <RouterLink
        v-for="item in navItems"
        :key="item.path"
        :to="item.path"
        class="nav-item"
        :class="{ active: isActive(item.path) }"
        :title="collapsed ? item.label : undefined"
      >
        <el-icon :size="18"><component :is="item.icon" /></el-icon>
        <span v-if="!collapsed">{{ item.label }}</span>
      </RouterLink>
    </nav>

    <div class="side-bottom">
      <button class="nav-item as-button" type="button" @click="goOldConsole">
        <el-icon :size="18"><Back /></el-icon>
        <span v-if="!collapsed">旧版入口</span>
      </button>
      <button class="nav-item as-button" type="button" @click="ui.toggleNav()">
        <el-icon :size="18">
          <Expand v-if="collapsed" />
          <Fold v-else />
        </el-icon>
        <span v-if="!collapsed">收起导航</span>
      </button>
    </div>
  </aside>
</template>

<style scoped>
.side-nav {
  position: sticky;
  top: 0;
  display: flex;
  flex-direction: column;
  width: var(--nav-width);
  height: 100vh;
  padding: var(--space-3);
  background: var(--color-surface);
  border-right: 1px solid var(--color-border);
  transition: width var(--duration-base) ease;
}

.side-nav.collapsed {
  width: var(--nav-width-collapsed);
}

.brand {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  height: 40px;
  padding: 0 var(--space-2);
  margin-bottom: var(--space-4);
}

.brand-mark {
  display: grid;
  place-items: center;
  width: 28px;
  height: 28px;
  border-radius: var(--radius-md);
  background: var(--color-primary);
  color: #fff;
  font-size: var(--text-sm);
  font-weight: 700;
  letter-spacing: 0.02em;
}

.brand-name {
  font-size: var(--text-lg);
  font-weight: 650;
  letter-spacing: -0.01em;
}

.nav-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.nav-item {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  height: 38px;
  padding: 0 var(--space-3);
  border-radius: var(--radius-md);
  color: var(--color-text-muted);
  font-size: var(--text-base);
  transition:
    background var(--duration-fast) ease,
    color var(--duration-fast) ease;
}

.nav-item:hover {
  background: var(--color-surface-muted);
  color: var(--color-text);
}

.nav-item.active {
  background: var(--color-primary-soft);
  color: var(--color-primary);
  font-weight: 600;
}

.as-button {
  width: 100%;
  border: 0;
  background: transparent;
  cursor: pointer;
  text-align: left;
}

.side-bottom {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  margin-top: auto;
  padding-top: var(--space-4);
  border-top: 1px solid var(--color-border);
}
</style>
