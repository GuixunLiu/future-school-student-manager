CREATE TABLE roster_extension (
 version INTEGER PRIMARY KEY CHECK(version=1)
);
INSERT INTO roster_extension VALUES(1);
CREATE TABLE archived_students (
 id TEXT PRIMARY KEY, org_id TEXT NOT NULL REFERENCES organizations(id),
 name TEXT NOT NULL, external_ref TEXT NOT NULL, remaining_units INTEGER NOT NULL,
 previous_class_name TEXT NOT NULL, archived_at INTEGER NOT NULL,
 UNIQUE(org_id,id), UNIQUE(org_id,external_ref)
);
CREATE TABLE roster_imports (
 org_id TEXT NOT NULL REFERENCES organizations(id), bundle_sha256 TEXT NOT NULL,
 source_sha256 TEXT NOT NULL, source_date TEXT NOT NULL, imported_at INTEGER NOT NULL,
 source_database_digest TEXT NOT NULL, receipt_json TEXT NOT NULL,
 PRIMARY KEY(org_id,bundle_sha256)
);
CREATE TABLE roster_source_links (
 org_id TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('class','student')),
 source_id INTEGER NOT NULL, entity_id TEXT NOT NULL,
 bundle_sha256 TEXT NOT NULL,
 PRIMARY KEY(org_id,kind,source_id), UNIQUE(org_id,kind,entity_id),
 FOREIGN KEY(org_id,bundle_sha256) REFERENCES roster_imports(org_id,bundle_sha256)
);
CREATE TABLE class_schedule_templates (
 org_id TEXT NOT NULL, class_id TEXT NOT NULL,
 weekday INTEGER NOT NULL CHECK(weekday BETWEEN 1 AND 7),
 interval_weeks INTEGER NOT NULL CHECK(interval_weeks IN (1,2)),
 anchor_date TEXT NOT NULL, start_time TEXT NOT NULL, end_time TEXT NOT NULL,
 duration_minutes INTEGER NOT NULL CHECK(duration_minutes BETWEEN 1 AND 720),
 end_day_offset INTEGER NOT NULL CHECK(end_day_offset IN (0,1)),
 effective_date TEXT NOT NULL, teacher_label TEXT NOT NULL,
 PRIMARY KEY(org_id,class_id), FOREIGN KEY(org_id,class_id) REFERENCES classes(org_id,id)
);
CREATE TRIGGER archive_identity_insert BEFORE INSERT ON archived_students
 WHEN EXISTS(SELECT 1 FROM students WHERE id=NEW.id OR (org_id=NEW.org_id AND external_ref=NEW.external_ref))
 BEGIN SELECT RAISE(ABORT,'student identity already exists'); END;
CREATE TRIGGER archive_identity_update BEFORE UPDATE OF id,org_id,external_ref ON archived_students
 WHEN EXISTS(SELECT 1 FROM students WHERE id=NEW.id OR (org_id=NEW.org_id AND external_ref=NEW.external_ref))
 BEGIN SELECT RAISE(ABORT,'student identity already exists'); END;
CREATE TRIGGER student_archive_insert BEFORE INSERT ON students
 WHEN EXISTS(SELECT 1 FROM archived_students WHERE id=NEW.id OR (org_id=NEW.org_id AND external_ref=NEW.external_ref))
 BEGIN SELECT RAISE(ABORT,'student identity already archived'); END;
CREATE TRIGGER student_archive_update BEFORE UPDATE OF id,org_id,external_ref ON students
 WHEN EXISTS(SELECT 1 FROM archived_students WHERE id=NEW.id OR (org_id=NEW.org_id AND external_ref=NEW.external_ref))
 BEGIN SELECT RAISE(ABORT,'student identity already archived'); END;
