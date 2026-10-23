<script setup lang="ts">
import SideNav from './SideNav.vue'
import TopBar from './TopBar.vue'
</script>

<template>
  <div class="shell">
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
  display: flex;
  min-height: 100vh;
}

.shell-main {
  display: flex;
  flex: 1;
  flex-direction: column;
  min-width: 0;
}

.shell-content {
  flex: 1;
  padding: var(--space-5) var(--space-6) var(--space-7);
}

@media (max-width: 1024px) {
  .shell-content {
    padding: var(--space-4);
  }
}
</style>
