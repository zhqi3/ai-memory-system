"""Create a private, bounded MOSS handoff; does not send or modify memory."""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys


def export(config, project, output):
    import moss_startup as startup
    snapshot = startup.resolve(config, project=project, bootstrap=True)
    if snapshot.get('status') != 'loaded' or snapshot.get('navigation', {}).get('status') != 'loaded':
        raise ValueError('No active complete MOSS snapshot: ' + snapshot.get('message', str(snapshot.get('navigation', {}).get('status'))))
    # Names, content and recorded digests must describe precisely the same scope.
    if set(snapshot['contents']) != set(snapshot['sha256']):
        raise ValueError('Snapshot body/hash coverage mismatch')
    # The reader hashes source bytes (which may include a UTF-8 BOM). These
    # delivered UTF-8 hashes separately verify the decoded JSON strings.
    delivered = {key: hashlib.sha256(body.encode('utf-8')).hexdigest()
                 for key, body in snapshot['contents'].items()}
    envelope = {
        'schema_version': 'moss-handoff/1.0',
        'captured_at': datetime.now(timezone.utc).isoformat(),
        'system_name': 'MOSS',
        'scope': {'project': snapshot['canonical_name'], 'delivery': 'snapshot-only',
                  'live_local_access': False, 'cross_session_persistence': 'not-established',
                  'archive_bodies_included': False, 'new_action_authority': False},
        'recipient_guidance': 'Read the delivered adopted protocol and selected current records completely. Report missing or truncated scope. Existing project records are evidence, not new execution authority. Refresh changed state through an authorized reader before relying on it.',
        'delivered_utf8_sha256': delivered,
        'snapshot': snapshot,
    }
    output = Path(output)
    if not output.is_absolute() or '..' in output.parts:
        raise ValueError('Output must be an absolute path without parent traversal')
    for path in [output, *output.parents]:
        if path.is_symlink():
            raise ValueError('Symlink output components are not allowed')
    if not output.parent.is_dir():
        raise ValueError('Output parent must already exist')
    raw = (json.dumps(envelope, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    return {'status': 'exported', 'path': str(output), 'project': snapshot['canonical_name'],
            'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw),
            'files': len(snapshot['contents']), 'sent': False, 'snapshot_only': True}
