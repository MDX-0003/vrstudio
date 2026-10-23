<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import { useSessionStore } from '@/stores/session'
import { useUiStore } from '@/stores/ui'
import NicknameDialog from './NicknameDialog.vue'
import SideNav from './SideNav.vue'
import TopBar from './TopBar.vue'

const ui = useUiStore()
const session = useSessionStore()
const prompted = ref(false)

onMounted(() => {
  void session.load()
})

watch(
  () => session.needsNickname,
  (needs) => {
    if (needs && !prompted.value) {
      prompted.value = true
      ui.openNicknameDialog()
    }
  },
  { immediate: true },
)
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
    <NicknameDialog v-model="ui.nicknameDialogOpen" />
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
