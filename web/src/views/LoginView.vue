<template>
  <div class="login-page">
    <form class="login-card" @submit.prevent="submit">
      <h1>未来学校学生管理系统</h1>
      <p class="subtitle">请使用机构账号登录</p>
      <label>
        机构编号
        <input v-model.trim="organization" required autocomplete="organization" placeholder="如 01">
      </label>
      <label>
        账号
        <input v-model.trim="login" required autocomplete="username">
      </label>
      <label>
        密码
        <input v-model="password" type="password" required autocomplete="current-password">
      </label>
      <p v-if="error" class="error">{{ error }}</p>
      <button type="submit" :disabled="busy">{{ busy ? '登录中…' : '登录' }}</button>
    </form>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import { ApiError } from '../api/client'

const organization = ref('')
const login = ref('')
const password = ref('')
const error = ref('')
const busy = ref(false)
const router = useRouter()
const route = useRoute()
const auth = useAuthStore()

async function submit() {
  error.value = ''
  busy.value = true
  try {
    await auth.login(organization.value, login.value, password.value)
    router.replace(route.query.redirect || '/')
  } catch (e) {
    if (e instanceof ApiError) {
      if (e.status === 401) error.value = '账号或密码不正确'
      else if (e.status === 429) error.value = '尝试过于频繁，请稍后再试'
      else if (e.status === 0) error.value = '无法连接服务器，请检查网络'
      else error.value = e.message || '登录失败'
    } else {
      error.value = '登录失败，请稍后再试'
    }
  } finally {
    busy.value = false
  }
}
</script>

<style scoped>
.login-page {
  min-height: 100%;
  display: grid;
  place-items: center;
  background: linear-gradient(135deg, #0165b3, #0a84d6);
}
.login-card {
  width: min(400px, 92vw);
  background: #fff;
  border-radius: 18px;
  padding: 36px 32px;
  display: grid;
  gap: 18px;
  box-shadow: 0 18px 48px #0165b340;
}
h1 { font-size: 22px; }
.subtitle { color: #66869d; font-size: 14px; margin-top: -12px; }
label { display: grid; gap: 6px; font-size: 14px; color: #396684; }
input {
  min-height: 46px;
  border: 1px solid #d4e6f3;
  border-radius: 12px;
  padding: 0 14px;
  font: inherit;
  color: #164e77;
}
input:focus { outline: 2px solid #0165b355; border-color: #0165b3; }
button {
  min-height: 48px;
  border: 0;
  border-radius: 12px;
  background: #0165b3;
  color: #fff;
  font: inherit;
  font-weight: 700;
  cursor: pointer;
}
button:disabled { opacity: .6; cursor: default; }
.error { color: #c0392b; font-size: 13px; }
</style>
