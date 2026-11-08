"""Build a deterministic, code-only unified-runtime release candidate."""
import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import zipfile


SERVER = Path(__file__).resolve().parents[1]
FORMAT = 'future-school-unified-runtime-release-v1'
SCHEMA = 'fresh-start-runtime-v2'
ENTRYPOINT = 'management.runtime_main:app'
CODE = [
    *(f'business/{name}.py' for name in ('__init__', 'api', 'models', 'security', 'store')),
    *(f'management/{name}.py' for name in (
        '__init__', 'fresh_start', 'fresh_start_postgres', 'runtime_api', 'runtime_faces',
        'runtime_main', 'runtime_release', 'runtime_reports', 'runtime_schema', 'runtime_service')),
]
WEB = [
    'runtime.html', 'runtime.js', 'runtime.css', 'workspace.css', 'teacher-runtime.js',
    'teacher-report.js', 'teacher-face.js', 'report-history.js', 'admin-report.js', 'admin-monthly.js',
    'admin-renewal.js', 'admin-enrollment.js', 'admin-roster-sync.js', 'admin-accounts.js',
    'admin-schedule.js', 'admin-directory.js', 'admin-face.js', 'face-engine.js',
    'face-api.min.js', 'ort.wasm.min.js', 'ort-wasm-simd-threaded.mjs',
    'ort-wasm-simd-threaded.wasm', 'future-school-logo.png',
]
FACE_MODELS = [
    'tiny_face_detector_model-weights_manifest.json', 'tiny_face_detector_model-shard1',
    'face_landmark_68_tiny_model-weights_manifest.json', 'face_landmark_68_tiny_model-shard1',
    'sface_int8bq.onnx',
]
LICENSES = ['face-api.js-LICENSE.txt', 'onnxruntime-MIT.txt', 'opencv-sface-Apache-2.0.txt']
FILES = CODE + [f'management/web/{name}' for name in WEB] + [
    f'management/web/face-models/{name}' for name in FACE_MODELS] + [
    f'management/web/face-licenses/{name}' for name in LICENSES] + [
    'requirements-runtime.txt', 'ops/future-school-runtime.service.template',
    'ops/runtime-preflight.py', 'ops/render-runtime-service.py',
    'management/runtime-api-v1.yaml', 'RUNTIME-RELEASE-CHECKLIST.md',
    'ISOLATED-LAUNCH-RUNBOOK.md', 'ACCEPTANCE-REPORT.md', 'GO-LIVE-SIGNOFF.md',
]


def canonical(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode('utf-8')


def build():
    contents = {name: (SERVER / name).read_bytes() for name in FILES}
    manifest = {
        'format': FORMAT, 'schema': SCHEMA, 'entrypoint': ENTRYPOINT,
        'contains_business_data': False, 'contains_credentials': False,
        'production_ready': False, 'wechat_sending_enabled': False,
        'network_access': 'loopback_or_ssh_tunnel_only',
        'files': {name: hashlib.sha256(raw).hexdigest() for name, raw in sorted(contents.items())},
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, raw in sorted(contents.items()):
            info = zipfile.ZipInfo(name, (2026, 11, 7, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100600 << 16
            archive.writestr(info, raw)
        info = zipfile.ZipInfo('MANIFEST.json', (2026, 11, 7, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = 0o100600 << 16
        archive.writestr(info, canonical(manifest))
    return output.getvalue(), manifest


def verify(raw):
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)) or 'MANIFEST.json' not in names:
            raise ValueError('Release archive has duplicate or missing manifest entries')
        for name in names:
            path = PurePosixPath(name)
            if path.is_absolute() or '..' in path.parts or '\\' in name:
                raise ValueError('Unsafe release archive path')
        manifest = json.loads(archive.read('MANIFEST.json'))
        if manifest.get('format') != FORMAT or manifest.get('schema') != SCHEMA:
            raise ValueError('Unexpected release format or schema')
        if manifest.get('entrypoint') != ENTRYPOINT:
            raise ValueError('Unexpected ASGI entrypoint')
        if manifest.get('contains_business_data') is not False or manifest.get('contains_credentials') is not False:
            raise ValueError('Release must remain code-only')
        if manifest.get('production_ready') is not False or manifest.get('wechat_sending_enabled') is not False:
            raise ValueError('Candidate boundaries must remain explicit')
        if set(names) != {*manifest['files'], 'MANIFEST.json'}:
            raise ValueError('Release file list differs from the manifest')
        for name, expected in manifest['files'].items():
            if hashlib.sha256(archive.read(name)).hexdigest() != expected:
                raise ValueError('Release file digest differs: ' + name)
    return manifest


def write(output):
    target = Path(output).resolve()
    checksum = target.with_suffix(target.suffix + '.sha256')
    if target.exists() or target.is_symlink() or checksum.exists() or checksum.is_symlink():
        raise FileExistsError('Release output and checksum must both be new files')
    target.parent.mkdir(parents=True, exist_ok=True)
    raw, manifest = build()
    verify(raw)
    with target.open('xb') as stream:
        stream.write(raw)
    digest = hashlib.sha256(raw).hexdigest()
    with checksum.open('x', encoding='ascii') as stream:
        stream.write(digest + '  ' + target.name + '\n')
    return {'output': str(target), 'bytes': len(raw), 'sha256': digest,
            'files': len(manifest['files']), 'production_ready': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Build code-only unified runtime candidate')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(write(args.output), ensure_ascii=False, sort_keys=True))
