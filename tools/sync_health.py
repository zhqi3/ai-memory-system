"""Deterministic sync triage from metadata only; never invokes a model."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile

import context_library as lib
import conversation_catalog as catalog

SCHEMA = 'ai-context-sync-health/1.0'
TRANSIENT = {'FileNotFoundError', 'PermissionError', 'TimeoutError', 'ConnectionError', 'OSError'}
SUCCESS = {'imported', 'deduplicated', 'unchanged-file', 'unchanged-visible-content'}


def build_report(receipts, settings, sync_state):
    """Replay immutable run metadata so duplicate checks cannot inflate retries."""
    enabled = {s['conversation_id'] for s in settings['sources'] if s['enabled']}
    registered = {s['conversation_id'] for s in settings['sources']}
    incidents, failures, seen = {}, {}, set()
    latest_files = None

    def put(kind, identity, evidence, **fields):
        key = lib.digest(lib.canonical_bytes([kind, identity]))
        old = incidents.get(key, {})
        incidents[key] = {'id': key, 'kind': kind, 'conversation_id': identity,
                          'first_receipt': old.get('first_receipt', evidence),
                          'last_receipt': evidence, **fields}

    def clear(kind, identity):
        incidents.pop(lib.digest(lib.canonical_bytes([kind, identity])), None)

    for name, run in receipts:
        if name in seen:
            continue
        seen.add(name)
        if run.get('scope') == 'files' or 'newly_registered' in run:
            latest_files = {'receipt': name, 'finished_at': run.get('finished_at'),
                            'status': run.get('status')}
        if run.get('status') in {'error', 'errors'} and not run.get('sources'):
            scope = run.get('scope', 'unknown')
            put('run-failure', scope, name, action='diagnose', severity='attention',
                error_type=run.get('error_type', 'UnknownError'))
        elif run.get('status') == 'ok':
            scope = run.get('scope', 'files' if 'newly_registered' in run else 'api')
            clear('run-failure', scope)
        for source in run.get('sources', []):
            identity = source.get('conversation_id')
            if identity not in enabled:
                continue
            status = source.get('status')
            if status == 'error':
                count = failures.get(identity, 0) + 1
                failures[identity] = count
                error = source.get('error_type', 'UnknownError')
                retry = error in TRANSIENT and count < 2
                put('source-failure', identity, name, error_type=error,
                    consecutive_failures=count, action='retry-next-run' if retry else 'diagnose',
                    severity='retry' if retry else 'attention')
            elif status in SUCCESS:
                failures.pop(identity, None)
                clear('source-failure', identity)
            else:
                put('source-failure', identity, name, action='diagnose',
                    severity='attention', error_type='UnknownResultStatus')
            selection = source.get('selection')
            if selection == 'explicit-version-required':
                put('version-conflict', identity, name, revision=source.get('revision'),
                    action='review-versions', severity='attention')
            elif selection == 'verified-coverage':
                clear('version-conflict', identity)

    for identity, candidate in sync_state.get('candidates', {}).items():
        if identity not in registered and candidate.get('status') == 'needs-project-association':
            put('project-association', identity, None, action='ask-user-project', severity='attention')
    active = sorted(incidents.values(), key=lambda x: (x['kind'], x['conversation_id']))
    if not settings.get('enabled'):
        status = 'paused'
    else:
        status = 'attention' if any(i['severity'] == 'attention' for i in active) else 'retry' if active else 'ok'
    return {'schema_version': SCHEMA, 'status': status, 'incidents': active,
            'receipt_count': len(seen), 'latest_file_run': latest_files,
            'model_calls': 0, 'scope': 'Run receipts and registration metadata only; no conversation bodies.'}


def atomic_json(path, value):
    lib.checked_path(path, must_exist=False)
    if path.exists():
        lib.check_private_file(path)
    fd, temp = tempfile.mkstemp(prefix='.health-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(lib.canonical_bytes(value) + b'\n')
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp, path)
        lib.fsync_directory(path.parent)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def refresh(root):
    """Caller holds sync/run.lock; sources and archive revisions are never read."""
    catalog.validate_root(root)
    folder = catalog.ensure_path(root, 'sync', 'health')
    lib.check_private_file(root / 'sync-config.json')
    settings = lib.read_json(root / 'sync-config.json')
    state_path = root / 'sync/state.json'
    if state_path.exists():
        lib.check_private_file(state_path)
    sync_state = lib.read_json(state_path) if state_path.exists() else {}
    runs = catalog.ensure_path(root, 'sync', 'runs')
    receipts, invalid = [], []
    for path in sorted(runs.glob('*.json')):
        try:
            lib.check_private_file(path)
            run = lib.read_json(path)
            if not isinstance(run, dict) or not isinstance(run.get('sources', []), list):
                raise ValueError('Invalid receipt')
            if not all(isinstance(s, dict) and isinstance(s.get('conversation_id'), str)
                       and isinstance(s.get('status'), str)
                       and isinstance(s.get('error_type', ''), str)
                       and isinstance(s.get('selection', ''), str)
                       for s in run.get('sources', [])):
                raise ValueError('Invalid receipt source')
            if not isinstance(run.get('scope', ''), str) or not isinstance(run.get('status'), str):
                raise ValueError('Invalid receipt status')
            if run.get('finished_at') is not None and not isinstance(run['finished_at'], str):
                raise ValueError('Invalid receipt timestamp')
            receipts.append((path.name, run))
        except (OSError, ValueError, lib.LibraryError):
            invalid.append(path.name)
    # Filenames have second precision plus a random suffix. Replaying by name
    # can reorder two runs within one second and invent another error episode.
    receipts.sort(key=lambda entry: (entry[1].get('finished_at') or entry[0], entry[0]))
    report = build_report(receipts, settings, sync_state)
    for name in invalid:
        report['incidents'].append({'id': lib.digest(('invalid-receipt:' + name).encode()),
            'kind': 'invalid-receipt', 'conversation_id': None, 'last_receipt': name,
            'action': 'diagnose', 'severity': 'attention'})
    if invalid and settings.get('enabled'):
        report['status'] = 'attention'
    path = folder / 'pending.json'
    if path.exists():
        lib.check_private_file(path)
    previous = lib.read_json(path) if path.exists() else {}
    old = {i['id']: i for i in previous.get('incidents', [])}
    # Only a new incident or escalation is a new alert. Repeated failures retain
    # one entry even when the count and evidence filename change.
    report['new_attention_ids'] = [i['id'] for i in report['incidents']
        if i['severity'] == 'attention' and old.get(i['id'], {}).get('severity') != 'attention']
    report['updated_at'] = datetime.now(timezone.utc).isoformat()
    atomic_json(path, report)
    return {'status': report['status'], 'pending_count': len(report['incidents']),
            'new_attention_count': len(report['new_attention_ids']), 'report': str(path), 'model_calls': 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    from auto_sync import locked
    try:
        with locked(args.root):
            result = refresh(args.root)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except BlockingIOError:
        print(json.dumps({'status': 'already-running'}))
        return 0
    except Exception as exc:
        print(json.dumps({'status': 'error', 'error_type': type(exc).__name__}))
        return 1


if __name__ == '__main__':
    sys.exit(main())
