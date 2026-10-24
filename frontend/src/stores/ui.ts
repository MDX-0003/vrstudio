import { defineStore } from 'pinia'

export const useUiStore = defineStore('ui', {
  state: () => ({
    navCollapsed: false,
    nicknameDialogOpen: false,
  }),
  actions: {
    toggleNav() {
      this.navCollapsed = !this.navCollapsed
    },
    openNicknameDialog() {
      this.nicknameDialogOpen = true
    },
    closeNicknameDialog() {
      this.nicknameDialogOpen = false
    },
  },
})
