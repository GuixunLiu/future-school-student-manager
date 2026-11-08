"""Renew the current tablet grant from the server terminal."""
import argparse
from getpass import getpass
import json
from pathlib import Path

from business.store import Problem
from .delivery import DeliveryStore
from .initialization import export_helper


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--organization', required=True)
    parser.add_argument('--login', required=True)
    parser.add_argument('--activation', required=True)
    parser.add_argument('--expected-expiry', type=int, required=True)
    parser.add_argument('--expires-at', type=int, required=True)
    parser.add_argument('--reason', required=True)
    parser.add_argument('--command-ref', required=True)
    args = parser.parse_args()
    helper = export_helper()
    helper.private_path(args.database.parent, True)
    helper.private_path(args.database)
    store = DeliveryStore(args.database)
    token = None
    try:
        token = store.login(args.organization, args.login, getpass('管理员网页登录密码: '), 'local-writer-renewal')['access_token']
        result = store.renew_active_writer(
            token, activation_id=args.activation, expected_expiry=args.expected_expiry,
            expires_at=args.expires_at, reason=args.reason, command_ref=args.command_ref,
        )
        print(json.dumps(result))
    except Problem as error:
        parser.exit(1, error.message + '\n')
    finally:
        if token:
            store.command(token, lambda db, actor: db.execute(
                'DELETE FROM sessions WHERE org_id=? AND digest=?',
                (actor['org_id'], actor['session_digest'])))


if __name__ == '__main__':
    main()
