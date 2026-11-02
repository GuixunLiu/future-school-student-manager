"""Read one sealed rehearsal boundary. Never changes a database or activates it."""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from management.initialization import export_helper
from management.history_boundary import load


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',type=Path,required=True)
    parser.add_argument('--organization',required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();helper=export_helper()
    helper.private_path(args.database.parent,True);helper.private_path(args.database)
    helper.private_path(args.output.parent,True)
    if not args.output.is_absolute() or args.output.is_symlink() or any((p/'.git').exists() or p.is_symlink() for p in args.output.parents):raise ValueError('Private ordinary output outside Git required')
    with closing(sqlite3.connect(args.database.resolve().as_uri()+'?mode=ro',uri=True,timeout=5)) as db:
        db.row_factory=sqlite3.Row;db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
        org=db.execute('SELECT id FROM organizations WHERE slug=?',(args.organization,)).fetchone()
        if not org:raise ValueError('Unknown organization')
        result=load(db,org['id'])
        if result is None:raise ValueError('No sealed initialization exists')
    fd=os.open(args.output,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    with os.fdopen(fd,'w',encoding='utf-8') as stream:json.dump(result,stream,sort_keys=True,separators=(',',':'))
    print(json.dumps(dict(boundary_sha256=result['boundary_sha256'],rehearsal_only=True,activation_authorized=False,database_written=False)))


if __name__=='__main__':main()
