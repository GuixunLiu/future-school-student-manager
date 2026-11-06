<template>
  <main class="monthly-page">
    <section class="month-bar">
      <div>
        <h2>运营月报</h2>
        <p>按学生去重统计新增与续费；续费次数单独列示。</p>
      </div>
      <label>
        报表月份
        <input v-model="month" type="month" @change="load">
      </label>
    </section>

    <p v-if="loading" class="hint">正在加载月报…</p>
    <p v-else-if="error" class="hint error">{{ error }}</p>

    <template v-else-if="report">
      <div class="period-state" :class="{ final: report.final_period }">
        {{ report.final_period ? '该月已结束，当前为完整月度数据' : '该月尚未结束，当前为进行中数据' }}
      </div>
      <section class="cards">
        <div class="card"><small>新增学生</small><b>{{ report.new_students }}</b><span>人（去重）</span></div>
        <div class="card"><small>续费学生</small><b>{{ report.renewal_students }}</b><span>人（去重）</span></div>
        <div class="card"><small>续费记录</small><b class="accent">{{ report.renewal_events }}</b><span>笔</span></div>
      </section>

      <section class="panel">
        <div class="panel-heading">
          <h3>活动明细</h3>
          <span>共 {{ report.items.length }} 条</span>
        </div>
        <table>
          <thead><tr><th>发生时间</th><th>学生</th><th>班级</th><th>类型</th></tr></thead>
          <tbody>
            <tr v-for="item in report.items" :key="item.id">
              <td>{{ fmtTime(item.occurred_at) }}</td>
              <td>{{ item.name }}</td>
              <td>{{ item.class_name }}</td>
              <td><span class="kind" :class="item.kind">{{ item.kind === 'new' ? '新增' : '续费' }}</span></td>
            </tr>
            <tr v-if="!report.items.length"><td colspan="4" class="empty">本月没有新增或续费活动</td></tr>
          </tbody>
        </table>
      </section>
      <p class="note">月报只汇总活动记录，不会据此调整学生课时。课时尚需以签到与课时明细为准。</p>
    </template>
  </main>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { api, ApiError } from '../../api/client'
import { useAuthStore } from '../../stores/auth'

const auth = useAuthStore()
const router = useRouter()
const month = ref(chinaMonth())
const report = ref(null)
const loading = ref(true)
const error = ref('')

function chinaMonth() {
  const d = new Date(Date.now() + 8 * 3600 * 1000)
  return d.getUTCFullYear() + '-' + String(d.getUTCMonth() + 1).padStart(2, '0')
}
function fmtTime(epochSec) {
  const d = new Date(epochSec * 1000 + 8 * 3600 * 1000)
  const p = (n) => String(n).padStart(2, '0')
  return p(d.getUTCMonth() + 1) + '月' + p(d.getUTCDate()) + '日 ' + p(d.getUTCHours()) + ':' + p(d.getUTCMinutes())
}
async function load() {
  if (!month.value) return
  loading.value = true
  error.value = ''
  try {
    report.value = await api.monthlyReport(month.value)
  } catch (e) {
    report.value = null
    if (e instanceof ApiError && e.status === 401) { router.replace('/login'); return }
    if (e instanceof ApiError && e.status === 403) { error.value = '只有管理员可以查看运营月报'; return }
    error.value = (e && e.message) || '月报加载失败'
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  if (!auth.ready) await auth.fetchMe()
  if (!auth.isLoggedIn) return
  if (!auth.roles.includes('admin')) {
    loading.value = false
    error.value = '只有管理员可以查看运营月报'
    return
  }
  load()
})
</script>

<style scoped>
.monthly-page { padding: 26px 32px; display: grid; gap: 20px; align-content: start; max-width: 1100px; }
.month-bar { display: flex; align-items: center; justify-content: space-between; gap: 20px; }
.month-bar h2 { font-size: 22px; }
.month-bar p { color: #718da1; font-size: 13px; margin-top: 6px; }
.month-bar label { display: flex; align-items: center; gap: 10px; color: #426682; font-size: 13px; }
.month-bar input { border: 1px solid #cbdfea; border-radius: 9px; padding: 9px 10px; font: inherit; color: #103e67; background: #fff; }
.period-state { color: #8a641c; background: #fff6e7; border: 1px solid #f0dfba; border-radius: 10px; padding: 11px 14px; font-size: 13px; }
.period-state.final { color: #23794a; background: #edf8f1; border-color: #d5ecdd; }
.cards { display: flex; gap: 14px; flex-wrap: wrap; }
.card { background: #fff; border: 1px solid #d8e7f2; border-radius: 14px; padding: 18px 24px; display: grid; gap: 6px; min-width: 170px; }
.card small { color: #66869d; }
.card b { font-size: 30px; }
.card b.accent { color: #178a4c; }
.card span { color: #8a9ba8; font-size: 12px; }
.panel { background: #fff; border: 1px solid #d8e7f2; border-radius: 16px; padding: 20px; overflow: hidden; }
.panel-heading { display: flex; align-items: center; justify-content: space-between; margin-bottom: 14px; }
.panel-heading h3 { font-size: 16px; }
.panel-heading span { color: #8a9ba8; font-size: 12px; }
table { width: 100%; border-collapse: collapse; }
th { text-align: left; font-size: 12px; color: #426682; background: #eaf4fc; padding: 10px 12px; }
td { padding: 12px; border-bottom: 1px solid #e2edf5; font-size: 14px; }
td.empty { text-align: center; color: #8a9eae; padding: 36px; }
.kind { border-radius: 6px; padding: 4px 9px; font-size: 12px; color: #24678e; background: #eaf4fc; }
.kind.renewal { color: #23794a; background: #e5f6ed; }
.note { color: #718da1; font-size: 12px; line-height: 1.6; }
.hint { padding: 40px; text-align: center; color: #7890a2; background: #fff; border-radius: 14px; }
.hint.error { color: #bd3c32; }
@media (max-width: 640px) { .monthly-page { padding: 20px 16px; } .month-bar { align-items: start; } .month-bar label { display: grid; } }
</style>
