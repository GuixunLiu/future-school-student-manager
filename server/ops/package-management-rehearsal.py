"""Package explicitly listed rehearsal code only. No data or runtime environment."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import zipfile


SERVER=Path(__file__).resolve().parents[1]
MODULES=('__init__','initialization','approvals','assessments','deadlines','delivery',
         'uplink','enrollment','segmented_enrollment','migration','migration_guard','history_boundary','activation','staging','reports','policy')


def build():
    files={f'server/management/{name}.py':(SERVER/'management'/f'{name}.py').read_bytes() for name in MODULES}
    for name in ('__init__','store','security'):
        files[f'server/business/{name}.py']=(SERVER/'business'/f'{name}.py').read_bytes()
    for name in ('export-management-review.py','rehearse-management-initialization.py','export-history-boundary.py'):
        files[f'server/ops/{name}']=(SERVER/'ops'/name).read_bytes()
    files['README.md']=(SERVER.parent/'docs/management-initialization-rehearsal.md').read_bytes()
    manifest=dict(format='future-school-rehearsal-code-v1',data_included=False,service_installer=False,
                  files={name:hashlib.sha256(raw).hexdigest() for name,raw in files.items()})
    files['MANIFEST.json']=(json.dumps(manifest,sort_keys=True,indent=2)+'\n').encode()
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as archive:
        for name,raw in sorted(files.items()):
            info=zipfile.ZipInfo(name,(2026,11,2,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED
            info.external_attr=0o100600<<16
            archive.writestr(info,raw)
    return out.getvalue(),manifest


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    target=args.output.resolve();repo=SERVER.parent.resolve()
    if target.is_relative_to(repo):raise ValueError('Package output must stay outside the repository')
    raw,manifest=build()
    with target.open('xb') as stream:stream.write(raw)
    print(json.dumps(dict(bytes=len(raw),sha256=hashlib.sha256(raw).hexdigest(),files=len(manifest['files']),
                         data_included=False,service_installer=False),sort_keys=True))


if __name__=='__main__':main()
