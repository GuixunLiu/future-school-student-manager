<template>
  <main class="content-studio">
    <section class="lesson-column">
      <div class="date-bar">
        <label for="lesson-day">上课日期</label>
        <input id="lesson-day" v-model="day" type="date" @change="loadLessons">
      </div>

      <p v-if="lessonsLoading" class="hint">正在加载课次…</p>
      <p v-else-if="lessonsError" class="hint error">{{ lessonsError }}</p>
      <div v-else class="lesson-list">
        <button
          v-for="lesson in lessons" :key="lesson.id"
          class="lesson-item" :class="{ selected: lesson.id === selectedId }"
          @click="selectLesson(lesson.id)"
        >
          <b>{{ timeRange(lesson) }}</b>
          <span>{{ lesson.class_name }}</span>
          <small>{{ lesson.course_name }} · {{ lesson.teacher_name }}</small>
          <em>应到 {{ lesson.expected }} · 实到 {{ lesson.arrived }} · 请假 {{ lesson.leave_count }}</em>
        </button>
        <p v-if="!lessons.length" class="hint">这一天没有课次</p>
      </div>
    </section>

    <section class="editor-column">
      <template v-if="selectedId">
        <p v-if="detailLoading" class="hint">正在加载课堂内容…</p>
        <p v-else-if="detailError" class="hint error">{{ detailError }}</p>
        <template v-else>
          <div class="editor-heading">
            <div>
              <h2>课堂内容</h2>
              <p>保存为草稿；勾选后将在课下生成模拟通知。</p>
            </div>
            <span v-if="content && content.published_in_test" class="locked">已生成，不可覆盖</span>
            <span v-else-if="content" class="draft">草稿已保存</span>
          </div>

          <div v-if="content && content.published_in_test" class="notice warning">
            这份内容已生成通知。为避免无留痕覆盖，页面已锁定；如需更正，请通过后续更正流程处理。
          </div>

          <label class="field">
            <span>内容正文</span>
            <textarea
              v-model="form.body" rows="12" maxlength="4000"
              :disabled="isLocked" placeholder="记录本节课重点、作业和课后建议…"
            ></textarea>
            <small>{{ form.body.length }} / 4000</small>
          </label>

          <fieldset class="audience" :disabled="isLocked">
            <legend>可见范围</legend>
            <label><input v-model="form.audience" type="radio" value="arrived"> 实到学生</label>
            <label><input v-model="form.audience" type="radio" value="roster"> 全班学生</label>
          </fieldset>

          <label class="publish-option">
            <input v-model="form.publishAfterClass" type="checkbox" :disabled="isLocked">
            课后生成课堂内容模拟通知
          </label>

          <div class="save-row">
            <span v-if="saveMessage" :class="saveError ? 'error' : 'success'">{{ saveMessage }}</span>
            <button class="primary" :disabled="isLocked || saving || !form.body.trim()" @click="save">
              {{ saving ? '保存中…' : '保存内容' }}
            </button>
          </div>

          <section class="absence-panel">
            <div>
              <h3>未到提醒</h3>
              <p v-if="workspace && workspace.unexcused.length">
                当前有 {{ workspace.unexcused.length }} 人未签到且未请假：{{ absentNames }}
              </p>
              <p v-else>当前没有未签到且未请假的学生。</p>
            </div>
            <button
              v-if="canRemind" class="remind" :disabled="reminding" @click="sendAbsenceNotices"
            >{{ reminding ? '提交中…' : '发送未到提醒' }}</button>
          </section>
          <p class="note">提醒仅在开课 5 分钟后至课后 24 小时内可提交；仅生成模拟记录，不会真实发送微信。</p>
          <p v-if="remindMessage" :class="remindError ? 'error' : 'success'">{{ remindMessage }}</p>
        </template>
      </template>
      <div v-else class="empty-editor">请选择左侧课次，再编辑课堂内容。</div>
    </section>
  </main>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { api, ApiError } from '../../api/client'
import { useAuthStore } from '../../stores/auth'

const auth = useAuthStore()
const router = useRouter()
const day = ref(chinaToday())
const lessons = ref([])
const lessonsLoading = ref(true)
const lessonsError = ref('')
const selectedId = ref('')
const workspace = ref(null)
const content = ref(null)
const detailLoading = ref(false)
const detailError = ref('')
const form = ref({ body: '', audience: 'arrived', publishAfterClass: false })
const saving = ref(false)
const saveMessage = ref('')
const saveError = ref(false)
const reminding = ref(false)
const remindMessage = ref('')
const remindError = ref(false)

const isLocked = computed(() => !!(content.value && content.value.published_in_test))
const canRemind = computed(() => !!(
  workspace.value && workspace.value.absence_window_open &&
  workspace.value.unexcused.length && !(remindMessage.value && !remindError.value)
))
const absentNames = computed(() => workspace.value
  ? workspace.value.unexcused.map((s) => s.name).join('、') : '')

function chinaToday() {
  const d = new Date(Date.now() + 8 * 3600 * 1000)
  const p = (n) => String(n).padStart(2, '0')
  return d.getUTCFullYear() + '-' + p(d.getUTCMonth() + 1) + '-' + p(d.getUTCDate())
}
function fmtTime(epochSec) {
  const d = new Date(epochSec * 1000 + 8 * 3600 * 1000)
  const p = (n) => String(n).padStart(2, '0')
  return p(d.getUTCHours()) + ':' + p(d.getUTCMinutes())
}
function timeRange(lesson) { return fmtTime(lesson.starts_at) + '–' + fmtTime(lesson.ends_at) }
function handleError(e, fallback) {
  if (e instanceof ApiError && e.status === 401) { router.replace('/login'); return '登录已失效' }
  return (e && e.message) || fallback
}

async function loadLessons() {
  lessonsLoading.value = true
  lessonsError.value = ''
  selectedId.value = ''
  workspace.value = null
  content.value = null
  try {
    const result = await api.lessonsByDay(day.value)
    lessons.value = result.items.slice().sort((a, b) => a.starts_at - b.starts_at || (a.id < b.id ? -1 : 1))
    if (lessons.value.length) await selectLesson(lessons.value[0].id)
  } catch (e) {
    lessonsError.value = handleError(e, '课次加载失败')
  } finally {
    lessonsLoading.value = false
  }
}

async function selectLesson(id) {
  selectedId.value = id
  detailLoading.value = true
  detailError.value = ''
  saveMessage.value = ''
  remindMessage.value = ''
  workspace.value = null
  content.value = null
  form.value = { body: '', audience: 'arrived', publishAfterClass: false }
  try {
    workspace.value = await api.lessonWorkspace(id)
    try {
      content.value = await api.getContent(id)
      form.value = {
        body: content.value.body,
        audience: content.value.audience,
        publishAfterClass: content.value.publish_after_class
      }
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 404)) throw e
    }
  } catch (e) {
    detailError.value = handleError(e, '课堂内容加载失败')
  } finally {
    detailLoading.value = false
  }
}

async function save() {
  saving.value = true
  saveMessage.value = ''
  saveError.value = false
  try {
    await api.putContent(selectedId.value, form.value.body.trim(), form.value.audience, form.value.publishAfterClass)
    content.value = await api.getContent(selectedId.value)
    saveMessage.value = form.value.publishAfterClass ? '已保存，课后将生成模拟通知' : '草稿保存成功'
  } catch (e) {
    saveError.value = true
    saveMessage.value = handleError(e, '保存失败')
  } finally {
    saving.value = false
  }
}

async function sendAbsenceNotices() {
  if (!workspace.value || !canRemind.value) return
  reminding.value = true
  remindMessage.value = ''
  remindError.value = false
  try {
    const ids = workspace.value.unexcused.map((s) => s.id)
    const result = await api.absenceNotices(selectedId.value, ids)
    remindMessage.value = '已确认未到名单，生成 ' + result.simulation_notices_queued + ' 条模拟通知；未真实发送微信'
  } catch (e) {
    remindError.value = true
    remindMessage.value = handleError(e, '未到提醒提交失败')
  } finally {
    reminding.value = false
  }
}

onMounted(async () => {
  if (!auth.ready) await auth.fetchMe()
  if (auth.isLoggedIn) loadLessons()
})
</script>

<style scoped>
.content-studio { display: grid; grid-template-columns: 320px minmax(0, 1fr); min-height: 100%; }
.lesson-column { background: #fff; border-right: 1px solid #dbe8f2; padding: 20px 16px; }
.date-bar { display: grid; gap: 8px; margin-bottom: 16px; }
.date-bar label, .field > span { font-size: 13px; font-weight: 600; color: #426682; }
.date-bar input { border: 1px solid #cbdfea; border-radius: 9px; padding: 9px 10px; color: #103e67; font: inherit; }
.lesson-list { display: grid; gap: 10px; }
.lesson-item { display: grid; gap: 5px; text-align: left; border: 1px solid #dbe8f2; border-radius: 12px; background: #fff; padding: 14px; color: #315b78; font: inherit; cursor: pointer; }
.lesson-item b { color: #103e67; font-size: 17px; }
.lesson-item span { font-weight: 600; }
.lesson-item small, .lesson-item em { font-size: 12px; color: #718da1; font-style: normal; }
.lesson-item.selected { border-color: #1682c4; background: #edf7fd; box-shadow: 0 0 0 2px #d9effa; }
.editor-column { padding: 26px 32px; max-width: 920px; width: 100%; }
.editor-heading { display: flex; align-items: start; justify-content: space-between; gap: 16px; margin-bottom: 20px; }
.editor-heading h2 { font-size: 22px; }
.editor-heading p { margin-top: 6px; color: #718da1; font-size: 13px; }
.locked, .draft { border-radius: 20px; padding: 6px 12px; font-size: 12px; white-space: nowrap; }
.locked { background: #fff1df; color: #9a6411; }
.draft { background: #e5f6ed; color: #23794a; }
.notice { border-radius: 10px; padding: 12px 14px; margin-bottom: 18px; font-size: 13px; }
.notice.warning { background: #fff6e7; color: #8a641c; }
.field { display: grid; gap: 8px; position: relative; }
.field textarea { resize: vertical; min-height: 250px; border: 1px solid #cbdfea; border-radius: 12px; padding: 14px; font: inherit; line-height: 1.7; color: #103e67; background: #fff; }
.field small { position: absolute; right: 12px; bottom: 10px; color: #91a4b2; }
.field textarea:disabled { background: #f2f5f7; color: #657c8c; }
.audience { border: 0; display: flex; gap: 22px; margin-top: 18px; align-items: center; }
.audience legend { float: left; margin-right: 22px; font-size: 13px; font-weight: 600; color: #426682; }
.audience label, .publish-option { font-size: 14px; color: #426682; }
.publish-option { display: block; margin-top: 18px; }
.save-row { display: flex; align-items: center; justify-content: flex-end; gap: 16px; margin-top: 18px; }
.primary, .remind { border: 0; border-radius: 10px; padding: 10px 22px; background: #0165b3; color: #fff; font: inherit; font-weight: 600; cursor: pointer; }
.primary:disabled, .remind:disabled { opacity: .5; cursor: default; }
.absence-panel { display: flex; align-items: center; justify-content: space-between; gap: 20px; border-top: 1px solid #dbe8f2; margin-top: 28px; padding-top: 22px; }
.absence-panel h3 { font-size: 16px; margin-bottom: 7px; }
.absence-panel p { font-size: 13px; color: #657f92; line-height: 1.6; }
.remind { background: #b77b16; white-space: nowrap; }
.note { margin-top: 10px; color: #7b8f9d; font-size: 12px; line-height: 1.6; }
.hint { padding: 24px 8px; text-align: center; color: #7890a2; }
.error { color: #bd3c32; font-size: 13px; }
.success { color: #178a4c; font-size: 13px; }
.empty-editor { padding: 70px 20px; text-align: center; color: #7890a2; }
@media (max-width: 800px) {
  .content-studio { grid-template-columns: 1fr; }
  .lesson-column { border-right: 0; border-bottom: 1px solid #dbe8f2; }
  .editor-column { padding: 22px 18px; }
}
</style>
