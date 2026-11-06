import { defineStore } from 'pinia'
import { api, ApiError } from '../api/client'

export const useAuthStore = defineStore('auth', {
  state: () => ({
    me: null,
    ready: false
  }),
  getters: {
    isLoggedIn: (s) => !!s.me,
    roles: (s) => (s.me && s.me.roles) || []
  },
  actions: {
    async login(organization, login, password) {
      const res = await api.login(organization, login, password)
      // 令牌只在登录响应里返回一次
      sessionStorage.setItem('fs_token', res.access_token)
      await this.fetchMe()
    },
    async fetchMe() {
      try {
        this.me = await api.me()
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) {
          sessionStorage.removeItem('fs_token')
          this.me = null
        } else {
          throw e
        }
      } finally {
        this.ready = true
      }
    },
    async logout() {
      try { await api.logout() } catch (e) { /* 忽略网络错误 */ }
      sessionStorage.removeItem('fs_token')
      this.me = null
    }
  }
})
