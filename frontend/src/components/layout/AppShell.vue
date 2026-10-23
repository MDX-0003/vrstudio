<script setup lang="ts">
import { useUiStore } from '@/stores/ui'
import SideNav from './SideNav.vue'
import TopBar from './TopBar.vue'

const ui = useUiStore()
</script>

<template>
  <div class="shell" :class="{ 'nav-collapsed': ui.navCollapsed }">
    <SideNav />
    <div class="shell-main">
      <TopBar />
      <main class="shell-content">
        <RouterView />
      </main>
    </div>
  </div>
</template>

<style scoped>
.shell {
  --nav-current-width: var(--nav-width);
  display: flex;
  min-height: 100vh;
}

.shell.nav-collapsed {
  --nav-current-width: var(--nav-width-collapsed);
}

.shell-main {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
}

.shell-content {
  flex: 1;
  padding: var(--space-5) var(--space-6) 0;
}

@media (max-width: 1024px) {
  .shell-content {
    padding: var(--space-4) var(--space-4) 0;
  }
}
</style>
