<template>
  <div class="lesson-page">
    <header class="lesson-header">
      <button class="link" @click="$router.replace('/')">← 返回</button>
      <div class="title">
        <h1>{{ ws ? ws.class_name : '课堂签到' }}</h1>
        <small v-if="ws">{{ fmtTime(ws.starts_at) }} – {{ fmtTime(ws.ends_at) }} · {{ ws.teacher_name }}</small>
      </div>
      <div class="stats" v-if="ws">
        <span>应到 <b>{{ ws.expected }}</b></span>
        <span>实到 <b class="ok">{{ arrivedCount }}</b></span>
        <span>请假 <b class="warn">{{ leaveCount }}</b></span>
        <span>到课率 <b>{{ liveRate }}</b></span>
        <button v-if="kiosk.pendingCount" class="flush-btn" :disabled="kiosk.flushing" @click="flush">
          {{ kiosk.flushing ? '补传中…' : '补传 ' + kiosk.pendingCount + ' 条' }}
        </button>
      </div>
    </header>

    <div v-if="offlineMode" class="offline-banner">
      网络不可用，正在显示本机缓存的名单；签到会离线记录，联网后自动补传。
    </div>

    <div v-if="!kiosk.deviceReady" class="device-banner">
      <span>本机尚未配置设备令牌，无法提交签到。请管理员在服务端签发设备令牌后在此配置：</span>
      <input v-model.trim="tokenInput" placeholder="粘贴设备令牌" @keyup.enter="saveToken">
      <button class="primary" :disabled="!tokenInput || tokenBusy" @click="saveToken">{{ tokenBusy ? '校验中…' : '保存' }}</button>
      <p v-if="tokenError" class="error">{{ tokenError }}</p>
    </div>

    <div class="toolbar">
      <input v-model.trim="keyword" class="search" placeholder="搜索姓名或学号">
    </div>

    <section v-if="loading" class="hint">正在加载名单…</section>
    <section v-else-if="loadError" class="hint error">{{ loadError }}</section>

    <section v-else class="roster">
      <article
        v-for="m in filteredMembers" :key="m.id"
        class="member" :class="{ arrived: m.arrived, busy: busyId === m.id }"
        @click="onTap(m)"
      >
        <div class="avatar">{{ m.name.slice(0, 1) }}</div>
        <div class="info">
          <b>{{ m.name }}</b>
          <small>{{ m.external_ref }} · 余 {{ fmtUnits(m.remaining_units) }} 课时</small>
        </div>
        <div class="state">
          <span v-if="busyId === m.id" class="tag">提交中…</span>
          <span v-else-if="m.pending" class="tag pending">待补传</span>
          <span v-else-if="m.arrived" class="tag ok">已签到</span>
          <span v-else-if="leaveOf(m.id)" class="tag warn">已请假</span>
          <span v-else class="tag">待签到</span>
        </div>
      </article>
      <p v-if="filteredMembers.length === 0" class="hint">没有匹配的学生</p>
    </section>

    <div v-if="modal.mode" class="modal-mask" @click.self="closeModal">
      <div class="modal">
        <template v-if="modal.mode === 'checkin'">
          <h2>确认签到</h2>
          <p><b>{{ modal.member.name }}</b>（{{ modal.member.external_ref }}）</p>
          <p>扣除 {{ kiosk.defaultConsumedUnits / 100 }} 课时，余 {{ fmtUnits(modal.member.remaining_units - kiosk.defaultConsumedUnits) }} 课时</p>
          <div class="actions">
            <button class="link" @click="closeModal">取消</button>
            <button class="leave-btn" @click="modal.mode = 'leave'">办理请假</button>
            <button class="primary" @click="doCheckin">确认签到</button>
          </div>
        </template>

        <template v-else-if="modal.mode === 'leave'">
          <h2>办理请假</h2>
          <p><b>{{ modal.member.name }}</b>（{{ modal.member.external_ref }}）</p>
          <p class="note">请假计应到、不计实到、不扣课时。</p>
          <input v-model.trim="leaveReason" class="reason" placeholder="请假事由（必填）" maxlength="200">
          <div class="actions">
            <button class="link" @click="modal.mode = 'checkin'">返回</button>
            <button class="primary" :disabled="!leaveReason" @click="doLeave">确认请假</button>
          </div>
        </template>

        <template v-else-if="modal.mode === 'cancel-leave'">
          <h2>撤销请假</h2>
          <p><b>{{ modal.member.name }}</b> 本次已请假（{{ leaveOf(modal.member.id).reason }}）。</p>
          <p class="note">撤销后恢复为待签到，不会自动签到。</p>
          <input v-model.trim="leaveReason" class="reason" placeholder="撤销事由（必填）" maxlength="200">
          <div class="actions">
            <button class="link" @click="closeModal">取消</button>
            <button class="danger" :disabled="!leaveReason" @click="doCancelLeave">确认撤假</button>
          </div>
        </template>

        <template v-else-if="modal.mode === 'undo'">
          <h2>撤销签到</h2>
          <p><b>{{ modal.member.name }}</b> 本次已签到，撤销后课时会退回。</p>
          <div class="actions">
            <button class="link" @click="closeModal">取消</button>
            <button class="danger" @click="doUndo">确认撤销</button>
          </div>
        </template>

        <template v-else-if="modal.mode === 'undo-pending'">
          <h2>撤销未上传的签到</h2>
          <p><b>{{ modal.member.name }}</b> 的签到还在本机队列里等待补传，现在撤销不会产生任何服务器记录。</p>
          <div class="actions">
            <button class="link" @click="closeModal">取消</button>
            <button class="danger" @click="doCancelPending">确认撤销</button>
          </div>
        </template>
      </div>
    </div>

    <div v-if="toast" class="toast" :class="{ bad: toastBad }">{{ toast }}</div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, ApiError } from '../api/client'
import { useKioskStore } from '../stores/kiosk'
import { useAuthStore } from '../stores/auth'
import { dbPut, dbGet, dbAll } from '../offline/db'

const route = useRoute()
const router = useRouter()
const kiosk = useKioskStore()
const auth = useAuthStore()

const ws = ref(null)
const loading = ref(true)
const loadError = ref('')
const keyword = ref('')
const busyId = ref('')
const modal = ref({ mode: '', member: null })
const leaveReason = ref('')
const toast = ref('')
const toastBad = ref(false)
const tokenInput = ref('')
const tokenBusy = ref(false)
const tokenError = ref('')
const offlineMode = ref(false)
// 本机已签到记录：studentId -> attendanceId（撤销仅限本设备提交的记录）
const attendanceIds = ref({})

const lessonId = route.params.id

const arrivedCount = computed(() =>
  (ws.value ? ws.value.members.filter((m) => m.arrived).length : 0))
const leaveCount = computed(() => (ws.value ? ws.value.leaves.length : 0))

// 到课率本地实时重算：总实到 ÷ 总应到（含请假），分母为 0 时显示 —
// 口径与后端 workflows.rate 一致
const liveRate = computed(() => {
  if (!ws.value || !ws.value.expected) return '—'
  return Math.round((arrivedCount.value / ws.value.expected) * 1000) / 10 + '%'
})

const filteredMembers = computed(() => {
  if (!ws.value) return []
  const k = keyword.value.toLowerCase()
  if (!k) return ws.value.members
  return ws.value.members.filter((m) =>
    m.name.toLowerCase().includes(k) || (m.external_ref || '').toLowerCase().includes(k))
})

const leaveMap = computed(() => {
  const map = {}
  for (const l of (ws.value && ws.value.leaves) || []) map[l.student_id] = l
  return map
})
const leaveOf = (id) => leaveMap.value[id]

// 业务口径：固定中国时间（UTC+08:00），不随设备时区漂移
function fmtTime(epochSec) {
  const d = new Date(epochSec * 1000 + 8 * 3600 * 1000)
  const p = (n) => String(n).padStart(2, '0')
  return p(d.getUTCHours()) + ':' + p(d.getUTCMinutes())
}
function fmtUnits(units) {
  const v = units / 100
  return Number.isInteger(v) ? v : v.toFixed(1)
}

async function load() {
  loading.value = true
  loadError.value = ''
  try {
    ws.value = await api.lessonWorkspace(lessonId)
    offlineMode.value = false
    // 名单写本机缓存，断网时兜底
    dbPut('cache', { key: 'lesson:' + lessonId, value: JSON.stringify(ws.value), savedAt: Date.now() })
      .catch(() => {})
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) { router.replace('/login'); return }
    if (e instanceof ApiError && e.status === 0) {
      const cached = await dbGet('cache', 'lesson:' + lessonId).catch(() => null)
      if (cached && cached.value) {
        ws.value = JSON.parse(cached.value)
        offlineMode.value = true
      } else {
        loadError.value = '网络不可用，且本机没有缓存名单'
      }
    } else {
      loadError.value = (e && e.message) || '加载失败'
    }
  } finally {
    loading.value = false
  }
  // 用队列里未上传的签到标记“待补传”
  try {
    const items = await dbAll('outbox')
    const pendingIds = new Set(items.filter((i) => i.kind === 'attendance' && i.event.lesson_id === lessonId).map((i) => i.event.student_id))
    if (ws.value) {
      for (const m of ws.value.members) m.pending = pendingIds.has(m.id) ? 1 : 0
    }
  } catch (e) { /* IndexedDB 不可用时跳过 */ }
}

async function flush() {
  const done = await kiosk.flushOutbox()
  if (done > 0) {
    showToast('已补传 ' + done + ' 条离线记录')
    await load()
  } else if (kiosk.pendingCount > 0) {
    showToast('网络仍不可用，稍后会自动重试', true)
  }
}

function showToast(msg, bad) {
  toast.value = msg
  toastBad.value = !!bad
  setTimeout(() => { toast.value = '' }, 2600)
}

function onTap(m) {
  if (busyId.value) return
  leaveReason.value = ''
  if (m.pending) {
    modal.value = { mode: 'undo-pending', member: m }
  } else if (m.arrived) {
    modal.value = { mode: 'undo', member: m }
  } else if (leaveOf(m.id)) {
    modal.value = { mode: 'cancel-leave', member: m }
  } else {
    modal.value = { mode: 'checkin', member: m }
  }
}
function closeModal() {
  modal.value = { mode: '', member: null }
  leaveReason.value = ''
}

async function doCheckin() {
  const m = modal.value.member
  closeModal()
  busyId.value = m.id
  try {
    const { receipt, event, offline } = await kiosk.checkin(lessonId, m)
    m.remaining_units = event.remaining_units
    if (offline) {
      m.pending = 1
      showToast(m.name + ' 已离线记录，联网后自动补传')
    } else {
      attendanceIds.value[m.id] = receipt.id
      m.arrived = 1
      showToast(receipt.duplicate ? m.name + ' 已提交过（幂等去重）' : m.name + ' 签到成功')
    }
  } catch (e) {
    handleEventError(e, m)
  } finally {
    busyId.value = ''
  }
}

async function doLeave() {
  const m = modal.value.member
  const reason = leaveReason.value
  closeModal()
  busyId.value = m.id
  try {
    const res = await api.addLeave(lessonId, m.id, reason)
    ws.value.leaves.push({ id: res.id, student_id: m.id, reason, created_at: 0, recorded_by: '' })
    showToast('已为 ' + m.name + ' 办理请假（计应到，不扣课时）')
  } catch (e) {
    if (e instanceof ApiError && e.status === 409) { showToast(e.message || '状态冲突', true); load() }
    else showToast((e && e.message) || '请假失败', true)
  } finally {
    busyId.value = ''
  }
}

async function doCancelLeave() {
  const m = modal.value.member
  const leave = leaveOf(m.id)
  const reason = leaveReason.value
  closeModal()
  busyId.value = m.id
  try {
    await api.cancelLeave(leave.id, reason)
    ws.value.leaves = ws.value.leaves.filter((l) => l.id !== leave.id)
    showToast('已撤销 ' + m.name + ' 的请假，恢复为待签到')
  } catch (e) {
    if (e instanceof ApiError && e.status === 409) { showToast(e.message || '状态冲突', true); load() }
    else showToast((e && e.message) || '撤假失败', true)
  } finally {
    busyId.value = ''
  }
}

// 撤销一条还在队列里、尚未上传的签到
async function doCancelPending() {
  const m = modal.value.member
  closeModal()
  busyId.value = m.id
  try {
    const removed = await kiosk.cancelQueued(m.id, lessonId)
    if (removed) {
      m.pending = 0
      m.remaining_units += kiosk.defaultConsumedUnits
      showToast('已撤销 ' + m.name + ' 未上传的签到')
    } else {
      showToast('队列里找不到该记录，请刷新核对', true)
    }
  } finally {
    busyId.value = ''
  }
}

async function doUndo() {
  const m = modal.value.member
  closeModal()
  const attendanceId = attendanceIds.value[m.id]
  if (!attendanceId) { showToast('该签到不是本机提交，不能在本机撤销', true); return }
  busyId.value = m.id
  try {
    // 退回 = 当前余额 + 签到时扣除的课时
    const refund = m.remaining_units + kiosk.defaultConsumedUnits
    await kiosk.undoCheckin(attendanceId, m, refund, '误操作撤销')
    m.arrived = 0
    m.remaining_units = refund
    delete attendanceIds.value[m.id]
    showToast('已撤销 ' + m.name + ' 的签到，课时已退回')
  } catch (e) {
    handleEventError(e, m)
  } finally {
    busyId.value = ''
  }
}

function handleEventError(e, m) {
  if (e instanceof ApiError) {
    if (e.status === 401) { showToast('设备令牌已失效，请重新配置', true); kiosk.clearDeviceToken(); return }
    if (e.status === 403) { showToast('设备未获授权或无权操作该班级', true); return }
    if (e.status === 409) { showToast(e.message || '状态冲突，请刷新名单', true); load(); return }
    if (e.status === 0) { showToast('网络不可用，离线补传将在阶段3接入', true); return }
  }
  showToast((e && e.message) || '操作失败', true)
}

async function saveToken() {
  tokenBusy.value = true
  tokenError.value = ''
  try {
    await kiosk.setDeviceToken(tokenInput.value)
    tokenInput.value = ''
  } catch (e) {
    tokenError.value = (e && e.message) || '令牌校验失败'
  } finally {
    tokenBusy.value = false
  }
}

let flushTimer = null
async function autoFlush() {
  if (kiosk.pendingCount > 0 && !kiosk.flushing) {
    const done = await kiosk.flushOutbox()
    if (done > 0) { showToast('已自动补传 ' + done + ' 条离线记录'); await load() }
  }
}

onMounted(async () => {
  if (!auth.ready) await auth.fetchMe()
  if (!auth.isLoggedIn) { router.replace('/login'); return }
  await kiosk.refreshPending()
  load()
  window.addEventListener('online', autoFlush)
  flushTimer = setInterval(autoFlush, 30000)
})

onUnmounted(() => {
  window.removeEventListener('online', autoFlush)
  if (flushTimer) clearInterval(flushTimer)
})
</script>

<style scoped>
.lesson-page { min-height: 100%; display: flex; flex-direction: column; }
.lesson-header {
  display: flex; align-items: center; gap: 18px;
  padding: 14px 24px; background: #0165b3; color: #fff; flex-wrap: wrap;
}
.lesson-header h1 { font-size: 19px; }
.lesson-header small { opacity: .85; }
.title { display: grid; gap: 2px; }
.stats { margin-left: auto; display: flex; gap: 16px; font-size: 14px; }
.stats b { font-size: 17px; }
.stats .ok { color: #b6f5c9; }
.stats .warn { color: #ffe3a3; }
.link {
  border: 1px solid #ffffff66; background: transparent; color: #fff;
  border-radius: 10px; padding: 8px 14px; font: inherit; cursor: pointer;
}
.device-banner {
  display: flex; align-items: center; gap: 10px; flex-wrap: wrap;
  background: #fff7e5; color: #8a6b2f; padding: 12px 24px; font-size: 14px;
}
.device-banner input {
  flex: 1; min-width: 220px; min-height: 40px; border: 1px solid #ecdcb3;
  border-radius: 10px; padding: 0 12px; font: inherit;
}
.toolbar { padding: 14px 24px 0; }
.search {
  width: 100%; max-width: 420px; min-height: 46px;
  border: 1px solid #d4e6f3; border-radius: 12px; padding: 0 14px; font: inherit;
}
.roster {
  display: grid; gap: 12px; padding: 16px 24px 28px;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  align-content: start;
}
.member {
  display: flex; align-items: center; gap: 14px;
  background: #fff; border: 1px solid #d8e7f2; border-radius: 14px;
  padding: 14px 16px; cursor: pointer; user-select: none;
}
.member.arrived { background: #f2fbf5; border-color: #bfe8cd; }
.member.busy { opacity: .6; }
.avatar {
  width: 44px; height: 44px; border-radius: 50%;
  display: grid; place-items: center;
  background: #e4f3ff; color: #0572b7; font-weight: 700; font-size: 17px;
}
.member.arrived .avatar { background: #d6f3e0; color: #178a4c; }
.info { display: grid; gap: 3px; min-width: 0; }
.info b { font-size: 16px; }
.info small { color: #7a93a5; }
.state { margin-left: auto; }
.tag { font-size: 13px; color: #66869d; }
.tag.ok { color: #178a4c; font-weight: 700; }
.tag.warn { color: #b6810d; font-weight: 700; }
.hint { padding: 36px; text-align: center; color: #66869d; }
.error, .hint.error { color: #c0392b; }
.modal-mask {
  position: fixed; inset: 0; background: #103e6780;
  display: grid; place-items: center; z-index: 10;
}
.modal {
  width: min(400px, 92vw); background: #fff; border-radius: 16px;
  padding: 26px; display: grid; gap: 14px;
}
.modal h2 { font-size: 18px; }
.modal .note { font-size: 13px; color: #8a6b2f; }
.reason {
  min-height: 44px; border: 1px solid #d4e6f3; border-radius: 10px;
  padding: 0 12px; font: inherit;
}
.actions { display: flex; justify-content: flex-end; gap: 12px; margin-top: 6px; }
.actions .link { color: #396684; border-color: #d4e6f3; background: #f3f8ff; }
.primary {
  border: 0; background: #0165b3; color: #fff; border-radius: 10px;
  padding: 10px 22px; font: inherit; font-weight: 700; cursor: pointer;
}
.primary:disabled { opacity: .5; cursor: default; }
.leave-btn {
  border: 1px solid #ecdcb3; background: #fff7e5; color: #8a6b2f;
  border-radius: 10px; padding: 10px 18px; font: inherit; font-weight: 700; cursor: pointer;
}
.danger {
  border: 0; background: #c0392b; color: #fff; border-radius: 10px;
  padding: 10px 22px; font: inherit; font-weight: 700; cursor: pointer;
}
.danger:disabled { opacity: .5; cursor: default; }
.toast {
  position: fixed; left: 50%; bottom: 36px; transform: translateX(-50%);
  background: #103e67; color: #fff; border-radius: 12px;
  padding: 12px 22px; font-size: 15px; z-index: 20;
}
.toast.bad { background: #c0392b; }
.offline-banner {
  background: #103e67; color: #fff; text-align: center;
  padding: 10px 24px; font-size: 14px;
}
.flush-btn {
  border: 1px solid #f4ab04; background: #f4ab04; color: #103e67;
  border-radius: 10px; padding: 8px 14px; font: inherit; font-weight: 700; cursor: pointer;
}
.flush-btn:disabled { opacity: .6; }
.tag.pending { color: #0569a9; font-weight: 700; }
</style>
