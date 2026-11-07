import { defineStore } from 'pinia'
import { api, ApiError } from '../api/client'
import { dbAdd, dbAll, dbDel } from '../offline/db'

// 平板签到状态。设备令牌由管理员在服务端签发（POST /v1/admin/devices），
// 每台平板配置一次；签到/余额/撤销事件必须走设备令牌。
//
// student_revision 规则（server/business/workflows.py apply_balance）：
// 服务器只接受比已知值更大的 revision 才更新余额快照。
// 这里用“毫秒时间戳 + 本机单调递增”生成，天然大于历史小计数器。
//
// 离线 outbox：网络断开（ApiError status=0）时事件进入 IndexedDB 队列，
// UI 标记“待补传”；联网后按 seq 顺序原样补传。event_ref 入队时已固定，
// 重复补传命中服务端幂等（duplicate=true），不会重复扣课时。

const TOKEN_KEY = 'fs_device_token'
const REV_KEY = 'fs_device_revisions'

function loadRevisions() {
  try { return JSON.parse(localStorage.getItem(REV_KEY) || '{}') } catch (e) { return {} }
}

export const useKioskStore = defineStore('kiosk', {
  state: () => ({
    deviceToken: localStorage.getItem(TOKEN_KEY) || '',
    deviceInfo: null,
    revisions: loadRevisions(),
    pendingCount: 0,
    flushing: false,
    // 默认扣课：100 = 1 课时（服务端 remaining_units 约定）
    defaultConsumedUnits: 100
  }),
  getters: {
    deviceReady: (s) => !!s.deviceToken
  },
  actions: {
    async setDeviceToken(token) {
      const info = await api.deviceMe(token)
      if (!info || info.kind === 'user') {
        throw new ApiError(422, '这不是设备令牌，请确认后重试')
      }
      this.deviceToken = token
      this.deviceInfo = info
      localStorage.setItem(TOKEN_KEY, token)
    },
    clearDeviceToken() {
      this.deviceToken = ''
      this.deviceInfo = null
      localStorage.removeItem(TOKEN_KEY)
    },
    nextRevision(studentId) {
      const now = Date.now()
      const last = this.revisions[studentId] || 0
      const next = now > last ? now : last + 1
      this.revisions[studentId] = next
      localStorage.setItem(REV_KEY, JSON.stringify(this.revisions))
      return next
    },
    newEventRef(prefix) {
      const uuid = (crypto.randomUUID && crypto.randomUUID()) ||
        ('xxxxxxxxxxxx4xxxyxxxxxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
          const r = (Math.random() * 16) | 0
          return (c === 'x' ? r : (r & 0x3) | 0x8).toString(16)
        }))
      return prefix + ':' + uuid
    },

    async refreshPending() {
      try {
        this.pendingCount = (await dbAll('outbox')).length
      } catch (e) { /* IndexedDB 不可用时不阻塞主流程 */ }
    },

    async enqueue(kind, event) {
      await dbAdd('outbox', { kind, event, token: this.deviceToken, createdAt: Date.now() })
      await this.refreshPending()
    },

    // 撤销一条还没上传的签到：直接从队列里拿掉，无需通知服务器
    async cancelQueued(studentId, lessonId) {
      const items = await dbAll('outbox')
      const hit = items.find((i) =>
        i.kind === 'attendance' && i.event.student_id === studentId && i.event.lesson_id === lessonId)
      if (!hit) return false
      await dbDel('outbox', hit.seq)
      await this.refreshPending()
      return true
    },

    // 按序补传。任一事件网络失败即停（保持顺序），401 停（令牌失效），
    // 服务端幂等命中（duplicate）视为成功。返回本次成功补传条数。
    async flushOutbox() {
      if (this.flushing) return 0
      this.flushing = true
      let done = 0
      try {
        const items = await dbAll('outbox')
        for (const item of items) {
          try {
            if (item.kind === 'attendance') {
              await api.deviceAttendance(item.event, item.token)
            } else if (item.kind === 'balance') {
              await api.deviceBalance(item.event, item.token)
            } else if (item.kind === 'revoke') {
              await api.revokeAttendance(item.event.attendance_id, item.event.event_ref, item.event.reason, item.token)
            } else {
              await dbDel('outbox', item.seq) // 未知类型，丢弃防堵
              continue
            }
            await dbDel('outbox', item.seq)
            done++
          } catch (e) {
            // 网络仍不通或令牌失效：保留队列，下次再试
            break
          }
        }
      } finally {
        this.flushing = false
        await this.refreshPending()
      }
      return done
    },

    // 签到：成功/幂等重复都返回服务端回执；断网时入队并标记 offline
    async checkin(lessonId, member, consumedUnits) {
      const consumed = consumedUnits || this.defaultConsumedUnits
      const event = {
        event_ref: this.newEventRef('att'),
        lesson_id: lessonId,
        student_id: member.id,
        occurred_at: new Date().toISOString(),
        consumed_units: consumed,
        remaining_units: member.remaining_units - consumed,
        student_revision: this.nextRevision(member.id),
        source: 'live'
      }
      try {
        const receipt = await api.deviceAttendance(event, this.deviceToken)
        return { receipt, event, offline: false }
      } catch (e) {
        if (e instanceof ApiError && e.status === 0) {
          await this.enqueue('attendance', event)
          return { receipt: { id: null, duplicate: false }, event, offline: true }
        }
        throw e
      }
    },

    // 撤销签到：服务端不自动退课时，需另发一笔更高 revision 的余额事件。
    // refundUnits 由调用方算好（撤销时余额 + 当时扣除的课时），
    // 因为 member.remaining_units 在签到后已被改写为扣后值。
    // 断网：撤销事件 + 余额事件按序入队，恢复后一并补传。
    async undoCheckin(attendanceId, member, refundUnits, reason) {
      const revokeRef = this.newEventRef('rev')
      const balanceEvent = {
        event_ref: this.newEventRef('bal'),
        student_id: member.id,
        occurred_at: new Date().toISOString(),
        remaining_units: refundUnits,
        student_revision: this.nextRevision(member.id),
        source: 'live'
      }
      try {
        const receipt = await api.revokeAttendance(attendanceId, revokeRef, reason || '误操作撤销', this.deviceToken)
        const balance = await api.deviceBalance(balanceEvent, this.deviceToken)
        return { receipt, balance, offline: false }
      } catch (e) {
        if (e instanceof ApiError && e.status === 0) {
          await this.enqueue('revoke', { attendance_id: attendanceId, event_ref: revokeRef, reason: reason || '误操作撤销' })
          await this.enqueue('balance', balanceEvent)
          return { offline: true }
        }
        throw e
      }
    }
  }
})
