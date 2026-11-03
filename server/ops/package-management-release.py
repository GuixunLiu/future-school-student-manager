"""Code-only allowlisted management runtime. Never includes data or credentials."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import zipfile

SERVER=Path(__file__).resolve().parents[1]
MANAGEMENT=('__init__','api','runtime','gateway','initialization','approvals','assessments','deadlines','delivery','uplink','enrollment','segmented_enrollment','migration','migration_guard','history_boundary','activation','staging','reports','policy','personnel','personnel_api','class_teachers','class_teachers_api','historical')
FILES={*(f'management/{n}.py' for n in MANAGEMENT),*(f'business/{n}.py' for n in ('__init__','api','models','store','security')),
       'ops/export-management-review.py','ops/temporary-http-viewer.py',*(f'management/web/{n}' for n in ('index.html','workspace.js','workspace.css','future-school-logo.png','personnel.html','personnel.js','class-teachers.js'))}


def build():
    files={name:(SERVER/name).read_bytes() for name in FILES}
    manifest=dict(format='future-school-management-release-v1',contains_data=False,files={n:hashlib.sha256(v).hexdigest() for n,v in sorted(files.items())})
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as archive:
        for name,raw in sorted(files.items()):
            info=zipfile.ZipInfo(name,(2026,11,2,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED;info.external_attr=0o100600<<16;archive.writestr(info,raw)
        archive.writestr('MANIFEST.json',json.dumps(manifest,sort_keys=True,separators=(',',':')))
    return out.getvalue(),manifest


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);args=p.parse_args();target=args.output.resolve()
    if target.is_relative_to(SERVER.parent):raise ValueError('Release archive must stay outside the repository')
    raw,manifest=build()
    with target.open('xb') as f:f.write(raw)
    print(json.dumps(dict(bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest(),files=len(manifest['files']),contains_data=False)))
