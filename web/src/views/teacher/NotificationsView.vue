<template>
  <main class="notifications-page">
    <div class="heading">
      <div>
        <h2>通知记录</h2>
        <p>仅显示当前账号有权限查看的记录。</p>
      </div>
      <button class="refresh" :disabled="loading" @click="reload">刷新</button>
    </div>

    <div class="simulation-banner">
      测试环境：以下均为模拟通知记录，未真实发送微信，也不代表对方已经收到。
    </div>

    <p v-if="loading && !items.length" class="hint">正在加载通知记录…</p>
    <p v-else-if="error && !items.length" class="hint error">{{ error }}</p>
    <section v-else class="notice-list">
      <article v-for="notice in items" :key="notice.id" class="notice-card">
        <div class="notice-top">
          <span class="type">{{ typeName(notice.notice_type) }}</span>
          <span class="status" :class="notice.status">{{ statusName(notice.status) }}</span>
          <time>{{ fmtDateTime(notice.created_at) }}</time>
        </div>
        <h3>{{ title(notice) }}</h3>
        <p class="summary">{{ summary(notice) }}</p>
        <div class="meta">
          <span>接收人：{{ notice.recipient_name }}</span>
          <span v-if="notice.simulated_at">模拟时间：{{ fmtDateTime(notice.simulated_at) }}</span>
          <span v-if="notice.cancel_reason">取消原因：{{ cancelReason(notice.cancel_reason) }}</span>
        </div>
      </article>
      <p v-if="!items.length" class="empty">暂无通知记录</p>
    </section>

    <p v-if="error && items.length" class="error load-more-error">{{ error }}</p>
    <button v-if="nextCursor" class="load-more" :disabled="loading" @click="loadMore">
      {{ loading ? '加载中…' : '加载更多' }}
    </button>
  </main>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { api, ApiError } from '../../api/client'
import { useAuthStore } from '../../stores/auth'

const auth = useAuthStore()
const router = useRouter()
const items = ref([])
const loading = ref(true)
const error = ref('')
const nextCursor = ref('')

const typeNames = {
  attendance: '签到通知', low_hours_parent: '课时不足', lesson_content: '课堂内容',
  absence_parent: '未到提醒', absence_teacher: '课堂未到汇总', low_hours_teacher: '课时不足',
  weekly_teacher: '教师周报', weekly_admin: '全校周报', monthly_admin: '运营月报'
}
const statusNames = {
  pending_simulation: '待模拟', simulated: '已模拟', cancelled: '已取消', expired: '已过期'
}

function typeName(type) { return typeNames[type] || type }
function statusName(status) { return statusNames[status] || status }
function cancelReason(reason) {
  return reason === 'authorization_or_consent_changed' ? '授权或接收意向已变化' : reason
}
function dataOf(notice) { return (notice.payload && notice.payload.data) || {} }
function title(notice) {
  const d = dataOf(notice)
  if (d.student_name) return d.student_name + ' · ' + typeName(notice.notice_type)
  if (notice.notice_type === 'absence_teacher') return '课堂有 ' + (d.missing_count || 0) + ' 人未到'
  if (notice.notice_type === 'weekly_teacher' || notice.notice_type === 'weekly_admin') return (d.week_start || '') + ' 起的一周'
  if (notice.notice_type === 'monthly_admin') return d.month || '月度运营报告'
  return typeName(notice.notice_type)
}
function summary(notice) {
  const d = dataOf(notice)
  if (notice.notice_type === 'lesson_content') return d.body || '课堂内容已生成'
  if (notice.notice_type === 'attendance') return '已记录本次到课；剩余课时 ' + remaining(d.remaining_units)
  if (notice.notice_type === 'absence_parent') return '学生本次未签到且未请假，请核实到课情况'
  if (notice.notice_type === 'absence_teacher') {
    const names = (d.missing_students || []).map((s) => s.name).join('、')
    return names ? '未到学生：' + names : '请查看课堂名单确认未到情况'
  }
  if (notice.notice_type === 'weekly_teacher' || notice.notice_type === 'weekly_admin') {
    return '应到 ' + (d.expected || 0) + ' 人次，实到 ' + (d.arrived || 0) + ' 人次，请假 ' + (d.leave_count || 0) + ' 人次'
  }
  if (notice.notice_type === 'low_hours_parent' || notice.notice_type === 'low_hours_teacher') {
    return '剩余课时 ' + remaining(d.remaining_units) + '，请及时关注续费安排'
  }
  return '模拟通知已记录，可查看对应业务页面了解详情'
}
function remaining(units) {
  if (units === null || units === undefined) return '—'
  const value = units / 100
  return (Number.isInteger(value) ? value : value.toFixed(1)) + ' 课时'
}
function fmtDateTime(epochSec) {
  if (!epochSec) return '—'
  const d = new Date(epochSec * 1000 + 8 * 3600 * 1000)
  const p = (n) => String(n).padStart(2, '0')
  return d.getUTCFullYear() + '-' + p(d.getUTCMonth() + 1) + '-' + p(d.getUTCDate()) + ' ' + p(d.getUTCHours()) + ':' + p(d.getUTCMinutes())
}

async function load(after) {
  loading.value = true
  error.value = ''
  try {
    const result = await api.myNotifications(after, 20)
    items.value = after ? items.value.concat(result.items) : result.items
    nextCursor.value = result.next_cursor || ''
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) { router.replace('/login'); return }
    error.value = (e && e.message) || '通知记录加载失败'
  } finally {
    loading.value = false
  }
}
function reload() { return load('') }
function loadMore() { return load(nextCursor.value) }

onMounted(async () => {
  if (!auth.ready) await auth.fetchMe()
  if (auth.isLoggedIn) load('')
})
</script>

<style scoped>
.notifications-page { padding: 26px 32px; max-width: 1000px; width: 100%; }
.heading { display: flex; align-items: center; justify-content: space-between; margin-bottom: 18px; }
.heading h2 { font-size: 22px; }
.heading p { margin-top: 6px; color: #718da1; font-size: 13px; }
.refresh, .load-more { border: 1px solid #cbdfea; background: #fff; color: #396684; border-radius: 10px; padding: 9px 18px; font: inherit; cursor: pointer; }
.refresh:disabled, .load-more:disabled { opacity: .5; }
.simulation-banner { background: #fff6e7; border: 1px solid #f0dfba; color: #856526; border-radius: 12px; padding: 12px 16px; font-size: 13px; margin-bottom: 18px; }
.notice-list { display: grid; gap: 12px; }
.notice-card { background: #fff; border: 1px solid #dbe8f2; border-radius: 14px; padding: 18px 20px; }
.notice-top { display: flex; align-items: center; gap: 10px; }
.type { color: #24678e; background: #eaf4fc; border-radius: 6px; padding: 4px 9px; font-size: 12px; font-weight: 600; }
.status { border-radius: 6px; padding: 4px 9px; font-size: 12px; color: #5e788a; background: #edf1f4; }
.status.simulated { color: #23794a; background: #e5f6ed; }
.status.pending_simulation { color: #8a641c; background: #fff3dd; }
.status.cancelled, .status.expired { color: #875c59; background: #f7eceb; }
.notice-top time { margin-left: auto; color: #8b9eac; font-size: 12px; }
.notice-card h3 { font-size: 16px; margin-top: 13px; }
.summary { color: #4d6d83; font-size: 14px; line-height: 1.7; margin-top: 7px; white-space: pre-wrap; }
.meta { display: flex; flex-wrap: wrap; gap: 8px 20px; margin-top: 13px; color: #8a9ba8; font-size: 12px; }
.empty, .hint { padding: 56px 20px; text-align: center; color: #7890a2; background: #fff; border-radius: 14px; }
.error { color: #bd3c32; }
.load-more { display: block; margin: 18px auto 0; }
.load-more-error { text-align: center; margin-top: 14px; font-size: 13px; }
@media (max-width: 640px) { .notifications-page { padding: 20px 16px; } }
</style>
