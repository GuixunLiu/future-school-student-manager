/* Candidate receiver, deliberately NOT loaded by the daily tablet app yet.
 * Transport must authenticate the pinned server before calling receive().
 * Storage CAS atomically persists the complete app snapshot AND receipt in one
 * write. Never call the old saveState/capture hook: that would emit a second
 * attendance deduction back to the server. No faces or other students change.
 */
(function(root) {
  'use strict';
  const fail = code => { const error = new Error(code); error.code = code; throw error; };
  const integer = value => Number.isSafeInteger(value);
  const units = value => {
    if (typeof value !== 'number' || !Number.isFinite(value)) fail('balance_changed');
    const result = Math.round(value * 100);
    if (!integer(result) || Math.abs(result - value * 100) > 0.00001) fail('balance_changed');
    return result;
  };
  const own = (obj, key) => Object.prototype.hasOwnProperty.call(obj || {}, key);
  const chinaDate = seconds => new Date((seconds + 28800) * 1000).toISOString().slice(0, 10);
  const isText = value => typeof value === 'string' && value.length > 0 && value.length <= 200;

  async function verified(envelope) {
    if (!envelope || !isText(envelope.org_id) || !isText(envelope.device_id) || !isText(envelope.generation)) fail('invalid_event');
    const item = envelope.item;
    if (!item || typeof item.event_json !== 'string' || item.event_json.length > 16000 ||
        !/^[0-9a-f]{64}$/.test(item.sha256) || !/^[0-9a-f]{32}$/.test(item.receipt_token)) fail('invalid_event');
    const hash = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(item.event_json));
    if (Array.from(new Uint8Array(hash), b => b.toString(16).padStart(2, '0')).join('') !== item.sha256) fail('invalid_event');
    const e = JSON.parse(item.event_json);
    const keys = ['event_id','request_id','org_id','lesson_id','student_id','class_id','decided_by','decided_by_name','reason'];
    if (keys.some(k => !isText(e[k])) || e.protocol !== 1 || e.org_id !== envelope.org_id ||
        !['arrived','leave'].includes(e.status) || e.expected !== true) fail('invalid_event');
    const numbers = ['previous_attendance_revision','attendance_revision','previous_balance_revision',
      'balance_revision','previous_balance_units','balance_units','balance_delta','charge_units','starts_at','effective_at'];
    if (numbers.some(k => !integer(e[k])) || e.previous_attendance_revision < 0 || e.previous_balance_revision < 0 ||
        e.attendance_revision !== e.previous_attendance_revision + 1 || e.charge_units <= 0 ||
        e.starts_at < 0 || e.effective_at < 0 || e.effective_at > 253402185599 || e.starts_at > 253402185599 ||
        e.balance_units !== e.previous_balance_units + e.balance_delta ||
        e.balance_revision !== e.previous_balance_revision + (e.status === 'arrived' ? 1 : 0) ||
        e.balance_delta !== (e.status === 'arrived' ? -e.charge_units : 0) ||
        (e.status === 'arrived' && e.effective_at < e.starts_at)) fail('invalid_event');
    return e;
  }

  function prepare(raw, envelope, e) {
    const state = JSON.parse(raw), sync = state.managementSync;
    if (!sync || sync.protocol !== 1 || sync.enabled !== true || sync.mode !== 'server_authoritative' ||
        sync.orgId !== envelope.org_id || sync.deviceId !== envelope.device_id || sync.generation !== envelope.generation) fail('mapping_changed');
    // A legacy event writer or mirror can still be active only until cutover.
    // Block rather than mix independent authorities or silently turn them off.
    if (state.deviceSync?.captureEnabled || sync.legacyMirrorDisabled !== true) fail('local_changes');
    const receipts = sync.receipts;
    if (!receipts || typeof receipts !== 'object' || Array.isArray(receipts)) fail('mapping_changed');
    if (own(receipts, e.event_id)) {
      if (receipts[e.event_id].sha256 !== envelope.item.sha256) fail('invalid_event');
      return {duplicate:true, nextRaw:raw}; // persisted once, even if later events changed its balance
    }
    if (Object.keys(receipts).length >= 10000) fail('storage_failed'); // explicit checkpoint required, never silently evict
    if (!Array.isArray(sync.queue) || sync.queue.some(q => q.status !== 'accepted')) fail('local_changes');
    if (!own(sync.students, e.student_id) || !own(sync.lessons, e.lesson_id)) fail('mapping_changed');
    const binding = sync.students[e.student_id], lesson = sync.lessons[e.lesson_id];
    if (!integer(binding.localId) || binding.classId !== e.class_id || lesson.classId !== e.class_id ||
        lesson.startsAt !== e.starts_at || lesson.date !== chinaDate(e.starts_at) ||
        !Array.isArray(lesson.studentIds) || !lesson.studentIds.includes(e.student_id)) fail('mapping_changed');
    if (!Array.isArray(state.students) || !Array.isArray(state.classes) || !Array.isArray(state.attendance) ||
        !Array.isArray(state.transactions) || !Array.isArray(state.settings?.studentLeaves)) fail('mapping_changed');
    const students = state.students.filter(s => s.id === binding.localId);
    const classes = state.classes.filter(c => c.id === binding.localClassId);
    if (students.length !== 1 || classes.length !== 1) fail('mapping_changed');
    const s = students[0], c = classes[0];
    if (s.studentNo !== binding.studentNo || s.name !== binding.name || s.className !== binding.className ||
        c.name !== binding.className || c.teacher !== binding.teacher || s.status === 'archived' || s.longTermAbsent === true) fail('mapping_changed');
    const facts = sync.facts?.[e.lesson_id];
    if (!own(facts, e.student_id)) fail('mapping_changed');
    const fact = facts[e.student_id];
    if (fact.status !== 'missing' || fact.expected !== true || fact.revision !== e.previous_attendance_revision ||
        fact.chargeUnits !== e.charge_units) fail('attendance_changed');
    if (binding.balanceRevision !== e.previous_balance_revision || binding.balanceUnits !== e.previous_balance_units ||
        units(s.hours) !== e.previous_balance_units) fail('balance_changed');
    if (state.attendance.some(a => a.studentId === s.id && a.date === lesson.date) ||
        state.settings.studentLeaves.some(l => l.studentId === s.id && l.date === lesson.date && l.status === 'approved')) fail('attendance_changed');
    let last = 0;
    for (const rows of [state.attendance,state.transactions,state.settings.studentLeaves]) {
      for (const row of rows) { if (!integer(row.id) || row.id <= 0) fail('mapping_changed'); last = Math.max(last,row.id); }
    }
    const nextId = () => { last++; if (!integer(last) || last <= 0) fail('mapping_changed'); return last; };
    const snapshot = {classId:c.id,className:binding.className,teacher:binding.teacher,classroom:c.classroom || ''};
    const source = {serverEventId:e.event_id,serverRequestId:e.request_id,source:'web_approval',approvedAt:new Date(e.effective_at * 1000).toISOString()};
    if (e.status === 'arrived') {
      const attendanceId = nextId(), time = new Date((e.starts_at + 28800) * 1000).toISOString().slice(11,16);
      state.attendance.push({id:attendanceId,studentId:s.id,date:lesson.date,time,method:'网页审批补签',makeup:true,...snapshot,...source});
      state.transactions.push({id:nextId(),attendanceId,studentId:s.id,amount:e.balance_delta/100,
        reason:'网页审批补签扣减',date:lesson.date,time,...snapshot,...source});
    } else {
      state.settings.studentLeaves.push({id:nextId(),studentId:s.id,studentNo:s.studentNo,date:lesson.date,
        status:'approved',reason:e.reason,recordedBy:e.decided_by_name,createdAt:source.approvedAt,...snapshot,...source});
    }
    s.hours = e.balance_units / 100; // absolute canonical result, never subtract from this balance again
    binding.balanceUnits = e.balance_units;
    binding.balanceRevision = e.balance_revision;
    fact.status = e.status;
    fact.revision = e.attendance_revision;
    receipts[e.event_id] = {sha256:envelope.item.sha256,attendanceRevision:e.attendance_revision,
      balanceRevision:e.balance_revision,balanceUnits:e.balance_units};
    return {duplicate:false,nextRaw:JSON.stringify(state)};
  }

  async function receive(storage, envelope) {
    let e;
    try {
      e = await verified(envelope);
      // Re-read AFTER asynchronous hash verification: no stale state overwrites
      // a local check-in that happened while verification was in progress.
      let raw;
      try { raw = storage.read(); } catch (_) { fail('storage_failed'); }
      const plan = prepare(raw, envelope, e);
      if (!plan.duplicate) {
        try { if (storage.compareAndSwap(raw, plan.nextRaw) !== true) fail('storage_failed'); }
        catch (_) { fail('storage_failed'); }
      }
      return {ok:true,duplicate:plan.duplicate,receipt:{generation:envelope.generation,event_id:e.event_id,
        receipt_token:envelope.item.receipt_token,sha256:envelope.item.sha256,status:'applied',
        attendance_revision:e.attendance_revision,balance_revision:e.balance_revision,balance_units:e.balance_units}};
    } catch (error) {
      const codes = ['mapping_changed','local_changes','attendance_changed','balance_changed','invalid_event','storage_failed'];
      const code = codes.includes(error.code) ? error.code : 'invalid_event';
      return {ok:false,code}; // do not claim successful application or emit a forged acknowledgement
    }
  }
  const api = {receive};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.ManagementApprovalReceiver = api;
})(globalThis);
