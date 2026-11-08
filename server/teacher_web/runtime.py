import hashlib
import json
from pathlib import Path
import sys

WORK=Path(__file__).resolve().parent.parent
RELEASE='/opt/wxnotify/app/roster-review-v0.5.0-fb7c43a1-20261027065406-ff172ce2/release'


def main():
    cfg=json.loads((WORK/'web.private.json').read_bytes())
    for name,expected in cfg['files'].items():
        p=WORK/name
        if p.is_symlink() or hashlib.sha256(p.read_bytes()).hexdigest()!=expected:raise ValueError('Pinned web runtime changed')
    sys.path.insert(0,RELEASE);sys.path.insert(0,str(WORK))
    from teacher_web.api import create_app
    import uvicorn
    uvicorn.run(create_app(WORK/'business.sqlite'),host='127.0.0.1',port=18086,access_log=False,proxy_headers=False,server_header=False,ws='none',limit_concurrency=24,timeout_keep_alive=5)


if __name__=='__main__':main()
