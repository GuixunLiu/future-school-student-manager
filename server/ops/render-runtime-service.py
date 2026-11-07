"""Render a local-only loopback systemd candidate without accepting secrets."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re


TEMPLATE = Path(__file__).with_name('future-school-runtime.service.template')
SAFE_LINUX_PATH = re.compile(r'/[A-Za-z0-9._/-]+')
PLACEHOLDER = re.compile(r'@[A-Z][A-Z0-9_]*@')


def linux_path(value, label):
    if not isinstance(value, str) or len(value) > 300 or not SAFE_LINUX_PATH.fullmatch(value):
        raise ValueError(label + ' must be a simple absolute Linux path')
    path = PurePosixPath(value)
    if not path.is_absolute() or value == '/' or any(part in {'..', '.'} for part in value.split('/')):
        raise ValueError(label + ' contains an unsafe path')
    return value


def render(runtime_root, python, environment_file, port):
    values = {
        '@RUNTIME_ROOT@': linux_path(runtime_root, 'runtime root'),
        '@PYTHON@': linux_path(python, 'Python'),
        '@ENVIRONMENT_FILE@': linux_path(environment_file, 'environment file'),
    }
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        raise ValueError('port must be an integer from 1 to 65535')
    values['@PORT@'] = str(port)
    text = TEMPLATE.read_text(encoding='utf-8')
    for placeholder, value in values.items():
        if text.count(placeholder) != 1:
            raise ValueError('unexpected template placeholder count: ' + placeholder)
        text = text.replace(placeholder, value)
    if PLACEHOLDER.search(text):
        raise ValueError('unresolved template placeholder')
    if '--host 127.0.0.1' not in text or 'EnvironmentFile=' + environment_file not in text:
        raise ValueError('rendered unit failed its loopback or environment boundary')
    return text.encode('utf-8')


def write(raw, output):
    target = Path(output)
    if target.suffix != '.service':
        raise ValueError('output must use the .service suffix')
    if target.exists() or target.is_symlink():
        raise FileExistsError('rendered unit must be a new file')
    if not target.parent.is_dir() or target.parent.is_symlink():
        raise ValueError('output parent must be an existing real directory')
    created = False
    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        created = True
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(raw)
    except Exception:
        if created:
            target.unlink()
        raise
    return {
        'output': str(target.resolve()),
        'bytes': len(raw),
        'sha256': hashlib.sha256(raw).hexdigest(),
        'local_output_only': True,
        'installed': False,
        'started': False,
    }


def main():
    parser = argparse.ArgumentParser(description='Render a new local-only loopback systemd candidate')
    parser.add_argument('--runtime-root', required=True)
    parser.add_argument('--python', required=True)
    parser.add_argument('--environment-file', required=True)
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        raw = render(args.runtime_root, args.python, args.environment_file, args.port)
        receipt = write(raw, args.output)
    except (ValueError, OSError) as error:
        parser.error(str(error))
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))


if __name__ == '__main__':
    main()
