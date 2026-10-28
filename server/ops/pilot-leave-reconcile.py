"""Compare explicit minimal snapshots. No network, database writes, replay, or activation."""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
import os
from pathlib import Path
import re

ID = re.compile(r"[a-f0-9]{32}")
SHA = re.compile(r"[a-f0-9]{64}")
LIMIT = 2 * 1024 * 1024


def require(condition, message):
    if not condition:
        raise ValueError(message)


def identifier(value):
    return isinstance(value, str) and ID.fullmatch(value) is not None


def records(value, key):
    require(isinstance(value, list) and len(value) <= 10000, "Bounded record list required")
    require(all(isinstance(r, dict) and isinstance(r.get(key), str) for r in value), "Explicit record identifiers required")
    require(len({r[key] for r in value}) == len(value), "Duplicate record identifier")
    return value


def review(data):
    require(isinstance(data, dict) and data.get("format") == "future-school-pilot-leave-review-input" and data.get("schema_version") == 1, "Explicit review input required")
    require(identifier(data.get("class_id")) and identifier(data.get("lesson_id")), "Explicit class and lesson required")
    members = data.get("student_ids")
    require(isinstance(members, list) and 0 < len(members) <= 2000 and all(identifier(v) for v in members) and len(set(members)) == len(members), "Unique confirmed lesson members required")
    members = set(members)
    device = data.get("device_id")
    require(device is None or identifier(device), "Explicit device identity required")
    sides = {}
    for side, record_key in (("tablet", "local_ref"), ("server", "id")):
        source = data.get(side)
        require(isinstance(source, dict) and source.get("class_id") == data["class_id"] and source.get("lesson_id") == data["lesson_id"], "Snapshot class or lesson differs")
        require(isinstance(source.get("snapshot_sha256"), str) and SHA.fullmatch(source["snapshot_sha256"]), "Snapshot checksum required")
        require(source.get("identity_mapping_verified") is True, "Student mapping must be verified separately")
        attendance = source.get("arrived_student_ids")
        require(isinstance(attendance, list) and all(identifier(v) and v in members for v in attendance) and len(attendance) == len(set(attendance)), "Attendance outside scope or duplicated")
        leaves = records(source.get("leaves"), record_key)
        for row in leaves:
            require(row.get("class_id") == data["class_id"] and row.get("lesson_id") == data["lesson_id"] and row.get("student_id") in members, "Leave outside confirmed scope")
            require(row.get("status") in {"approved", "cancelled"}, "Explicit leave state required")
            if side == "server":
                require(identifier(row["id"]) and row.get("origin") in {"teacher", "device"}, "Server origin must be verified from audit")
                require(identifier(row.get("recorded_by")), "Verified server actor required")
            else:
                require(re.fullmatch(r"[0-9]{1,20}", row["local_ref"]) is not None, "Explicit local reference required")
                require(row.get("server_leave_id") is None or identifier(row["server_leave_id"]), "Invalid acknowledged server reference")
        sides[side] = {"arrived": set(attendance), "leaves": leaves, "hash": source["snapshot_sha256"]}

    local, remote = sides["tablet"], sides["server"]
    remote_by_id = {r["id"]: r for r in remote["leaves"]}
    references = [r["server_leave_id"] for r in local["leaves"] if r.get("server_leave_id")]
    require(len(set(references)) == len(references), "Two local records cannot claim one receipt")
    for row in local["leaves"]:
        ref = row.get("server_leave_id")
        if ref in remote_by_id:
            require(remote_by_id[ref]["student_id"] == row["student_id"], "Receipt belongs to another student")

    issues = []
    def issue(student, code, local_refs=(), remote_ids=()):
        issues.append({"student_id": student, "code": code, "tablet_leave_refs": sorted(local_refs), "server_leave_ids": sorted(remote_ids), "automatic_change_allowed": False})

    l_by, r_by = defaultdict(list), defaultdict(list)
    for row in local["leaves"]:l_by[row["student_id"]].append(row)
    for row in remote["leaves"]:r_by[row["student_id"]].append(row)
    for student in sorted(members):
        l_rows, r_rows = l_by[student], r_by[student]
        la = [r for r in l_rows if r["status"] == "approved"]
        ra = [r for r in r_rows if r["status"] == "approved"]
        if len(la) > 1 or len(ra) > 1:
            issue(student, "multiple_active_leaves", [r["local_ref"] for r in la], [r["id"] for r in ra])
        if (la or ra) and student in (local["arrived"] | remote["arrived"]):
            issue(student, "attendance_leave_conflict", [r["local_ref"] for r in la], [r["id"] for r in ra])
        if student in local["arrived"] - remote["arrived"]:issue(student, "tablet_attendance_not_mirrored")
        if student in remote["arrived"] - local["arrived"]:issue(student, "server_attendance_missing_tablet")
        linked = set()
        for row in l_rows:
            ref = row.get("server_leave_id")
            target = remote_by_id.get(ref)
            if target is None:
                if ref:issue(student, "acknowledged_leave_missing_server", [row["local_ref"]], [ref])
                elif row["status"] == "approved":issue(student, "tablet_leave_not_on_server", [row["local_ref"]])
                continue
            linked.add(ref)
            if target["origin"] != "device":
                issue(student, "receipt_origin_mismatch", [row["local_ref"]], [ref])
            elif device is None or target["recorded_by"] != device:
                issue(student, "device_identity_unverified" if device is None else "foreign_device_leave", [row["local_ref"]], [ref])
            if row["status"] != target["status"]:
                code = "server_cancellation_missing_tablet" if row["status"] == "approved" else "tablet_cancellation_missing_server"
                issue(student, code, [row["local_ref"]], [ref])
        for row in ra:
            if row["id"] not in linked:
                issue(student, "server_teacher_leave_missing_tablet" if row["origin"] == "teacher" else "server_device_leave_missing_tablet", remote_ids=[row["id"]])
        if la and ra and not any(r.get("server_leave_id") in {s["id"] for s in ra} for r in la):
            issue(student, "independent_active_leaves", [r["local_ref"] for r in la], [r["id"] for r in ra])

    return {"format": "future-school-pilot-leave-review", "schema_version": 1,
            "class_id": data["class_id"], "lesson_id": data["lesson_id"], "expected_students": len(members),
            "tablet_snapshot_sha256": local["hash"], "server_snapshot_sha256": remote["hash"],
            "tablet_arrived": len(local["arrived"]), "server_arrived": len(remote["arrived"]),
            "tablet_approved_leave_students": len({r["student_id"] for r in local["leaves"] if r["status"] == "approved"}),
            "server_approved_leave_students": len({r["student_id"] for r in remote["leaves"] if r["status"] == "approved"}),
            "leave_facts_match": not issues, "manual_review_required": bool(issues),
            "issue_counts": dict(sorted(Counter(r["code"] for r in issues).items())), "issues": issues,
            "contains_private_data": True, "changes_applied": False, "events_generated": False,
            "activation_permitted": False, "notice_sending_enabled": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    require(not args.input.is_symlink() and args.input.is_file() and args.input.stat().st_size <= LIMIT, "Bounded ordinary input file required")
    raw = args.input.read_bytes()
    result = review(json.loads(raw))
    result["input_sha256"] = hashlib.sha256(raw).hexdigest()
    with os.fdopen(os.open(args.output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "w", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=True, indent=2)
        stream.write("\n")
    print(json.dumps({"report_saved": True, "leave_facts_match": result["leave_facts_match"],
                      "manual_review_required": result["manual_review_required"], "changes_applied": False, "activation_permitted": False}))


if __name__ == "__main__":main()
