"""Bounded, explicit-source archive refresh; scheduling belongs to the caller.

Only metadata and receipts are printed. Source text stays in the archive files.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import sys
import tempfile
import uuid

import adapters
import context_library as lib
import conversation_catalog as catalog
from source_verification import verify_source, SourceReplayError

MARKER = 'AI_CONTEXT_AUTO_SYNC_V1'
CAPTURE_POLICY = f'adapter-{adapters.ADAPTER_VERSION}/redaction-{lib.REDACTION_POLICY_VERSION}'


def now():
    return datetime.now(timezone.utc).isoformat()


def key_for(provider, conversation):
    return lib.digest((provider + '\x00' + conversation).encode('utf-8'))


def atomic_json(path, value):
    lib.checked_path(path, must_exist=False)
    if path.exists():
        lib.check_private_file(path)
    fd, temporary = tempfile.mkstemp(prefix='.sync-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(lib.canonical_bytes(value) + b'\n')
            out.flush()
            os.fsync(out.fileno())
        os.replace(temporary, path)
        lib.fsync_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def locked(root):
    catalog.validate_root(root)
    folder = catalog.ensure_path(root, 'sync')
    path = folder / 'run.lock'
    lib.checked_path(path, must_exist=False)
    fd = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, 'O_NOFOLLOW', 0), 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield folder
    finally:
        os.close(fd)


def config(root):
    path = root / 'sync-config.json'
    lib.check_private_file(path)
    value = lib.read_json(path)
    if value.get('schema_version') != 'ai-context-auto-sync/1.0' or not isinstance(value.get('sources'), list):
        raise lib.LibraryError('Unsupported automatic sync configuration.')
    seen = set()
    for source in value['sources']:
        if source.get('kind') not in {'codex', 'read_thread', 'transcript'}:
            raise lib.LibraryError('Unsupported registered source kind.')
        provider, identity = source.get('provider'), source.get('conversation_id')
        if not isinstance(provider, str) or not lib.PROVIDER.fullmatch(provider) or not isinstance(identity, str) or not identity:
            raise lib.LibraryError('Invalid registered source identity.')
        key = key_for(provider, identity)
        if key in seen:
            raise lib.LibraryError('Duplicate registered source.')
        seen.add(key)
        if source['kind'] == 'codex' and provider != 'codex' or source['kind'] == 'read_thread' and provider != 'chatgpt':
            raise lib.LibraryError('Source kind and provider disagree.')
        if not isinstance(source.get('enabled'), bool) or not isinstance(source.get('title'), str):
            raise lib.LibraryError('Registered source needs enabled and title fields.')
        if not isinstance(source.get('projects'), list) or not source['projects']:
            raise lib.LibraryError('Automatic sources require explicit project association.')
        for project in source['projects']:
            lib.resolve_project(root, project)
        if source['kind'] != 'read_thread':
            path = Path(source.get('path', ''))
            if not path.is_absolute() or '..' in path.parts:
                raise lib.LibraryError('Automatic file source needs an absolute path.')
    return value


def read_state(folder):
    path = folder / 'state.json'
    if not path.exists():
        return {'sources': {}, 'candidates': {}}
    lib.check_private_file(path)
    value = lib.read_json(path)
    if not isinstance(value.get('sources'), dict) or not isinstance(value.get('candidates'), dict):
        raise lib.LibraryError('Invalid sync state; no checkpoints were advanced.')
    return value


def stat_signature(path):
    lib.regular_file(path)
    info = path.stat()
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]


def user_text(row):
    payload = row.get('payload', {})
    if not isinstance(payload, dict):
        return None
    if row.get('type') == 'response_item' and payload.get('type') == 'message' and payload.get('role') == 'user':
        content = payload.get('content')
    elif row.get('type') == 'event_msg' and payload.get('type') == 'item_completed' and isinstance(payload.get('item'), dict) and payload['item'].get('type') == 'UserMessage':
        content = payload['item'].get('content')
    elif row.get('type') == 'event_msg' and payload.get('type') == 'user_message':
        content = payload.get('message')
    else:
        return None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return '\n'.join(part.get('text', '') for part in content if isinstance(part, dict) and isinstance(part.get('text'), str))
    return None


def before_automation_tail(data):
    """Keep all history through the last human turn, before its next heartbeat.

    Only a user message beginning with our exact scheduled prompt marker counts.
    Never inspect quoted tool output or assistant prose for the marker.
    """
    offset, cutoff = 0, None
    for line in data.splitlines(keepends=True):
        if not line.endswith(b'\n'):
            break
        row = json.loads(line)
        text = user_text(row)
        if text is not None:
            if text.strip().startswith(MARKER):
                if cutoff is None:
                    cutoff = offset
            else:
                cutoff = None
        offset += len(line)
    return data if cutoff is None else data[:cutoff]


def file_payload(source, settings):
    path = Path(source['path'])
    if source['kind'] == 'codex':
        if source['conversation_id'] != settings.get('heartbeat_conversation_id'):
            return adapters.parse_codex(path)
        data, provenance = adapters._snapshot(path)
        bounded = before_automation_tail(data)
        provenance.update(snapshot_bytes=len(bounded), snapshot_sha256=lib.digest(bounded))
        payload = adapters._parse_codex_snapshot(bounded, provenance)
        payload['completeness']['automatic_capture_boundary'] = 'Before the first marked maintenance prompt after the latest non-maintenance user message; original source is unchanged.'
        return payload
    return adapters.parse_transcript(path, source['provider'], source['conversation_id'], source['title'])


def semantic_digest(manifest, records):
    # A newer file mtime, pagination cursor, or extra private runtime event is
    # not new visible context. Keep all public record extension fields.
    metadata = dict(manifest.get('conversation_metadata', {}))
    metadata.pop('page_metadata', None)
    assessment = manifest.get('completeness', {}).get('source_assessment', {})
    evidence = {name: assessment.get(name) for name in
                ('unknown_types', 'truncation_marker_count', 'attachments', 'source_evidence_gaps')}
    def without_refs(value):
        if isinstance(value, dict):
            return {k: without_refs(v) for k, v in value.items() if k != 'source_refs'}
        if isinstance(value, list):
            return [without_refs(v) for v in value]
        return value
    return lib.digest(lib.canonical_bytes({
        'provider': manifest['provider'], 'conversation_id': manifest['conversation_id'],
        'title': manifest.get('title'), 'metadata': metadata,
        'source_declared_completeness': manifest.get('source_declared_completeness'),
        'records': [{k: v for k, v in r.items() if k != 'source_refs'} for r in records],
        'evidence': without_refs(evidence)}))


def capture(root, source, payload, state, folder):
    if (payload.get('provider'), payload.get('conversation_id')) != (source['provider'], source['conversation_id']):
        raise lib.LibraryError('Source identity changed; the registered conversation was not updated.')
    manifest, records = lib.prepare_payload(payload, None, source['title'])
    key = manifest['conversation_key']
    fingerprint = semantic_digest(manifest, records)
    checkpoint = state['sources'].get(key, {})
    existing = catalog.revisions_for_key(root, key) if key in catalog.conversation_keys(root) else []
    matches = [pair for pair in existing if pair[1]['revision'] == checkpoint.get('revision')]
    if not matches:
        matches = [pair for pair in existing if pair[1].get('schema_version') == '2.0']
    for path, old in matches:
        verified, previous = lib.validate_revision(path)
        if verified.get('redaction', {}).get('policy_version') != lib.REDACTION_POLICY_VERSION:
            continue
        if semantic_digest(verified, previous) == fingerprint:
            try:
                ensure_source_verified(root, path, old['revision'])
            except SourceReplayError:
                # Preserve intact legacy snapshots, but do not let a broken
                # replay prevent a fresh authorized source from being captured.
                # Integrity failures above and invalid saved receipts still fail.
                continue
            for project in source['projects']:
                catalog.link_project(root, project, key)
            state['sources'][key] = {**checkpoint, 'revision': old['revision'], 'semantic_sha256': fingerprint,
                                     'checked_at': now(), 'capture_policy': CAPTURE_POLICY}
            return {'status': 'unchanged-visible-content', 'conversation_id': source['conversation_id'], 'record_count': len(records), 'revision': old['revision']}
    result = lib.publish(root, source['projects'][0], payload, source['title'])
    for project in source['projects'][1:]:
        catalog.link_project(root, project, key)
    ensure_source_verified(root, Path(result['path']), result['revision'])
    state['sources'][key] = {'revision': result['revision'], 'semantic_sha256': fingerprint,
                             'checked_at': now(), 'capture_policy': CAPTURE_POLICY}
    from revision_selection import select_revision, SelectionError
    try:
        select_revision(catalog.revisions_for_key(root, key))
        selection = 'verified-coverage'
    except SelectionError:
        selection = 'explicit-version-required'
    return {'status': result['status'], 'conversation_id': source['conversation_id'], 'record_count': len(records), 'revision': result['revision'], 'source_verified': True, 'selection': selection}


def ensure_source_verified(root, path, revision):
    reports = catalog.ensure_path(root, 'sync', 'source-reports')
    saved = reports / (revision + '.json')
    if saved.exists():
        lib.check_private_file(saved)
        previous = lib.read_json(saved)
        if previous.get('revision') == revision and previous.get('status') == 'verified-source-replay':
            return
        raise lib.LibraryError('Invalid saved source verification receipt.')
    report = verify_source(path)
    if report['status'] != 'verified-source-replay':
        raise SourceReplayError('Revision did not pass source replay.')
    catalog.immutable_json(saved, report)


def primary_metadata(path):
    lib.regular_file(path)
    with path.open('rb') as stream:
        first = stream.readline(4 * 1024 * 1024 + 1)
    if len(first) > 4 * 1024 * 1024 or not first.endswith(b'\n'):
        return None
    row = json.loads(first)
    payload = row.get('payload', {})
    if row.get('type') != 'session_meta' or not isinstance(payload, dict):
        return None
    source = payload.get('source')
    # Known interactive source types only; agent descendants stay out.
    if source not in ('vscode', 'cli', 'exec') or not isinstance(payload.get('id'), str):
        return None
    return {'conversation_id': payload['id'], 'cwd': payload.get('cwd'), 'path': str(path), 'source': source}


def discover(root, settings, state):
    known = {(s['provider'], s['conversation_id']) for s in settings['sources']}
    candidates, added = [], []
    for directory in settings.get('codex_discovery_roots', []):
        base = Path(directory)
        if not base.exists():
            continue
        lib.checked_path(base)
        for path in sorted(base.rglob('rollout-*.jsonl')):
            try:
                item = primary_metadata(path)
            except (OSError, ValueError, lib.LibraryError):
                continue
            if not item or ('codex', item['conversation_id']) in known:
                continue
            cwd = item.get('cwd')
            matches = []
            if isinstance(cwd, str) and Path(cwd).is_absolute() and '..' not in Path(cwd).parts:
                for route in settings.get('workspace_routes', []):
                    if Path(cwd).is_relative_to(Path(route['path'])):
                        matches.append(route)
            if matches:
                longest = max(len(Path(route['path']).parts) for route in matches)
                projects = sorted({r['project'] for r in matches if len(Path(r['path']).parts) == longest})
            else:
                projects = []
            if len(projects) == 1:
                lib.resolve_project(root, projects[0])
                source = {'kind': 'codex', 'provider': 'codex', 'conversation_id': item['conversation_id'],
                          'path': item['path'], 'title': 'Codex ' + item['conversation_id'],
                          'projects': projects, 'enabled': True, 'assignment_basis': 'Unique longest configured project workspace match.', 'discovered_at': now()}
                settings['sources'].append(source)
                known.add(('codex', item['conversation_id']))
                added.append({'conversation_id': item['conversation_id'], 'project': projects[0]})
            elif item['conversation_id'] not in state['candidates']:
                state['candidates'][item['conversation_id']] = {**item, 'status': 'needs-project-association', 'first_seen_at': now()}
                candidates.append(item)
    return added, candidates


def receipt(folder, value):
    value['finished_at'] = now()
    runs = catalog.ensure_path(folder.parent, 'sync', 'runs')
    path = runs / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + uuid.uuid4().hex[:8] + '.json')
    lib.write_private(path, lib.canonical_bytes(value) + b'\n')
    return {**value, 'receipt': str(path)}


def sync_files(root, force=False):
    with locked(root) as folder:
        settings, state = config(root), read_state(folder)
        if not settings.get('enabled'):
            return {'status': 'paused'}
        added, candidates = discover(root, settings, state)
        if added:
            atomic_json(root / 'sync-config.json', settings)
        results = []
        for source in settings['sources']:
            if not source['enabled'] or source['kind'] == 'read_thread':
                continue
            key = key_for(source['provider'], source['conversation_id'])
            try:
                path = Path(source['path'])
                if not path.exists() and source['kind'] == 'codex':
                    found = []
                    for directory in settings.get('codex_discovery_roots', []):
                        found.extend(Path(directory).rglob('*-' + source['conversation_id'] + '.jsonl'))
                    if len(found) == 1:
                        source['path'] = str(found[0]); path = found[0]
                        atomic_json(root / 'sync-config.json', settings)
                stamp = stat_signature(path)
                checkpoint = state['sources'].get(key, {})
                if (not force and checkpoint.get('file_signature') == stamp
                        and checkpoint.get('capture_policy') == CAPTURE_POLICY):
                    for project in source['projects']:
                        catalog.link_project(root, project, key)
                    results.append({'status': 'unchanged-file', 'conversation_id': source['conversation_id']})
                    continue
                payload = file_payload(source, settings)
                result = capture(root, source, payload, state, folder)
                # A writer may have appended after the capture boundary. In
                # that case never claim the new stat belongs to this snapshot.
                if stat_signature(path) == stamp:
                    state['sources'][key]['file_signature'] = stamp
                results.append(result)
            except Exception as exc:
                results.append({'status': 'error', 'conversation_id': source['conversation_id'],
                                'error_type': type(exc).__name__, 'detail': 'Source was not checkpointed; retained snapshots are unchanged. Retry after the source is available and valid.'})
            atomic_json(folder / 'state.json', state)
        atomic_json(folder / 'state.json', state)
        return receipt(folder, {'status': 'errors' if any(r['status'] == 'error' for r in results) else 'ok',
                                'sources': results, 'newly_registered': added, 'new_candidates': candidates})


def import_api(root, conversation, path):
    with locked(root) as folder:
        settings, state = config(root), read_state(folder)
        matches = [s for s in settings['sources'] if s['kind'] == 'read_thread' and s['conversation_id'] == conversation and s['enabled']]
        if not settings.get('enabled') or len(matches) != 1:
            raise lib.LibraryError('API conversation is not registered and enabled for automatic sync.')
        payload = adapters.parse_read_thread(path)
        if not payload['completeness']['pagination_exhausted']:
            raise lib.LibraryError('Automatic API refresh requires pages through the end cursor.')
        result = capture(root, matches[0], payload, state, folder)
        atomic_json(folder / 'state.json', state)
        return receipt(folder, {'status': 'ok', 'sources': [result]})


def status(root):
    catalog.validate_root(root)
    settings = config(root)
    return {'enabled': settings['enabled'], 'interval_minutes': settings.get('interval_minutes'),
            'sources': settings['sources'], 'heartbeat_conversation_id': settings.get('heartbeat_conversation_id')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('status')
    sync = commands.add_parser('sync-files')
    sync.add_argument('--force', action='store_true', help='Recheck contents even when filesystem metadata is unchanged.')
    api = commands.add_parser('import-read-thread')
    api.add_argument('--conversation', required=True)
    api.add_argument('--source', type=Path, required=True)
    args = parser.parse_args()
    try:
        result = status(args.root) if args.command == 'status' else sync_files(args.root, args.force) if args.command == 'sync-files' else import_api(args.root, args.conversation, args.source)
        print(json.dumps(result, ensure_ascii=False))
        return 1 if result.get('status') == 'errors' else 0
    except BlockingIOError:
        print(json.dumps({'status': 'already-running'})); return 0
    except Exception as exc:
        print(json.dumps({'status': 'error', 'error_type': type(exc).__name__, 'detail': 'No source text was printed. Check registered paths, source identity, permissions, and source format.'})); return 1


if __name__ == '__main__':
    sys.exit(main())
