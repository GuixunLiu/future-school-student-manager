<template>
  <main class="approvals-page">
    <div class="heading">
      <div>
        <h2>请假与补签审批</h2>
        <p>申请经审批后生效；平板是否保存以接收状态为准。</p>
      </div>
      <div class="heading-actions">
        <select v-if="availableRoles.length > 1" v-model="role" aria-label="工作身份" @change="reload">
          <option v-for="r in availableRoles" :key="r" :value="r">{{ roleName(r) }}</option>
        </select>
        <button class="secondary" :disabled="loading" @click="reload">刷新</button>
      </div>
    </div>

    <div class="policy-note">
      批准补签会按原课堂记录扣减一次课时；批准请假不扣课时。驳回只记录审批意见，不改变签到与课时。
    </div>
    <p v-if="message" class="success">{{ message }}</p>
    <p v-if="loading && !items.length" class="hint">正在加载审批申请…</p>
    <p v-else-if="error && !items.length" class="hint error">{{ error }}</p>

    <section v-else class="request-list">
      <article v-for="row in items" :key="row.id" class="request-card">
        <div class="request-head">
          <h3>{{ row.student_name }} · {{ row.kind === 'leave' ? '请假' : '补签' }}</h3>
          <span class="status" :class="row.status">{{ statusName(row.status) }}</span>
        </div>
        <div class="request-meta">
          <span>{{ row.class_name }}</span>
          <span>上课：{{ fmtTime(row.starts_at) }}</span>
          <span>申请：{{ fmtTime(row.created_at) }}</span>
          <span v-if="row.historical" class="history">历史课程</span>
        </div>
        <p class="reason">{{ row.reason }}</p>
        <p v-if="row.decision_reason" class="decision-reason">审批意见：{{ row.decision_reason }}</p>
        <p v-if="row.status === 'approved'" class="delivery" :class="row.tablet_delivery">
          {{ deliveryName(row.tablet_delivery) }}
        </p>
        <div v-if="row.status === 'pending' && canDecide" class="decision-actions">
          <button class="reject-btn" @click="openDecision(row, false)">驳回</button>
          <button class="approve-btn" @click="openDecision(row, true)">批准</button>
        </div>
      </article>
      <p v-if="!items.length" class="empty">暂无当前身份可查看的申请</p>
    </section>

    <p v-if="error && items.length" class="error pager-error">{{ error }}</p>
    <button v-if="nextBefore" class="load-more" :disabled="loading" @click="load(nextBefore)">
      {{ loading ? '加载中…' : '加载更多' }}
    </button>

    <div v-if="decision.row" class="modal-mask" @click.self="closeDecision">
      <form class="decision-modal" @submit.prevent="submitDecision">
        <h2>{{ decision.approve ? '批准申请' : '驳回申请' }}</h2>
        <p>
          {{ decision.row.student_name }} · {{ decision.row.kind === 'leave' ? '请假' : '补签' }} · {{ decision.row.class_name }}
        </p>
        <p class="modal-warning">
          {{ decision.approve
            ? (decision.row.kind === 'leave' ? '批准后记为请假，不扣课时。' : '批准后记为已签到，并按原课堂扣减一次课时。')
            : '驳回后不改变签到和课时，原申请及审批意见会保留。' }}
        </p>
        <label>
          审批意见（必填）
          <textarea v-model.trim="decision.reason" required maxlength="200" rows="4" placeholder="填写核对结果"></textarea>
        </label>
        <p v-if="decision.error" class="error">{{ decision.error }}</p>
        <div class="modal-actions">
          <button type="button" class="secondary" :disabled="decision.busy" @click="closeDecision">取消</button>
          <button type="submit" class="decision-submit" :class="{ reject: !decision.approve }" :disabled="decision.busy || !decision.reason">
            {{ decision.busy ? '提交中…' : (decision.approve ? '确认批准' : '确认驳回') }}
          </button>
        </div>
      </form>
    </div>
  </main>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { api, ApiError } from '../../api/client'
import { useAuthStore } from '../../stores/auth'

const auth = useAuthStore()
const router = useRouter()
const items = ref([])
const loading = ref(true)
const error = ref('')
const message = ref('')
const nextBefore = ref('')
const role = ref('')
const decision = ref({ row: null, approve: true, reason: '', ref: '', busy: false, error: '' })

const availableRoles = computed(() => ['admin', 'assessor', 'teacher'].filter((r) => auth.roles.includes(r)))
const canDecide = computed(() => role.value === 'admin' || role.value === 'assessor')

function roleName(r) { return { admin: '管理员', assessor: '审批专员', teacher: '教师' }[r] || r }
function statusName(s) { return { pending: '待审批', approved: '已批准', rejected: '已驳回' }[s] || s }
function deliveryName(s) {
  return {
    unassigned: '已生效 · 尚无可接收的平板', pending: '已生效 · 等待平板接收',
    conflict: '已生效 · 平板记录有差异，待核对', applied: '已生效 · 平板已保存',
    not_created: '尚未生成平板接收任务'
  }[s] || '已生效 · 接收状态待核对'
}
function fmtTime(epochSec) {
  if (epochSec === null || epochSec === undefined) return '原时间未记录'
  const d = new Date(epochSec * 1000 + 8 * 3600 * 1000)
  const p = (n) => String(n).padStart(2, '0')
  return d.getUTCFullYear() + '-' + p(d.getUTCMonth() + 1) + '-' + p(d.getUTCDate()) + ' ' + p(d.getUTCHours()) + ':' + p(d.getUTCMinutes())
}
function requestRef() {
  const bytes = new Uint8Array(16)
  crypto.getRandomValues(bytes)
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
}

async function load(before) {
  if (!role.value) return
  loading.value = true
  error.value = ''
  try {
    const result = await api.managementRequests(role.value, before, 20)
    items.value = before ? items.value.concat(result.items) : result.items
    nextBefore.value = result.next_before || ''
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) { router.replace('/login'); return }
    error.value = (e && e.message) || '审批申请加载失败'
  } finally {
    loading.value = false
  }
}
function reload() { return load('') }
function openDecision(row, approve) {
  decision.value = { row, approve, reason: '', ref: requestRef(), busy: false, error: '' }
}
function closeDecision() {
  if (!decision.value.busy) decision.value.row = null
}
async function submitDecision() {
  const current = decision.value
  if (!current.row || current.busy || !current.reason) return
  current.busy = true
  current.error = ''
  try {
    const result = await api.decideManagementRequest(current.row.id, role.value, current.approve, current.reason, current.ref)
    const action = current.approve ? '批准' : '驳回'
    decision.value.row = null
    message.value = action + '已保存' + (current.approve ? '，等待平板接收；不代表平板已保存' : '，签到和课时未改变')
    if (result.duplicate) message.value = action + '结果已存在，本次为安全重试'
    await load('')
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) { router.replace('/login'); return }
    current.error = (e && e.message) || '审批未保存，请核对后重试'
    current.busy = false
  }
}

onMounted(async () => {
  if (!auth.ready) await auth.fetchMe()
  if (!auth.isLoggedIn) return
  role.value = availableRoles.value[0] || ''
  if (!role.value) {
    loading.value = false
    error.value = '当前账号没有审批工作台权限'
    return
  }
  load('')
})
</script>

<style scoped>
.approvals-page { padding: 26px 32px; max-width: 1080px; width: 100%; }
.heading { display: flex; align-items: center; justify-content: space-between; gap: 20px; margin-bottom: 18px; }
.heading h2 { font-size: 22px; }
.heading p { margin-top: 6px; color: #718da1; font-size: 13px; }
.heading-actions { display: flex; gap: 10px; }
.secondary, .load-more { border: 1px solid #cbdfea; background: #fff; color: #396684; border-radius: 10px; padding: 9px 18px; font: inherit; cursor: pointer; }
.secondary:disabled, .load-more:disabled { opacity: .5; }
select { border: 1px solid #cbdfea; background: #fff; color: #396684; border-radius: 10px; padding: 8px 12px; font: inherit; }
.policy-note { background: #edf6fc; border: 1px solid #d5e8f4; color: #456d87; border-radius: 12px; padding: 12px 16px; font-size: 13px; margin-bottom: 18px; line-height: 1.6; }
.request-list { display: grid; gap: 12px; }
.request-card { background: #fff; border: 1px solid #dbe8f2; border-radius: 14px; padding: 18px 20px; }
.request-head { display: flex; align-items: center; justify-content: space-between; gap: 14px; }
.request-head h3 { font-size: 17px; }
.status { border-radius: 20px; padding: 5px 11px; font-size: 12px; white-space: nowrap; }
.status.pending { color: #8a641c; background: #fff3dd; }
.status.approved { color: #23794a; background: #e5f6ed; }
.status.rejected { color: #875c59; background: #f7eceb; }
.request-meta { display: flex; flex-wrap: wrap; gap: 8px 18px; color: #7890a2; font-size: 12px; margin-top: 10px; }
.history { color: #8a641c; }
.reason { color: #315b78; line-height: 1.7; margin-top: 14px; white-space: pre-wrap; }
.decision-reason { color: #657f92; font-size: 13px; margin-top: 10px; }
.delivery { margin-top: 12px; font-size: 13px; color: #8a641c; }
.delivery.applied { color: #23794a; }
.delivery.conflict { color: #b44338; }
.decision-actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 16px; }
.approve-btn, .reject-btn, .decision-submit { border: 0; border-radius: 9px; padding: 9px 22px; color: #fff; background: #0165b3; font: inherit; font-weight: 600; cursor: pointer; }
.reject-btn { background: #fff; color: #a04840; border: 1px solid #e5c8c4; }
.empty, .hint { padding: 56px 20px; text-align: center; color: #7890a2; background: #fff; border-radius: 14px; }
.error { color: #bd3c32; font-size: 13px; }
.success { color: #23794a; font-size: 13px; margin-bottom: 14px; }
.load-more { display: block; margin: 18px auto 0; }
.pager-error { text-align: center; margin-top: 14px; }
.modal-mask { position: fixed; inset: 0; background: #0a263c66; display: grid; place-items: center; padding: 20px; z-index: 20; }
.decision-modal { width: min(480px, 100%); background: #fff; border-radius: 16px; padding: 24px; display: grid; gap: 16px; box-shadow: 0 24px 60px #0a263c33; }
.decision-modal h2 { font-size: 20px; }
.decision-modal > p { color: #56748a; font-size: 14px; line-height: 1.6; }
.modal-warning { background: #fff6e7; border-radius: 9px; padding: 10px 12px; color: #856526 !important; }
.decision-modal label { display: grid; gap: 8px; color: #426682; font-size: 13px; font-weight: 600; }
.decision-modal textarea { resize: vertical; border: 1px solid #cbdfea; border-radius: 10px; padding: 12px; font: inherit; color: #103e67; }
.modal-actions { display: flex; justify-content: flex-end; gap: 10px; }
.decision-submit.reject { background: #a04840; }
.decision-submit:disabled { opacity: .5; }
@media (max-width: 640px) { .approvals-page { padding: 20px 16px; } .heading { align-items: start; } }
</style>
