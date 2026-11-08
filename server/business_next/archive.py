"""Unassigned archives never become lesson members or financial activity."""

from pathlib import Path

from business.store import Problem, Store


def initialize(path):
    store = Store(path)
    db = store.connect()
    try:
        db.executescript("BEGIN IMMEDIATE;\n" + Path(__file__).with_name("schema.sql").read_text(encoding="utf-8"))
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def verify(store):
    db = store.connect()
    try:
        if [r[0] for r in db.execute("SELECT version FROM roster_extension")] != [1]:
            raise ValueError("Initialize the roster extension on a new database copy first")
        if db.execute("PRAGMA foreign_key_check").fetchone():
            raise ValueError("Database relationship check failed")
        for table in ("archived_students", "roster_imports", "roster_source_links", "class_schedule_templates"):
            db.execute("SELECT * FROM " + table + " LIMIT 0")
    finally:
        db.close()


def restore(store, db, actor, student_id, class_id, expected_units):
    store.require_user(actor, "admin")
    row = db.execute("SELECT * FROM archived_students WHERE org_id=? AND id=?", (actor["org_id"], student_id)).fetchone()
    if row is None:
        raise Problem(404, "archive_not_found", "未找到此归档，可能已恢复，请刷新核对")
    store.entity(db, actor, "classes", class_id)
    if row["remaining_units"] != expected_units:
        raise Problem(409, "archive_changed", "档案余额已变化，请刷新核对")
    # The caller owns the surrounding transaction: any failed INSERT restores the archive.
    db.execute("DELETE FROM archived_students WHERE org_id=? AND id=?", (actor["org_id"], student_id))
    db.execute("INSERT INTO students(id,org_id,class_id,name,external_ref,remaining_units,active) VALUES(?,?,?,?,?,?,1)",
               (row["id"], actor["org_id"], class_id, row["name"], row["external_ref"], row["remaining_units"]))
    store.audit(db, actor, "archive_restored", student_id, class_id=class_id)
    return {"id": student_id, "class_id": class_id, "remaining_units": expected_units,
            "new_or_renewal_recorded": False, "notification_created": False}
