CREATE TABLE organizations (
 id TEXT PRIMARY KEY, slug TEXT UNIQUE NOT NULL, created_at INTEGER NOT NULL
);
CREATE TABLE users (
 id TEXT PRIMARY KEY, org_id TEXT NOT NULL REFERENCES organizations(id),
 login TEXT NOT NULL, display_name TEXT NOT NULL, password_hash TEXT NOT NULL,
 active INTEGER NOT NULL CHECK(active IN (0,1)), created_at INTEGER NOT NULL,
 UNIQUE(org_id,login), UNIQUE(org_id,id)
);
CREATE TABLE roles (
 org_id TEXT NOT NULL, user_id TEXT NOT NULL,
 role TEXT NOT NULL CHECK(role IN ('admin','teacher','parent')),
 PRIMARY KEY(org_id,user_id,role), FOREIGN KEY(org_id,user_id) REFERENCES users(org_id,id)
);
CREATE TABLE sessions (
 digest TEXT PRIMARY KEY, org_id TEXT NOT NULL, user_id TEXT NOT NULL,
 expires_at INTEGER NOT NULL, created_at INTEGER NOT NULL,
 FOREIGN KEY(org_id,user_id) REFERENCES users(org_id,id)
);
CREATE TABLE login_limits (
 bucket TEXT PRIMARY KEY, failures INTEGER NOT NULL, window_start INTEGER NOT NULL,
 blocked_until INTEGER NOT NULL
);
CREATE TABLE classes (
 id TEXT PRIMARY KEY, org_id TEXT NOT NULL REFERENCES organizations(id),
 name TEXT NOT NULL, course_name TEXT NOT NULL, created_at INTEGER NOT NULL,
 UNIQUE(org_id,id), UNIQUE(org_id,name)
);
CREATE TABLE teacher_classes (
 org_id TEXT NOT NULL, user_id TEXT NOT NULL, class_id TEXT NOT NULL,
 PRIMARY KEY(org_id,user_id,class_id),
 FOREIGN KEY(org_id,user_id) REFERENCES users(org_id,id),
 FOREIGN KEY(org_id,class_id) REFERENCES classes(org_id,id)
);
CREATE TABLE students (
 id TEXT PRIMARY KEY, org_id TEXT NOT NULL, class_id TEXT NOT NULL,
 name TEXT NOT NULL, external_ref TEXT NOT NULL, remaining_units INTEGER NOT NULL,
 active INTEGER NOT NULL DEFAULT 1, source_device_id TEXT, source_revision INTEGER NOT NULL DEFAULT 0,
 UNIQUE(org_id,id), UNIQUE(org_id,external_ref),
 FOREIGN KEY(org_id,class_id) REFERENCES classes(org_id,id)
);
CREATE TABLE test_parent_links (
 org_id TEXT NOT NULL, user_id TEXT NOT NULL, student_id TEXT NOT NULL,
 created_at INTEGER NOT NULL, PRIMARY KEY(org_id,user_id,student_id),
 FOREIGN KEY(org_id,user_id) REFERENCES users(org_id,id),
 FOREIGN KEY(org_id,student_id) REFERENCES students(org_id,id)
);
CREATE TABLE devices (
 id TEXT PRIMARY KEY, org_id TEXT NOT NULL REFERENCES organizations(id), name TEXT NOT NULL,
 digest TEXT UNIQUE NOT NULL, active INTEGER NOT NULL CHECK(active IN (0,1)),
 expires_at INTEGER NOT NULL, created_at INTEGER NOT NULL, UNIQUE(org_id,id)
);
CREATE TABLE device_classes (
 org_id TEXT NOT NULL, device_id TEXT NOT NULL, class_id TEXT NOT NULL,
 PRIMARY KEY(org_id,device_id,class_id),
 FOREIGN KEY(org_id,device_id) REFERENCES devices(org_id,id),
 FOREIGN KEY(org_id,class_id) REFERENCES classes(org_id,id)
);
CREATE TABLE audit (
 id TEXT PRIMARY KEY, org_id TEXT NOT NULL, actor_id TEXT NOT NULL,
 action TEXT NOT NULL, entity_id TEXT NOT NULL, metadata TEXT NOT NULL, created_at INTEGER NOT NULL
);
CREATE INDEX sessions_user ON sessions(org_id,user_id);
CREATE INDEX students_class ON students(org_id,class_id);
CREATE INDEX audit_org ON audit(org_id,created_at,id);
CREATE TABLE lessons (
 id TEXT PRIMARY KEY, org_id TEXT NOT NULL, class_id TEXT NOT NULL, teacher_id TEXT NOT NULL,
 starts_at INTEGER NOT NULL, ends_at INTEGER NOT NULL, created_at INTEGER NOT NULL,
 UNIQUE(org_id,id), UNIQUE(org_id,class_id,starts_at),
 FOREIGN KEY(org_id,class_id) REFERENCES classes(org_id,id),
 FOREIGN KEY(org_id,teacher_id) REFERENCES users(org_id,id)
);
CREATE TABLE lesson_members (
 org_id TEXT NOT NULL, lesson_id TEXT NOT NULL, student_id TEXT NOT NULL,
 PRIMARY KEY(org_id,lesson_id,student_id),
 FOREIGN KEY(org_id,lesson_id) REFERENCES lessons(org_id,id),
 FOREIGN KEY(org_id,student_id) REFERENCES students(org_id,id)
);
CREATE TABLE leaves (
 id TEXT PRIMARY KEY, org_id TEXT NOT NULL, lesson_id TEXT NOT NULL, student_id TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('approved','cancelled')), reason TEXT NOT NULL,
 recorded_by TEXT NOT NULL, created_at INTEGER NOT NULL, cancelled_by TEXT, cancel_reason TEXT, cancelled_at INTEGER,
 UNIQUE(org_id,id), FOREIGN KEY(org_id,lesson_id) REFERENCES lessons(org_id,id),
 FOREIGN KEY(org_id,student_id) REFERENCES students(org_id,id)
);
CREATE UNIQUE INDEX one_active_leave ON leaves(org_id,lesson_id,student_id) WHERE status='approved';
CREATE TABLE attendance (
 id TEXT PRIMARY KEY, org_id TEXT NOT NULL, lesson_id TEXT NOT NULL, student_id TEXT NOT NULL,
 device_id TEXT NOT NULL, occurred_at INTEGER NOT NULL, consumed_units INTEGER NOT NULL,
 remaining_units INTEGER NOT NULL, source TEXT NOT NULL, revoked INTEGER NOT NULL DEFAULT 0,
 UNIQUE(org_id,id),
 FOREIGN KEY(org_id,lesson_id) REFERENCES lessons(org_id,id),
 FOREIGN KEY(org_id,student_id) REFERENCES students(org_id,id),
 FOREIGN KEY(org_id,device_id) REFERENCES devices(org_id,id)
);
CREATE TABLE inbound_events (
 org_id TEXT NOT NULL, device_id TEXT NOT NULL, event_ref TEXT NOT NULL, payload_digest TEXT NOT NULL,
 response_json TEXT NOT NULL, created_at INTEGER NOT NULL,
 PRIMARY KEY(org_id,device_id,event_ref), FOREIGN KEY(org_id,device_id) REFERENCES devices(org_id,id)
);
CREATE UNIQUE INDEX one_active_attendance ON attendance(org_id,lesson_id,student_id) WHERE revoked=0;
CREATE TABLE notification_preferences (
 org_id TEXT NOT NULL, user_id TEXT NOT NULL, event_type TEXT NOT NULL,
 enabled INTEGER NOT NULL, updated_at INTEGER NOT NULL,
 PRIMARY KEY(org_id,user_id,event_type), FOREIGN KEY(org_id,user_id) REFERENCES users(org_id,id)
);
CREATE TABLE outbox (
 id TEXT PRIMARY KEY, org_id TEXT NOT NULL, notice_type TEXT NOT NULL, origin_id TEXT NOT NULL,
 recipient_id TEXT NOT NULL, student_id TEXT NOT NULL DEFAULT '', class_id TEXT NOT NULL DEFAULT '',
 payload TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('pending_simulation','simulated','cancelled','expired')),
 expires_at INTEGER NOT NULL, created_at INTEGER NOT NULL, simulated_at INTEGER, cancel_reason TEXT,
 UNIQUE(org_id,notice_type,origin_id,recipient_id,student_id),
 FOREIGN KEY(org_id,recipient_id) REFERENCES users(org_id,id)
);
CREATE TABLE low_hours_state (
 org_id TEXT NOT NULL, student_id TEXT NOT NULL, active INTEGER NOT NULL,
 PRIMARY KEY(org_id,student_id), FOREIGN KEY(org_id,student_id) REFERENCES students(org_id,id)
);
CREATE TABLE lesson_content (
 org_id TEXT NOT NULL, lesson_id TEXT NOT NULL, body TEXT NOT NULL, audience TEXT NOT NULL,
 publish_after_class INTEGER NOT NULL, edited_by TEXT NOT NULL, updated_at INTEGER NOT NULL,
 queued_at INTEGER, PRIMARY KEY(org_id,lesson_id), FOREIGN KEY(org_id,lesson_id) REFERENCES lessons(org_id,id)
);
CREATE TABLE student_activity (
 id TEXT PRIMARY KEY, org_id TEXT NOT NULL, student_id TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('new','renewal')),
 occurred_at INTEGER NOT NULL, source_ref TEXT NOT NULL, class_name TEXT NOT NULL,
 UNIQUE(org_id,source_ref), FOREIGN KEY(org_id,student_id) REFERENCES students(org_id,id)
);
CREATE TABLE notification_schedule (
 org_id TEXT PRIMARY KEY REFERENCES organizations(id), enabled INTEGER NOT NULL, enabled_since INTEGER NOT NULL,
 weekly_weekday INTEGER NOT NULL, weekly_hour INTEGER NOT NULL, weekly_minute INTEGER NOT NULL,
 monthly_delay_minutes INTEGER NOT NULL, low_hours_units INTEGER NOT NULL
);
CREATE TABLE notice_skips (
 org_id TEXT NOT NULL, reason TEXT NOT NULL, count INTEGER NOT NULL,
 PRIMARY KEY(org_id,reason)
);
CREATE INDEX lessons_period ON lessons(org_id,starts_at);
CREATE INDEX outbox_pending ON outbox(org_id,status,created_at);
PRAGMA user_version=1;
PRAGMA application_id=1465405012;
