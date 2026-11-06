// 未来学校业务 API 客户端
// 约定见 server/business/API.md：
// - 全部业务接口 Bearer 令牌；机构由令牌确定，前端不传角色/机构
// - 错误码：401 登录失效 / 403 无权限 / 404 跨机构 / 409 冲突 / 422 字段错误 / 429 限流 / 503 存储忙

export class ApiError extends Error {
  constructor(status, detail) {
    super(detail || ('HTTP ' + status))
    this.status = status
  }
}

// options.token：覆盖默认用户令牌（设备事件须用设备令牌）
async function request(method, path, body, options) {
  const headers = { Accept: 'application/json' }
  const token = (options && options.token) || sessionStorage.getItem('fs_token')
  if (token) headers.Authorization = 'Bearer ' + token
  const init = { method, headers }
  if (body !== undefined) {
    headers['Content-Type'] = 'application/json'
    init.body = JSON.stringify(body)
  }
  let res
  try {
    res = await fetch(path, init)
  } catch (e) {
    // 网络断开：交给上层离线队列处理（阶段3）
    throw new ApiError(0, '网络不可用')
  }
  if (res.status === 204) return null
  const data = await res.json().catch(() => null)
  if (!res.ok) {
    throw new ApiError(res.status, data && (data.detail || data.message || (data.error && data.error.message)))
  }
  return data
}

export const api = {
  // 身份
  login: (organization, login, password) =>
    request('POST', '/v1/auth/login', { organization, login, password }),
  logout: () => request('POST', '/v1/auth/logout'),
  me: () => request('GET', '/v1/me'),

  // 班级与学生（按令牌权限范围返回）
  classes: () => request('GET', '/v1/classes'),
  students: (after, limit) => {
    const q = new URLSearchParams()
    if (after) q.set('after', after)
    if (limit) q.set('limit', String(limit))
    const s = q.toString()
    return request('GET', '/v1/students' + (s ? '?' + s : ''))
  },

  // 课堂
  lessonsByDay: (day, scope) =>
    request('GET', '/v1/lessons?day=' + day + (scope ? '&scope=' + scope : '')),
  lesson: (id) => request('GET', '/v1/lessons/' + id),
  lessonWorkspace: (id) => request('GET', '/v1/lessons/' + id + '/workspace'),

  // 请假
  addLeave: (lessonId, studentId, reason) =>
    request('POST', '/v1/lessons/' + lessonId + '/leaves', { student_id: studentId, reason }),
  cancelLeave: (leaveId, reason) =>
    request('POST', '/v1/leaves/' + leaveId + '/cancel', { reason }),

  // 周报：weekStart 必须是周一 YYYY-MM-DD；own=true 走教师本人范围
  weeklyReport: (weekStart, own) =>
    request('GET', (own ? '/v1/me/reports/weekly?week_start=' : '/v1/reports/weekly?week_start=') + weekStart),
  monthlyReport: (month) => request('GET', '/v1/reports/monthly?month=' + month),

  // 候选管理库：请假/补签审批；角色由用户在当前工作身份中明确选择
  managementRequests: (workspaceRole, before, limit) => {
    const q = new URLSearchParams({ workspace_role: workspaceRole })
    if (before) q.set('before', before)
    if (limit) q.set('limit', String(limit))
    return request('GET', '/v1/management/requests?' + q.toString())
  },
  decideManagementRequest: (requestId, workspaceRole, approve, reason, decisionRef) =>
    request('POST', '/v1/management/requests/' + requestId + '/decision',
      { workspace_role: workspaceRole, approve, reason, decision_ref: decisionRef }),

  // 课堂内容（草稿/课后发布）与未到提醒
  getContent: (lessonId) => request('GET', '/v1/lessons/' + lessonId + '/content'),
  putContent: (lessonId, body, audience, publishAfterClass) =>
    request('PUT', '/v1/lessons/' + lessonId + '/content',
      { body, audience, publish_after_class: !!publishAfterClass }),
  absenceNotices: (lessonId, studentIds) =>
    request('POST', '/v1/lessons/' + lessonId + '/absence-notices', { student_ids: studentIds }),

  // 通知记录（模拟，非真实微信发送）
  myNotifications: (after, limit) => {
    const q = new URLSearchParams()
    if (after) q.set('after', after)
    if (limit) q.set('limit', String(limit))
    const s = q.toString()
    return request('GET', '/v1/me/notifications' + (s ? '?' + s : ''))
  },

  // 设备事件（平板签到；event_ref 幂等由客户端生成；必须用设备令牌）
  deviceAttendance: (event, deviceToken) =>
    request('POST', '/v1/device/attendance', event, { token: deviceToken }),
  deviceBalance: (event, deviceToken) =>
    request('POST', '/v1/device/balance', event, { token: deviceToken }),
  revokeAttendance: (attendanceId, eventRef, reason, deviceToken) =>
    request('POST', '/v1/device/attendance/' + attendanceId + '/revoke', { event_ref: eventRef, reason }, { token: deviceToken }),
  // 用设备令牌查询设备身份（校验令牌有效性）
  deviceMe: (deviceToken) => request('GET', '/v1/me', undefined, { token: deviceToken })
}
