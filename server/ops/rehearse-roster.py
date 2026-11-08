"""Local-only rehearsal: reconstruct minimal reviewed server rows with FICTIONAL credentials."""

import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from business.store import Store, uid
from business_next.migration import checked_json, prepare


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("bundle", "review", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    for name in ("bundle-sha256", "review-sha256"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    args.output_dir.mkdir(mode=0o700, parents=False, exist_ok=False)
    expected = checked_json(args.review, args.review_sha256)
    source = args.output_dir / "FICTIONAL-server-before.sqlite"
    target = args.output_dir / "FICTIONAL-roster-after.sqlite"
    st = Store.bootstrap(source, "fixture-only", "fixture-owner", "虚构演练管理员", "Fictional local rehearsal credential only 2026!")
    with st.transaction() as db:
        password = db.execute("SELECT password_hash FROM users").fetchone()[0]
        db.execute("DELETE FROM roles")
        db.execute("DELETE FROM users")
        db.execute("DELETE FROM organizations")
        org, owner = expected["org_id"], uid()
        db.execute("INSERT INTO organizations VALUES(?,?,0)", (org, expected["organization"]))
        db.execute("INSERT INTO users VALUES(?,?,?,?,?,1,0)", (owner, org, "fixture-owner", "虚构演练管理员", password))
        db.execute("INSERT INTO roles VALUES(?,?,'admin')", (org, owner))
        people = set()
        for c in expected["classes"]:
            db.execute("INSERT INTO classes VALUES(?,?,?,?,0)", (c["id"], org, c["name"], c["course_name"]))
            for t in c["teachers"]:
                if t["id"] not in people:
                    db.execute("INSERT INTO users VALUES(?,?,?,?,?,1,0)", (t["id"], org, "fixture-" + t["id"], t["name"], password))
                    db.execute("INSERT INTO roles VALUES(?,?,'teacher')", (org, t["id"]))
                    people.add(t["id"])
                db.execute("INSERT INTO teacher_classes VALUES(?,?,?)", (org, t["id"], c["id"]))
        for s in expected["students"]:
            db.execute("INSERT INTO students(id,org_id,class_id,name,external_ref,remaining_units,active) VALUES(?,?,?,?,?,?,?)", (s["id"], org, s["class_id"], s["name"], s["external_ref"], s["remaining_units"], int(s["active"])))
    receipt = prepare(source, target, args.bundle, args.bundle_sha256, args.review, args.review_sha256)
    receipt.update(local_rehearsal_only=True, fictional_server_credentials=True, remote_connection_performed=False)
    with closing(Store(target).connect()) as db:
        receipt["result_classes"] = db.execute("SELECT count(*) FROM classes").fetchone()[0]
        receipt["result_active_students"] = db.execute("SELECT count(*) FROM students WHERE active=1").fetchone()[0]
        receipt["result_archived_students"] = db.execute("SELECT count(*) FROM archived_students").fetchone()[0]
    (args.output_dir / "rehearsal-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt, sort_keys=True))


if __name__ == "__main__":
    main()
