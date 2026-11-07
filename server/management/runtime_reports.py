"""One-shot, externally scheduled generation of completed weekly/monthly reports.

The web process never starts a background thread. A system scheduler invokes this
module repeatedly; completed periods are skipped and failed latest runs get a new
version on the next invocation.
"""
import argparse
from datetime import datetime, timedelta
import json
import os

from .runtime_service import CHINA_TZ, RuntimeStore


def due_periods(now):
    today = datetime.fromtimestamp(now, CHINA_TZ).date()
    monday = today - timedelta(days=today.weekday())
    current_month = today.replace(day=1)
    previous_month = (current_month - timedelta(days=1)).replace(day=1)
    return (('weekly', (monday - timedelta(days=7)).isoformat()),
            ('monthly', previous_month.strftime('%Y-%m')))


def run_due_reports(store, org_id=None):
    with store.connect() as connection:
        with connection.transaction():
            if org_id:
                rows = connection.execute('SELECT id FROM organizations WHERE id=%s ORDER BY id',
                                          (org_id,)).fetchall()
            else:
                rows = connection.execute('SELECT id FROM organizations ORDER BY id').fetchall()
    results = []
    for organization in rows:
        for kind, period_start in due_periods(store.clock()):
            result = store.generate_automatic_report(organization['id'], kind, period_start)
            run = result['run']
            results.append({'org_id': organization['id'], 'kind': kind,
                            'period_start': period_start, 'run_id': run['id'],
                            'version': run['version'], 'status': run['status'],
                            'skipped': result['skipped']})
    return {'format': 'future-school-report-worker-v1', 'results': results,
            'generated': sum(not item['skipped'] and item['status'] == 'complete' for item in results),
            'failed': sum(item['status'] == 'failed' for item in results),
            'skipped': sum(item['skipped'] for item in results)}


def main(argv=None):
    parser = argparse.ArgumentParser(description='Generate completed weekly and monthly report snapshots once')
    parser.add_argument('--org-id', help='Optionally restrict this invocation to one organization')
    args = parser.parse_args(argv)
    dsn = os.environ.get('RUNTIME_DATABASE_DSN', '')
    if not dsn:
        print(json.dumps({'ok': False, 'error': 'runtime_database_dsn_required'}, sort_keys=True))
        return 2
    try:
        result = run_due_reports(RuntimeStore(dsn), args.org_id)
    except Exception:
        # Never echo database errors, connection strings or SQL parameters.
        print(json.dumps({'ok': False, 'error': 'report_worker_failed'}, sort_keys=True))
        return 1
    print(json.dumps({'ok': result['failed'] == 0, **result}, ensure_ascii=True, sort_keys=True))
    return 1 if result['failed'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
