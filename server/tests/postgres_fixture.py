"""Use explicit loopback TCP for disposable PostgreSQL on Windows and Unix."""
import socket

from psycopg.conninfo import make_conninfo


def loopback_conninfo(server):
    """pgserver uses Unix sockets on Linux; release rehearsals require TCP."""
    info = server.get_postmaster_info()
    if info.hostname != '127.0.0.1':
        from pgserver import pg_ctl
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            port = listener.getsockname()[1]
        pg_ctl(['-w', 'restart', '-o', f'-h 127.0.0.1 -p {port}',
                '-l', str(server.log)], pgdata=server.pgdata,
               user=server.system_user, timeout=15)
        server.ensure_postgres_running()
        info = server.get_postmaster_info()
    if info.hostname != '127.0.0.1' or not info.port:
        raise RuntimeError('Disposable PostgreSQL did not start on loopback TCP')
    return make_conninfo(host='127.0.0.1', port=info.port, user=server.postgres_user)
