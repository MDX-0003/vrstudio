import { defineStore } from 'pinia'

export const useUiStore = defineStore('ui', {
  state: () => ({
    navCollapsed: false,
    taskDrawerOpen: false,
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
  },
})
