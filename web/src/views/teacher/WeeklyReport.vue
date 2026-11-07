<template>
  <main class="content">
    <section class="week-bar">
      <button class="link" @click="shiftWeek(-1)">← 上一周</button>
      <b>{{ report ? report.week_start + ' 起的一周' : monday + ' 起的一周' }}</b>
      <button class="link" :disabled="weekOffset === 0" @click="shiftWeek(1)">下一周 →</button>
      <span v-if="isAdmin" class="scope-tag">全校范围</span>
      <span v-else class="scope-tag mine">我的任课范围</span>
    </section>

    <section v-if="loading" class="hint">正在加载周报…</section>
    <section v-else-if="error" class="hint error">{{ error }}</section>

    <template v-else-if="report">
      <section class="cards">
        <div class="card"><small>加权到课率</small><b>{{ pct(report.rate_percent) }}</b></div>
        <div class="card"><small>应到人次</small><b>{{ report.expected }}</b></div>
        <div class="card"><small>实到人次</small><b class="ok">{{ report.arrived }}</b></div>
        <div class="card"><small>请假人次</small><b class="warn">{{ report.leave_count }}</b></div>
        <div class="card" v-if="report.pending_lessons"><small>未结束课次</small><b>{{ report.pending_lessons }}</b></div>
      </section>

      <section class="panel">
        <h2>班级到课率</h2>
        <table>
          <thead><tr><th>班级</th><th>应到</th><th>实到</th><th>请假</th><th>到课率</th></tr></thead>
          <tbody>
            <tr v-for="c in report.classes" :key="c.id">
              <td>{{ c.name }}</td><td>{{ c.expected }}</td><td>{{ c.arrived }}</td>
              <td>{{ c.leaves }}</td><td>{{ pct(c.rate_percent) }}</td>
            </tr>
            <tr v-if="!report.classes.length"><td colspan="5" class="empty">本周没有已结束的课次</td></tr>
          </tbody>
        </table>
      </section>

      <section class="panel" v-if="isAdmin && report.teachers.length">
        <h2>教师到课率</h2>
        <table>
          <thead><tr><th>教师</th><th>应到</th><th>实到</th><th>请假</th><th>到课率</th></tr></thead>
          <tbody>
            <tr v-for="t in report.teachers" :key="t.id">
              <td>{{ t.name }}</td><td>{{ t.expected }}</td><td>{{ t.arrived }}</td>
              <td>{{ t.leaves }}</td><td>{{ pct(t.rate_percent) }}</td>
            </tr>
          </tbody>
        </table>
      </section>

      <section class="panel" v-if="report.low_hours.length">
        <h2>课时不足学生</h2>
        <table>
          <thead><tr><th>学生</th><th>班级</th><th>剩余课时</th><th>任课教师</th></tr></thead>
          <tbody>
            <tr v-for="s in report.low_hours" :key="s.id">
              <td>{{ s.name }}</td><td>{{ s.class_name }}</td>
              <td class="warn">{{ s.remaining_units / 100 }}</td>
              <td>{{ (s.teachers || []).map((t) => t.display_name).join('、') }}</td>
            </tr>
          </tbody>
        </table>
        <p v-if="report.low_hours_truncated" class="note">名单过长，仅显示前 1000 条</p>
      </section>

      <section class="panel">
        <h2>课次明细</h2>
        <table>
          <thead><tr><th>时间</th><th>应到</th><th>实到</th><th>请假</th><th>到课率</th></tr></thead>
          <tbody>
            <tr v-for="s in report.sessions" :key="s.id">
              <td>{{ fmtSession(s) }}</td>
              <td>{{ s.expected }}</td><td>{{ s.arrived }}</td>
              <td>{{ s.leave_count }}</td><td>{{ pct(s.rate_percent) }}</td>
            </tr>
            <tr v-if="!report.sessions.length"><td colspan="5" class="empty">本周没有已结束的课次</td></tr>
          </tbody>
        </table>
      </section>
    </template>
  </main>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { api, ApiError } from '../../api/client'
import { useAuthStore } from '../../stores/auth'

const auth = useAuthStore()
const router = useRouter()

const weekOffset = ref(0)
const report = ref(null)
const loading = ref(true)
const error = ref('')

const isAdmin = computed(() => auth.roles.includes('admin'))

function mondayOf(offset) {
  const d = new Date()
  const day = (d.getDay() + 6) % 7 // 周一为一周起点
  d.setDate(d.getDate() - day + offset * 7)
  const p = (n) => String(n).padStart(2, '0')
  return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate())
}
const monday = computed(() => mondayOf(weekOffset.value))

function pct(v) { return v === null || v === undefined ? '—' : v + '%' }
function fmtSession(s) {
  // 固定中国时间（UTC+08:00），不随设备时区漂移
  const d = new Date(s.starts_at * 1000 + 8 * 3600 * 1000)
  const p = (n) => String(n).padStart(2, '0')
  return (d.getUTCMonth() + 1) + '月' + d.getUTCDate() + '日 ' + p(d.getUTCHours()) + ':' + p(d.getUTCMinutes())
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    report.value = await api.weeklyReport(monday.value, !isAdmin.value)
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) { router.replace('/login'); return }
    if (e instanceof ApiError && e.status === 403) { error.value = '当前身份没有查看周报的权限'; return }
    error.value = (e && e.message) || '加载失败'
  } finally {
    loading.value = false
  }
}

function shiftWeek(delta) {
  weekOffset.value += delta
  load()
}

onMounted(async () => {
  if (!auth.ready) await auth.fetchMe()
  if (!auth.isLoggedIn) return
  load()
})
</script>

<style scoped>
.content { padding: 22px 28px; display: grid; gap: 20px; align-content: start; max-width: 1100px; }
.week-bar { display: flex; align-items: center; gap: 16px; }
.week-bar b { font-size: 16px; }
.scope-tag { font-size: 12px; color: #38799f; background: #eaf4fc; border-radius: 8px; padding: 4px 10px; }
.scope-tag.mine { color: #8a6b2f; background: #fff7e5; }
.link {
  border: 1px solid #d4e6f3; background: #f3f8ff; color: #396684;
  border-radius: 10px; padding: 8px 16px; font: inherit; cursor: pointer;
}
.link:disabled { opacity: .5; cursor: default; }
.cards { display: flex; gap: 14px; flex-wrap: wrap; }
.card {
  background: #fff; border: 1px solid #d8e7f2; border-radius: 14px;
  padding: 16px 22px; display: grid; gap: 6px; min-width: 130px;
}
.card small { color: #7a93a5; }
.card b { font-size: 24px; color: #103e67; }
.card .ok, b.ok { color: #178a4c; }
.card .warn, b.warn, td.warn { color: #b6810d; }
.panel {
  background: #fff; border: 1px solid #d8e7f2; border-radius: 16px; padding: 20px;
}
.panel h2 { font-size: 16px; margin-bottom: 14px; }
table { width: 100%; border-collapse: collapse; }
th {
  text-align: left; font-size: 12px; color: #426682;
  background: #eaf4fc; padding: 10px 12px;
}
td { padding: 12px; border-bottom: 1px solid #e2edf5; font-size: 14px; }
td.empty { text-align: center; color: #8a9eae; }
.note { margin-top: 10px; font-size: 12px; color: #8a6b2f; }
.hint { padding: 36px; text-align: center; color: #66869d; }
.hint.error { color: #c0392b; }
</style>
