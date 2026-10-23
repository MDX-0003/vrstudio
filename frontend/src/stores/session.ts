import { defineStore } from 'pinia'
import { fetchMe, saveDisplayName, type Me } from '@/api/me'

export const useSessionStore = defineStore('session', {
  state: () => ({
    user: null as Me | null,
    loading: false,
    loaded: false,
    error: '',
  }),
  getters: {
    displayName(state): string {
      return state.user?.display_name || '访客'
    },
    initials(state): string {
      const name = state.user?.display_name || '访客'
      return name.slice(0, 2).toUpperCase()
    },
    needsNickname(state): boolean {
      return state.loaded && (!state.user || state.user.display_name === '访客')
    },
  },
  actions: {
    async load(): Promise<void> {
      if (this.loading) return
      this.loading = true
      this.error = ''
      try {
        this.user = await fetchMe()
      } catch (err) {
        this.error = err instanceof Error ? err.message : '读取使用者失败'
      } finally {
        this.loaded = true
        this.loading = false
      }
    },
    async saveName(name: string): Promise<void> {
      this.user = await saveDisplayName(name)
      this.loaded = true
    },
  },
})
