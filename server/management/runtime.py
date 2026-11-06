"""Loopback runtime for an explicitly admitted working copy. No migration."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main():
    import uvicorn
    from management.api import create_app
    from management.initialization import export_helper
    parser=argparse.ArgumentParser();parser.add_argument('--config',type=Path,required=True);args=parser.parse_args()
    helper=export_helper();helper.private_path(args.config.parent,True);helper.private_path(args.config)
    config=json.loads(args.config.read_bytes())
    if set(config)!={'database','files'} or not isinstance(config['files'],dict):raise ValueError('Explicit runtime configuration required')
    for name,checksum in config['files'].items():
        if not isinstance(name,str) or not name.startswith(('business/','management/','ops/')) or any(p in ('','.','..') for p in name.split('/')) or '\\' in name:raise ValueError('Runtime source path differs')
        target=ROOT/name
        if target.is_symlink() or hashlib.sha256(target.read_bytes()).hexdigest()!=checksum:raise ValueError('Runtime source changed')
    database=Path(config['database']);helper.private_path(database.parent,True);helper.private_path(database)
    app=create_app(database)
    uvicorn.run(app,host='127.0.0.1',port=18098,access_log=False,proxy_headers=False,server_header=False,limit_concurrency=32)


if __name__=='__main__':main()
