"""Generate a separate read-only HTTPS viewer draft. Never installs, reloads or obtains certificates."""
import argparse
import ipaddress
from pathlib import PurePosixPath, Path
import re
from urllib.parse import urlsplit


def origin_parts(origin):
    p = urlsplit(origin)
    if p.scheme != "https" or p.username or p.password or p.path or p.query or p.fragment or not p.hostname:
        raise ValueError("Use an explicit HTTPS origin without path or credentials")
    host = p.hostname
    try:
        address = ipaddress.ip_address(host)
        if address.version != 4 or not address.is_global:
            raise ValueError("Use a public IPv4 or DNS name")
    except ValueError:
        if not re.fullmatch(r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{1,62}", host):
            raise ValueError("Use a public IPv4 or DNS name")
    port = p.port or 443
    if not 1024 <= port <= 65535 and port != 443:
        raise ValueError("Unsupported HTTPS listener port")
    if origin != "https://" + host + (":" + str(port) if p.port else ""):
        raise ValueError("Use canonical lowercase HTTPS origin")
    return host, port


def certificate_path(value):
    p = PurePosixPath(value)
    if not p.is_absolute() or ".." in p.parts or not re.fullmatch(r"/[A-Za-z0-9_./-]+", value):
        raise ValueError("Use explicit Linux certificate paths without substitutions")
    return value


def render(origin, certificate, key):
    host, port = origin_parts(origin)
    certificate, key = certificate_path(certificate), certificate_path(key)
    if certificate == key:
        raise ValueError("Certificate and private key paths must differ")
    def proxy():
        return """        proxy_pass http://127.0.0.1:18082;
        proxy_http_version 1.1;
        proxy_set_header Host "127.0.0.1:18082";
        proxy_set_header Origin "";
        proxy_set_header Forwarded "";
        proxy_set_header X-Forwarded-For "";
        proxy_set_header X-Forwarded-Host "";
        proxy_set_header X-Forwarded-Proto "";
        proxy_set_header Connection "";
        proxy_connect_timeout 3s;
        proxy_read_timeout 30s;
        proxy_cache off;
"""
    # Generated only for admin review. The exact external Origin is validated BEFORE clearing it for LocalGuard.
    return f"""# DRAFT ONLY: verify certificate SAN, renewal, server_name conflicts and nginx -t before installation.
# Include at nginx http context; do not replace existing nginx.conf or other sites.
limit_req_zone $binary_remote_addr zone=wxnotify_viewer_login:1m rate=10r/m;
server {{
    listen {port} ssl;
    server_name {host};
    ssl_certificate {certificate};
    ssl_certificate_key {key};
    ssl_protocols TLSv1.2 TLSv1.3;
    client_max_body_size 16k;
    client_body_timeout 10s;
    access_log off;
    if ($host != "{host}") {{ return 444; }}
    if ($http_origin != "") {{ set $wxnotify_viewer_origin $http_origin; }}
    if ($http_origin = "") {{ set $wxnotify_viewer_origin "{origin}"; }}
    if ($wxnotify_viewer_origin != "{origin}") {{ return 403; }}
    location = /v1/auth/login {{
        limit_except POST {{ deny all; }}
        limit_req zone=wxnotify_viewer_login burst=10 nodelay;
        limit_req_status 429;
{proxy()}    }}
    location = /v1/auth/logout {{
        limit_except POST {{ deny all; }}
{proxy()}    }}
    location / {{
        limit_except GET {{ deny all; }}
{proxy()}    }}
}}
"""


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("origin", "certificate", "private-key", "output"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    payload = render(args.origin, args.certificate, args.private_key)
    with Path(args.output).open("x", encoding="utf-8", newline="\n") as f:
        f.write(payload)
    print("Draft created only; no certificate requested or server configuration changed.")
