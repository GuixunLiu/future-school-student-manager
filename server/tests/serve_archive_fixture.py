"""Dedicated fictional archive browser fixture; never contacts a real service."""

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from business.store import Store, uid
from business_next.archive import initialize
from business_next.api import create_app
import uvicorn

parser = argparse.ArgumentParser()
parser.add_argument("--database", type=Path, required=True)
parser.add_argument("--port", type=int, required=True)
args = parser.parse_args()
password = "Fictional archive browser credential 2026!"
st = Store.bootstrap(args.database, "school-ui", "owner-ui", "演示管理员", password)
initialize(args.database)
with st.transaction() as db:
    actor = {"kind": "user", "id": db.execute("SELECT id FROM users").fetchone()[0], "org_id": db.execute("SELECT id FROM organizations").fetchone()[0], "roles": {"admin"}}
    st.create_person(db, actor, {"login": "teacher-ui", "display_name": "演示老师", "password": password, "roles": ["teacher"]})
    db.execute("INSERT INTO classes VALUES(?,?,?,?,0)", (uid(), actor["org_id"], "演示周六下午班", "信息学奥赛"))
    for ref, name, old, units in (("DEMO-001", "演示同名学生", "", 2300), ("DEMO-002", "演示同名学生", "原周日下午班", 0), ("DEMO-003", "<img src=x onerror=alert(1)>", "旧班 <script>", 750)):
        db.execute("INSERT INTO archived_students VALUES(?,?,?,?,?,?,0)", (uid(), actor["org_id"], name, ref, units, old))
uvicorn.run(create_app(args.database), host="127.0.0.1", port=args.port, access_log=False, proxy_headers=False, server_header=False, ws="none")
