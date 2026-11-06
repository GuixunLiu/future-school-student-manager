"""Create an offline full-database rehearsal copy; NEVER apply or start services."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from management.initialization import rehearse


def read_bounded(path, limit):
    with path.open('rb') as stream:
        raw=stream.read(limit+1)
    if len(raw)>limit: raise ValueError('Private input exceeds rehearsal limit')
    return raw


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('source','output-dir','plan','archive','review'):
        parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--plan-sha256',required=True)
    args=parser.parse_args()
    # Paths as well as source/output are private owned Linux files, not symlinks.
    from management.initialization import export_helper
    helper=export_helper()
    for p in (args.plan,args.archive,args.review):
        helper.private_path(p.parent,True);helper.private_path(p)
    result=rehearse(args.source,args.output_dir,plan_raw=read_bounded(args.plan,8*1024*1024),plan_sha256=args.plan_sha256,
        archive_raw=read_bounded(args.archive,10*1024*1024),review_raw=read_bounded(args.review,32*1024*1024))
    print(json.dumps({k:result[k] for k in ('format','rehearsal_only','activation_authorized','source_written','events_replayed','accounts_preserved','legacy_applications_preserved','scope','history_counts','reserved_id_rows','teacher_grants_aligned','copy_sha256')},sort_keys=True))


if __name__=='__main__':
    main()
