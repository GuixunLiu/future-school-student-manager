<template>
  <div class="kiosk-page">
    <header class="kiosk-header">
      <h1>课堂签到</h1>
      <span class="today">{{ today }}</span>
      <button class="link" @click="reload">刷新</button>
      <button class="link" @click="onLogout">退出</button>
    </header>

    <section v-if="loading" class="hint">正在加载今日课堂…</section>
    <section v-else-if="error" class="hint error">{{ error }}</section>
    <section v-else-if="lessons.length === 0" class="hint">今天没有排课</section>

    <section v-else class="lesson-grid">
      <article v-for="l in lessons" :key="l.id" class="lesson-card" @click="openLesson(l)">
        <div class="row">
          <b>{{ l.class_name }}</b>
          <span v-if="l.course_name" class="course">{{ l.course_name }}</span>
        </div>
        <small>{{ fmtTime(l.starts_at) }} – {{ fmtTime(l.ends_at) }} · {{ l.teacher_name }}</small>
        <div class="numbers">
          <span>应到 {{ l.expected }}</span>
          <span class="ok">实到 {{ l.arrived }}</span>
          <span v-if="l.leave_count" class="warn">请假 {{ l.leave_count }}</span>
        </div>
      </article>
    </section>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { api, ApiError } from '../api/client'
import { useAuthStore } from '../stores/auth'

const router = useRouter()
const auth = useAuthStore()
const lessons = ref([])
const loading = ref(true)
const error = ref('')
const today = new Date().toLocaleDateString('zh-CN', { year: 'numeric', month: 'long', day: 'numeric', weekday: 'long' })

function dayStr(d) {
  const p = (n) => String(n).padStart(2, '0')
  return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate())
}

// 业务口径：固定中国时间（UTC+08:00），不随设备时区漂移
function fmtTime(epochSec) {
  const d = new Date(epochSec * 1000 + 8 * 3600 * 1000)
  const p = (n) => String(n).padStart(2, '0')
  return p(d.getUTCHours()) + ':' + p(d.getUTCMinutes())
}

function openLesson(l) {
  router.push('/lesson/' + l.id)
}

async function reload() {
  loading.value = true
  error.value = ''
  try {
    const res = await api.lessonsByDay(dayStr(new Date()))
    lessons.value = (res && res.items) || []
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) { router.replace('/login'); return }
    error.value = (e && e.message) || '加载失败'
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  if (!auth.ready) await auth.fetchMe()
  if (!auth.isLoggedIn) { router.replace('/login'); return }
  reload()
})

async function onLogout() {
  await auth.logout()
  router.replace('/login')
}
</script>

<style scoped>
.kiosk-page { min-height: 100%; display: grid; grid-template-rows: auto 1fr; }
.kiosk-header {
  display: flex; align-items: center; gap: 14px;
  padding: 18px 28px; background: #0165b3; color: #fff;
}
.kiosk-header h1 { font-size: 20px; }
.today { margin-left: auto; font-size: 14px; opacity: .9; }
.link {
  border: 1px solid #ffffff66; background: transparent; color: #fff;
  border-radius: 10px; padding: 8px 16px; font: inherit; cursor: pointer;
}
.hint { place-self: center; color: #66869d; font-size: 16px; padding: 40px; }
.hint.error { color: #c0392b; }
.lesson-grid {
  display: grid; gap: 16px; padding: 24px 28px;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  align-content: start;
}
.lesson-card {
  background: #fff; border: 1px solid #d8e7f2; border-radius: 16px;
  padding: 20px; display: grid; gap: 10px; cursor: pointer;
}
.lesson-card:hover { border-color: #0165b3; box-shadow: 0 6px 20px #0165b322; }
.lesson-card .row { display: flex; align-items: baseline; gap: 10px; }
.lesson-card b { font-size: 18px; }
.course { font-size: 12px; color: #38799f; background: #eaf4fc; border-radius: 8px; padding: 3px 8px; }
.lesson-card small { color: #7a93a5; }
.numbers { display: flex; gap: 14px; font-size: 14px; color: #557d99; }
.numbers .ok { color: #178a4c; font-weight: 700; }
.numbers .warn { color: #b6810d; }
</style>
