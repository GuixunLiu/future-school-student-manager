"""PostgreSQL runtime schema for the final tablet/web/WeChat product scope.

Base identities come from fresh_start.POSTGRES_SCHEMA. Historical business rows
stay in read-only archives and are intentionally absent from this runtime schema.
"""
from .fresh_start import POSTGRES_SCHEMA


POSTGRES_RUNTIME_SCHEMA = """
CREATE TABLE schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at BIGINT NOT NULL
);
CREATE TABLE user_credentials (
    org_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    changed_at BIGINT NOT NULL,
    PRIMARY KEY(org_id, user_id),
    FOREIGN KEY(org_id, user_id) REFERENCES users(org_id, id)
);
CREATE TABLE auth_sessions (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    token_hash TEXT UNIQUE NOT NULL,
    created_at BIGINT NOT NULL,
    expires_at BIGINT NOT NULL,
    revoked_at BIGINT,
    FOREIGN KEY(org_id, user_id) REFERENCES users(org_id, id),
    CHECK(expires_at > created_at)
);
CREATE TABLE user_versions (
    org_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    version INTEGER NOT NULL CHECK(version >= 0),
    updated_at BIGINT NOT NULL,
    PRIMARY KEY(org_id, user_id),
    FOREIGN KEY(org_id, user_id) REFERENCES users(org_id, id)
);
CREATE TABLE runtime_login_limits (
    bucket TEXT PRIMARY KEY,
    failures INTEGER NOT NULL CHECK(failures >= 0),
    window_start BIGINT NOT NULL,
    blocked_until BIGINT NOT NULL
);
CREATE TABLE device_credentials (
    org_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    generation TEXT NOT NULL,
    token_hash TEXT UNIQUE NOT NULL,
    issued_at BIGINT NOT NULL,
    expires_at BIGINT NOT NULL,
    revoked_at BIGINT,
    PRIMARY KEY(org_id, device_id),
    FOREIGN KEY(org_id, device_id) REFERENCES devices(org_id, id),
    CHECK(expires_at > issued_at)
);
CREATE TABLE device_enrollments (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    generation TEXT NOT NULL,
    code_hash TEXT UNIQUE NOT NULL,
    issued_by TEXT NOT NULL,
    issued_at BIGINT NOT NULL,
    expires_at BIGINT NOT NULL,
    consumed_at BIGINT,
    FOREIGN KEY(org_id, device_id) REFERENCES devices(org_id, id),
    FOREIGN KEY(org_id, issued_by) REFERENCES users(org_id, id),
    CHECK(expires_at > issued_at)
);
CREATE TABLE device_classes (
    org_id TEXT NOT NULL,
    device_id TEXT NOT NULL,
    class_id TEXT NOT NULL,
    PRIMARY KEY(org_id, device_id, class_id),
    FOREIGN KEY(org_id, device_id) REFERENCES devices(org_id, id),
    FOREIGN KEY(org_id, class_id) REFERENCES classes(org_id, id)
);
CREATE TABLE lessons (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    class_id TEXT NOT NULL,
    schedule_id TEXT,
    starts_at BIGINT NOT NULL,
    ends_at BIGINT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('scheduled','cancelled')),
    created_at BIGINT NOT NULL,
    UNIQUE(org_id, id),
    UNIQUE(org_id, class_id, id),
    FOREIGN KEY(org_id, class_id) REFERENCES classes(org_id, id),
    FOREIGN KEY(org_id, schedule_id) REFERENCES class_schedule_templates(org_id, id),
    CHECK(ends_at > starts_at)
);
CREATE UNIQUE INDEX lessons_scheduled_class_time
    ON lessons(org_id, class_id, starts_at) WHERE state='scheduled';
CREATE TABLE lesson_versions (
    org_id TEXT NOT NULL,
    lesson_id TEXT NOT NULL,
    version INTEGER NOT NULL CHECK(version >= 0),
    updated_at BIGINT NOT NULL,
    PRIMARY KEY(org_id, lesson_id),
    FOREIGN KEY(org_id, lesson_id) REFERENCES lessons(org_id, id)
);
CREATE TABLE lesson_charge_rules (
    org_id TEXT NOT NULL,
    lesson_id TEXT NOT NULL,
    charge_units INTEGER NOT NULL CHECK(charge_units > 0),
    revision INTEGER NOT NULL CHECK(revision >= 1),
    updated_at BIGINT NOT NULL,
    PRIMARY KEY(org_id, lesson_id),
    FOREIGN KEY(org_id, lesson_id) REFERENCES lessons(org_id, id)
);
CREATE TABLE lesson_teachers (
    org_id TEXT NOT NULL,
    lesson_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    teacher_name TEXT NOT NULL,
    PRIMARY KEY(org_id, lesson_id, user_id),
    FOREIGN KEY(org_id, lesson_id) REFERENCES lessons(org_id, id),
    FOREIGN KEY(org_id, user_id) REFERENCES users(org_id, id)
);
CREATE TABLE class_versions (
    org_id TEXT NOT NULL,
    class_id TEXT NOT NULL,
    version INTEGER NOT NULL CHECK(version >= 0),
    updated_at BIGINT NOT NULL,
    PRIMARY KEY(org_id, class_id),
    FOREIGN KEY(org_id, class_id) REFERENCES classes(org_id, id)
);
CREATE TABLE student_versions (
    org_id TEXT NOT NULL,
    student_id TEXT NOT NULL,
    version INTEGER NOT NULL CHECK(version >= 0),
    updated_at BIGINT NOT NULL,
    PRIMARY KEY(org_id, student_id),
    FOREIGN KEY(org_id, student_id) REFERENCES students(org_id, id)
);
CREATE TABLE lesson_students (
    org_id TEXT NOT NULL,
    class_id TEXT NOT NULL,
    lesson_id TEXT NOT NULL,
    student_id TEXT NOT NULL,
    expected BOOLEAN NOT NULL,
    charge_units INTEGER NOT NULL CHECK(charge_units > 0),
    PRIMARY KEY(org_id, lesson_id, student_id),
    UNIQUE(org_id, lesson_id, student_id),
    FOREIGN KEY(org_id, class_id, lesson_id) REFERENCES lessons(org_id, class_id, id),
    FOREIGN KEY(org_id, student_id) REFERENCES students(org_id, id)
);
CREATE TABLE attendance_records (
    org_id TEXT NOT NULL,
    lesson_id TEXT NOT NULL,
    student_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('missing','arrived','leave')),
    revision INTEGER NOT NULL CHECK(revision >= 0),
    last_event_id TEXT,
    updated_at BIGINT NOT NULL,
    PRIMARY KEY(org_id, lesson_id, student_id),
    FOREIGN KEY(org_id, lesson_id, student_id) REFERENCES lesson_students(org_id, lesson_id, student_id)
);
CREATE TABLE attendance_events (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    lesson_id TEXT NOT NULL,
    student_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('tablet_checkin','web_face_checkin','makeup','leave','cancel_leave','revoke')),
    event_ref TEXT NOT NULL,
    actor_type TEXT NOT NULL CHECK(actor_type IN ('device','user')),
    actor_id TEXT NOT NULL,
    reason TEXT,
    occurred_at BIGINT NOT NULL,
    recorded_at BIGINT NOT NULL,
    UNIQUE(org_id, actor_type, actor_id, event_ref),
    UNIQUE(org_id, lesson_id, student_id, id),
    FOREIGN KEY(org_id, lesson_id, student_id) REFERENCES lesson_students(org_id, lesson_id, student_id)
);
ALTER TABLE attendance_records
    ADD CONSTRAINT attendance_records_last_event_fk
    FOREIGN KEY(org_id, lesson_id, student_id, last_event_id)
    REFERENCES attendance_events(org_id, lesson_id, student_id, id);
CREATE TABLE student_balances (
    org_id TEXT NOT NULL,
    student_id TEXT NOT NULL,
    units INTEGER NOT NULL CHECK(units BETWEEN -1000000 AND 10000000),
    revision INTEGER NOT NULL CHECK(revision >= 0),
    updated_at BIGINT NOT NULL,
    PRIMARY KEY(org_id, student_id),
    FOREIGN KEY(org_id, student_id) REFERENCES students(org_id, id)
);
CREATE TABLE balance_ledger (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    student_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('opening','checkin','makeup','adjustment','renewal','reversal')),
    source_event_id TEXT NOT NULL,
    previous_units INTEGER NOT NULL,
    units_delta INTEGER NOT NULL,
    units INTEGER NOT NULL,
    previous_revision INTEGER NOT NULL,
    revision INTEGER NOT NULL,
    actor_type TEXT NOT NULL CHECK(actor_type IN ('system','device','user')),
    actor_id TEXT,
    reason TEXT NOT NULL,
    created_at BIGINT NOT NULL,
    UNIQUE(org_id, student_id, source_event_id),
    FOREIGN KEY(org_id, student_id) REFERENCES student_balances(org_id, student_id),
    CHECK(revision = previous_revision + 1),
    CHECK(units = previous_units + units_delta)
);
CREATE TABLE enrollment_records (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    student_id TEXT NOT NULL,
    class_id TEXT NOT NULL,
    occurred_at BIGINT NOT NULL,
    source_ref TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    created_at BIGINT NOT NULL,
    UNIQUE(org_id, student_id),
    UNIQUE(org_id, source_ref),
    FOREIGN KEY(org_id, student_id) REFERENCES students(org_id, id),
    FOREIGN KEY(org_id, class_id) REFERENCES classes(org_id, id),
    FOREIGN KEY(org_id, actor_id) REFERENCES users(org_id, id)
);
CREATE TABLE renewal_records (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    student_id TEXT NOT NULL,
    course_id TEXT NOT NULL,
    units_added INTEGER NOT NULL CHECK(units_added > 0),
    amount_cents INTEGER NOT NULL CHECK(amount_cents >= 0),
    unit_price_cents INTEGER NOT NULL CHECK(unit_price_cents >= 0),
    occurred_at BIGINT NOT NULL,
    source_ref TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at BIGINT NOT NULL,
    UNIQUE(org_id, source_ref),
    FOREIGN KEY(org_id, student_id) REFERENCES students(org_id, id),
    FOREIGN KEY(org_id, course_id) REFERENCES courses(org_id, id),
    FOREIGN KEY(org_id, actor_id) REFERENCES users(org_id, id)
);
CREATE TABLE face_profiles (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    student_id TEXT NOT NULL,
    model_version TEXT NOT NULL,
    descriptor_ciphertext BYTEA NOT NULL,
    consent_ref TEXT NOT NULL,
    active BOOLEAN NOT NULL,
    created_at BIGINT NOT NULL,
    retired_at BIGINT,
    FOREIGN KEY(org_id, student_id) REFERENCES students(org_id, id)
);
CREATE UNIQUE INDEX face_profiles_active_student
    ON face_profiles(org_id, student_id) WHERE active;
CREATE TABLE wechat_identities (
    org_id TEXT NOT NULL,
    openid TEXT NOT NULL,
    unionid TEXT,
    created_at BIGINT NOT NULL,
    PRIMARY KEY(org_id, openid),
    UNIQUE(org_id, unionid)
);
CREATE TABLE parent_student_bindings (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    openid TEXT NOT NULL,
    student_id TEXT NOT NULL,
    bound_at BIGINT NOT NULL,
    unbound_at BIGINT,
    UNIQUE(org_id, id),
    FOREIGN KEY(org_id, openid) REFERENCES wechat_identities(org_id, openid),
    FOREIGN KEY(org_id, student_id) REFERENCES students(org_id, id)
);
CREATE UNIQUE INDEX parent_student_active_binding
    ON parent_student_bindings(org_id, openid, student_id) WHERE unbound_at IS NULL;
CREATE TABLE notification_outbox (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    binding_id TEXT NOT NULL,
    student_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('attendance','renewal','low_hours')),
    source_event_id TEXT NOT NULL,
    payload JSONB NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending','sending','sent','failed')),
    attempts INTEGER NOT NULL CHECK(attempts >= 0),
    next_attempt_at BIGINT NOT NULL,
    last_error TEXT,
    provider_message_id TEXT,
    created_at BIGINT NOT NULL,
    sent_at BIGINT,
    UNIQUE(org_id, kind, source_event_id, binding_id),
    FOREIGN KEY(org_id, binding_id) REFERENCES parent_student_bindings(org_id, id),
    FOREIGN KEY(org_id, student_id) REFERENCES students(org_id, id)
);
CREATE TABLE report_runs (
    id TEXT PRIMARY KEY,
    org_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('weekly','monthly')),
    period_start TEXT NOT NULL,
    version INTEGER NOT NULL CHECK(version >= 1),
    status TEXT NOT NULL CHECK(status IN ('running','complete','failed')),
    payload JSONB,
    error TEXT,
    requested_by TEXT,
    created_at BIGINT NOT NULL,
    completed_at BIGINT,
    UNIQUE(org_id, kind, period_start, version),
    FOREIGN KEY(org_id, requested_by) REFERENCES users(org_id, id)
);
CREATE TABLE audit_log (
    seq BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    org_id TEXT NOT NULL REFERENCES organizations(id),
    actor_type TEXT NOT NULL CHECK(actor_type IN ('system','device','user')),
    actor_id TEXT,
    action TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    detail JSONB NOT NULL,
    created_at BIGINT NOT NULL
);
CREATE TABLE api_commands (
    org_id TEXT NOT NULL,
    actor_type TEXT NOT NULL CHECK(actor_type IN ('device','user')),
    actor_id TEXT NOT NULL,
    command_ref TEXT NOT NULL,
    request_digest TEXT NOT NULL,
    response JSONB NOT NULL,
    created_at BIGINT NOT NULL,
    PRIMARY KEY(org_id, actor_type, actor_id, command_ref)
);
CREATE FUNCTION prevent_immutable_mutation() RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'immutable business record';
END;
$$ LANGUAGE plpgsql;
CREATE TABLE immutable_registry(table_name TEXT PRIMARY KEY);
INSERT INTO immutable_registry(table_name) VALUES
    ('opening_balances'),('attendance_events'),('balance_ledger'),
    ('enrollment_records'),('renewal_records'),('audit_log'),
    ('migration_receipts'),('immutable_registry');
CREATE TRIGGER immutable_registry_no_change BEFORE UPDATE OR DELETE ON immutable_registry
FOR EACH ROW EXECUTE FUNCTION prevent_immutable_mutation();
DO $$
DECLARE name TEXT;
BEGIN
    FOREACH name IN ARRAY ARRAY['opening_balances','attendance_events','balance_ledger',
        'enrollment_records','renewal_records','audit_log','migration_receipts']
    LOOP
        EXECUTE format('CREATE TRIGGER %I BEFORE UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION prevent_immutable_mutation()',
            name || '_no_change', name);
    END LOOP;
END;
$$;
INSERT INTO schema_migrations VALUES(
    'fresh-start-runtime-v2', EXTRACT(EPOCH FROM clock_timestamp())::BIGINT
);
""".strip() + "\n"


def full_schema():
    """One transaction-ready DDL script; callers control the transaction."""
    lines = POSTGRES_SCHEMA.strip().splitlines()
    if lines[0] != "BEGIN;" or lines[-1] != "COMMIT;":
        raise ValueError("Unexpected base schema wrapper")
    return "\n".join(lines[1:-1]) + "\n" + POSTGRES_RUNTIME_SCHEMA
