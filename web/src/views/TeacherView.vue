<template>
  <div class="teacher-page">
    <header class="teacher-header">
      <h1>教师工作台</h1>
      <nav class="tabs">
        <router-link to="/teacher/weekly" class="tab" active-class="on">周报</router-link>
        <router-link to="/teacher/content" class="tab" active-class="on">课堂内容</router-link>
        <router-link v-if="canApprovals" to="/teacher/approvals" class="tab" active-class="on">请假与补签审批</router-link>
        <router-link v-if="isAdmin" to="/teacher/monthly" class="tab" active-class="on">月报</router-link>
        <router-link to="/teacher/notifications" class="tab" active-class="on">通知记录</router-link>
      </nav>
      <nav>
        <span class="me">{{ (auth.me && (auth.me.display_name || auth.me.login)) || '' }}</span>
        <button class="link" @click="onLogout">退出</button>
      </nav>
    </header>
    <router-view />
  </div>
</template>

<script setup>
import { onMounted, computed } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'

const auth = useAuthStore()
const router = useRouter()
const isAdmin = computed(() => auth.roles.includes('admin'))
const canApprovals = computed(() => ['admin', 'assessor', 'teacher'].some((r) => auth.roles.includes(r)))

onMounted(async () => {
  if (!auth.ready) await auth.fetchMe()
  if (!auth.isLoggedIn) router.replace({ path: '/login', query: { redirect: '/teacher' } })
})

async function onLogout() {
  await auth.logout()
  router.replace('/login')
}
</script>

<style scoped>
.teacher-page { min-height: 100%; display: grid; grid-template-rows: auto 1fr; }
.teacher-header {
  display: flex; align-items: center; gap: 28px;
  padding: 12px 28px; background: #fff; border-bottom: 1px solid #e0ecf5;
}
.teacher-header h1 { font-size: 18px; }
nav { display: flex; align-items: center; gap: 14px; }
nav:last-child { margin-left: auto; }
.tabs { gap: 4px; }
.tab {
  padding: 9px 18px; border-radius: 10px; color: #557d99;
  text-decoration: none; font-size: 14px; font-weight: 600;
}
.tab.on { background: #0165b3; color: #fff; }
.me { color: #557d99; font-size: 14px; }
.link {
  border: 1px solid #d4e6f3; background: #f3f8ff; color: #396684;
  border-radius: 10px; padding: 8px 16px; font: inherit; cursor: pointer;
}
</style>
