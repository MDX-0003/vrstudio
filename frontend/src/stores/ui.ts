import { defineStore } from 'pinia'

export const useUiStore = defineStore('ui', {
  state: () => ({
    navCollapsed: false,
    taskDrawerOpen: false,
    nicknameDialogOpen: false,
  }),
  actions: {
    toggleNav() {
      this.navCollapsed = !this.navCollapsed
    },
    openTaskDrawer() {
      this.taskDrawerOpen = true
    },
    closeTaskDrawer() {
      this.taskDrawerOpen = false
    },
    openNicknameDialog() {
      this.nicknameDialogOpen = true
    },
    closeNicknameDialog() {
      this.nicknameDialogOpen = false
    },
  },
})
