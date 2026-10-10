"""Pinned project navigation snapshots; never recover or rewrite canonical memory.

The lock name and flock protocol match memory_bridge.store.MemoryStore. Journals
are metadata, not instructions. Only explicitly registered runtimes are covered.
"""
from contextlib import contextmanager
from datetime import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import time


RECORDS = {
    'identity': 'project-manifest.json', 'goal': 'project-brief.md',
    'current_state': 'handoff.md', 'decisions': 'decisions.md',
    'evidence': 'findings.md', 'permissions': 'working-agreement.md',
}
HASH = re.compile(r'[0-9a-f]{64}\Z')
OP = re.compile(r'[0-9a-f]{32}\Z')
MAX_JOURNALS = 10000
MAX_JOURNAL_BYTES = 2 * 1024 * 1024


class NavigationReadError(ValueError):
    def __init__(self, message, *, status='partial', diagnostics=None):
        super().__init__(message)
        self.status = status
        self.diagnostics = diagnostics or {}


def absolute_path(path, *, must_exist=False):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts or any(ord(c) < 32 for c in str(path)):
        raise NavigationReadError(f'Invalid absolute path: {path}')
    for component in [path, *path.parents]:
        if component.is_symlink():
            raise NavigationReadError(f'Symlink component forbidden: {component}')
    if must_exist and not path.exists():
        raise NavigationReadError(f'Required path missing: {path}')
    return path


def relative_file(project_path, relative):
    if not isinstance(relative, str) or not relative or any(ord(c) < 32 for c in relative):
        raise NavigationReadError('A nonempty relative file path is required')
    part = Path(relative)
    if part.is_absolute() or '..' in part.parts or relative != part.as_posix():
        raise NavigationReadError(f'Invalid project-relative path: {relative}')
    result = absolute_path(project_path / part, must_exist=True)
    if not result.is_file() or not result.resolve().is_relative_to(project_path):
        raise NavigationReadError(f'Invalid project file: {relative}')
    return result


@contextmanager
def vault_lock(root, timeout_seconds=2.0):
    """Serialize the complete reader snapshot with every conforming writer."""
    lock_dir = absolute_path(root.parent / '.ai-memory-locks')
    lock_dir.mkdir(mode=0o700, exist_ok=True)
    path = absolute_path(lock_dir / (hashlib.sha256(str(root.resolve()).encode()).hexdigest() + '.lock'))
    # Existing lock files are opened read-only: an ordinary reader need not have
    # write permission to the coordination file. flock still coordinates writers.
    try:
        handle = path.open('rb') if path.exists() else os.fdopen(os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600), 'r+b')
    except FileExistsError:
        handle = path.open('rb')
    deadline = time.monotonic() + timeout_seconds
    try:
        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise NavigationReadError('Memory writer lock is busy; retry after the writer finishes',
                        diagnostics={'lock_path': str(path), 'retryable': True})
                time.sleep(0.025)
        yield path
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def _object(raw, location):
    try:
        def unique(pairs):
            value = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError(f'duplicate key: {key}')
                value[key] = item
            return value
        result = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=unique)
    except (ValueError, UnicodeError) as exc:
        raise NavigationReadError(f'Invalid JSON: {location}: {exc}') from exc
    if not isinstance(result, dict):
        raise NavigationReadError(f'JSON object required: {location}')
    return result


def _journal(raw, path):
    data = _object(raw, path)
    keys = {'schema_version', 'operation_id', 'request_id', 'trigger',
            'user_request_sha256', 'timestamp', 'verified', 'rolled_back', 'error', 'changes'}
    if set(data) != keys or data.get('schema_version') != '1.0':
        raise NavigationReadError(f'Unknown journal schema: {path}')
    if (not isinstance(data['operation_id'], str) or not OP.fullmatch(data['operation_id'])
            or path.name != data['operation_id'] + '.json'
            or not isinstance(data['request_id'], str) or not OP.fullmatch(data['request_id'])
            or not isinstance(data['user_request_sha256'], str) or not HASH.fullmatch(data['user_request_sha256'])
            or data['trigger'] not in ('explicit_user_request', 'authorized_named_milestone')
            or type(data['verified']) is not bool or type(data['rolled_back']) is not bool
            or (data['verified'] and data['rolled_back']) or not isinstance(data['error'], str)):
        raise NavigationReadError(f'Malformed operation metadata: {path}')
    try:
        stamp = datetime.fromisoformat(data['timestamp'].replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            raise ValueError('missing timezone')
    except (AttributeError, TypeError, ValueError) as exc:
        raise NavigationReadError(f'Invalid journal timestamp: {path}') from exc
    changes = data['changes']
    if not isinstance(changes, list) or not 1 <= len(changes) <= 100:
        raise NavigationReadError(f'Invalid change list: {path}')
    required = {'target', 'mode', 'scope', 'canonical_project', 'existed_before', 'before_sha256', 'after_sha256'}
    seen = set()
    for change in changes:
        if not isinstance(change, dict) or set(change) != required:
            raise NavigationReadError(f'Invalid change metadata: {path}')
        if (not isinstance(change['target'], str) or not isinstance(change['after_sha256'], str)
                or not HASH.fullmatch(change['after_sha256']) or type(change['existed_before']) is not bool
                or change['mode'] not in ('append', 'replace_current', 'create_canonical')
                or change['scope'] not in ('project', 'global')
                or (change['canonical_project'] is not None and not isinstance(change['canonical_project'], str))):
            raise NavigationReadError(f'Malformed change fields: {path}')
        before = change['before_sha256']
        if ((change['existed_before'] and (not isinstance(before, str) or not HASH.fullmatch(before)))
                or (not change['existed_before'] and before is not None)):
            raise NavigationReadError(f'Invalid before-image metadata: {path}')
        target = absolute_path(change['target'])
        if str(target) in seen:
            raise NavigationReadError(f'Duplicate journal target: {path}')
        seen.add(str(target))
    return data


def check_transactions(root, project_path, config, read_bytes):
    if not isinstance(config, dict) or set(config) != {'runtime_roots'}:
        raise NavigationReadError('transaction_guard must register runtime_roots')
    roots = config['runtime_roots']
    if not isinstance(roots, list) or not roots or len(roots) > 16 or any(not isinstance(p, str) for p in roots):
        raise NavigationReadError('transaction_guard requires 1 to 16 absolute runtime roots')
    checked, unresolved, count = [], [], 0
    for raw_root in roots:
        runtime = absolute_path(raw_root, must_exist=True)
        if not runtime.is_dir() or str(runtime.resolve()) in checked:
            raise NavigationReadError('Invalid or duplicate registered runtime root')
        checked.append(str(runtime.resolve()))
        journal_dir = absolute_path(runtime / 'evidence' / 'runtime-memory-operations')
        if not journal_dir.exists():
            continue  # A registered, unused runtime has no operations yet.
        if not journal_dir.is_dir():
            raise NavigationReadError(f'Journal directory is inaccessible: {journal_dir}')
        for path in sorted(journal_dir.iterdir()):
            if path.name.endswith('.tmp'):
                raise NavigationReadError(f'Unresolved journal temporary file: {path}')
            if path.suffix != '.json':
                continue
            count += 1
            if count > MAX_JOURNALS:
                raise NavigationReadError('Journal scan limit exceeded; no snapshot delivered')
            absolute_path(path, must_exist=True)
            if not path.is_file() or path.stat().st_size > MAX_JOURNAL_BYTES:
                raise NavigationReadError(f'Invalid or oversized journal: {path}')
            journal = _journal(read_bytes(path), path)
            def under(target, directory):
                # Match the writer's case-folded policy on macOS volumes too.
                return str(target).casefold().startswith(str(directory).casefold().rstrip('/') + '/')
            affected = [c for c in journal['changes'] if under(c['target'], project_path)
                        or under(c['target'], root / 'global')]
            # Completed journals attest to their own operation, not to the latest
            # version; comparing historical after-hashes would reject later edits.
            if affected and not journal['verified'] and not journal['rolled_back']:
                inputs = []
                for index, change in enumerate(journal['changes']):
                    backup = runtime / 'recovery' / 'memory' / journal['operation_id'] / f'{index:03d}.bak'
                    inputs.append({'target': change['target'], 'before_sha256': change['before_sha256'],
                        'after_sha256': change['after_sha256'], 'preimage_path': str(backup) if change['existed_before'] else None,
                        'preimage_exists': backup.is_file() if change['existed_before'] else None,
                        'preimage_hash_verified': False})
                unresolved.append({'operation_id': journal['operation_id'], 'journal_path': str(path),
                    'state': 'unfinished', 'inputs': inputs,
                    'required_next_evidence': ['Verify current target hashes against journal before/after images',
                        'Read and verify every required preimage before proposing recovery',
                        'Obtain explicit recovery authorization; recheck versions immediately before mutation',
                        'Verify all batch targets and terminal journal after recovery'],
                    'automatic_recovery': False})
    report = {'status': 'clear', 'runtime_roots': checked, 'journals_checked': count,
              'coverage': 'registered-runtimes-only', 'automatic_recovery': False}
    if unresolved:
        report.update(status='unfinished', operations=unresolved)
        raise NavigationReadError('Unfinished memory transaction affects this project; no memory snapshot delivered', diagnostics=report)
    return report


def validate_policy(binding):
    if (binding.get('records') != RECORDS or binding.get('archive_body_loading') != 'explicit-request-only'
            or binding.get('memory_write_policy') != {'mode': 'explicit-only', 'authorization_ref': None}):
        raise NavigationReadError('Unsupported navigation record map or permissions')


def validate_protocol_reference(binding, *, global_scope=False):
    protocol = binding.get('adopted_protocol')
    if (not isinstance(protocol, dict) or set(protocol) != {'version', 'path', 'sha256', 'authorization_ref'}
            or not isinstance(protocol.get('version'), str) or not protocol['version'].startswith('ai-navigation/')
            or not isinstance(protocol.get('sha256'), str) or not HASH.fullmatch(protocol['sha256'])):
        raise NavigationReadError('Malformed adopted protocol binding')
    suffix = protocol['version'].removeprefix('ai-navigation/')
    prefix = 'global/' if global_scope else ''
    if (not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}', suffix)
            or protocol['path'] != f'{prefix}navigation/versions/{suffix}/protocol.md'
            or protocol['authorization_ref'] != f'{prefix}navigation/adoption.json'):
        raise NavigationReadError('Protocol and adoption must use the known navigation paths')
    return protocol


def pinned_protocol(base, binding, canonical_name, read_bytes, *, global_scope=False):
    protocol = validate_protocol_reference(binding, global_scope=global_scope)
    protocol_path = relative_file(base, protocol['path'])
    raw = read_bytes(protocol_path)
    actual_hash = hashlib.sha256(raw).hexdigest()
    if actual_hash != protocol['sha256']:
        raise NavigationReadError('Adopted protocol hash mismatch', status='version-conflict',
            diagnostics={'expected_sha256': protocol['sha256'], 'actual_sha256': actual_hash})
    adoption_path = relative_file(base, protocol['authorization_ref'])
    adoption_raw = read_bytes(adoption_path)
    adoption = _object(adoption_raw, adoption_path)
    identity_ok = (adoption.get('schema_version') == 'moss-adoption/1.0'
                   and adoption.get('system_name') == 'MOSS'
                   and adoption.get('scope') == 'all-registered-and-future') if global_scope else (
                       adoption.get('schema_version') == 'ai-navigation-adoption/1.0'
                       and adoption.get('project') == canonical_name)
    if (not identity_ok or adoption.get('protocol_version') != protocol['version'] or adoption.get('protocol_sha256') != actual_hash
            or not isinstance(adoption.get('user_request'), str) or not adoption['user_request'].strip()
            or not isinstance(adoption.get('adopted_at'), str) or not adoption['adopted_at'].strip()):
        raise NavigationReadError('Adoption record does not match this pinned project/version', status='version-conflict')
    return protocol, protocol_path, adoption_path, {protocol['path']: raw, protocol['authorization_ref']: adoption_raw}


def global_defaults(root, read_bytes):
    """Read explicit defaults only. Absence preserves the legacy project-only path."""
    path = absolute_path(root / 'global/navigation/moss.json')
    if not path.exists() and not path.is_symlink():
        return None
    binding = _object(read_bytes(relative_file(root, 'global/navigation/moss.json')), path)
    required = {'schema_version', 'system_name', 'system_version', 'enabled', 'default_enabled',
                'adopted_protocol', 'records', 'memory_write_policy', 'archive_body_loading', 'transaction_guard'}
    if (set(binding) != required or binding.get('schema_version') != 'moss-defaults/1.0'
            or binding.get('system_name') != 'MOSS' or binding.get('system_version') != '1.0.0'
            or type(binding.get('enabled')) is not bool or type(binding.get('default_enabled')) is not bool):
        raise NavigationReadError('MOSS defaults identity or schema mismatch', status='version-conflict')
    validate_policy(binding)
    validate_protocol_reference(binding, global_scope=True)
    return binding


def runtime_union(*bindings):
    roots = []
    for binding in bindings:
        if binding is None:
            continue
        config = binding.get('transaction_guard')
        if (not isinstance(config, dict) or set(config) != {'runtime_roots'}
                or not isinstance(config['runtime_roots'], list) or not 1 <= len(config['runtime_roots']) <= 16
                or any(not isinstance(p, str) for p in config['runtime_roots'])
                or len(set(config['runtime_roots'])) != len(config['runtime_roots'])):
            raise NavigationReadError('transaction_guard requires unique registered runtime_roots')
        for path in config['runtime_roots']:
            if path not in roots:
                roots.append(path)
    return {'runtime_roots': roots}


def navigation_snapshot(root, project_path, canonical_name, read_bytes, *, bootstrap):
    """Resolve MOSS defaults and project overrides under the canonical vault lock."""
    defaults = global_defaults(root, read_bytes)
    binding_path = absolute_path(project_path / 'navigation.json')
    local = None
    if binding_path.exists() or binding_path.is_symlink():
        local = _object(read_bytes(relative_file(project_path, 'navigation.json')), binding_path)
        if (local.get('schema_version') not in ('ai-navigation-binding/1.0', 'moss-project-override/1.0')
                or local.get('project') != canonical_name or type(local.get('enabled')) is not bool):
            raise NavigationReadError('Navigation binding identity or schema mismatch', status='version-conflict')
        if local['schema_version'] == 'moss-project-override/1.0':
            if set(local) != {'schema_version', 'project', 'enabled'} or defaults is None:
                raise NavigationReadError('MOSS project override requires valid global defaults')
    if defaults is None and local is None:
        return None, {}
    pinned_local = local if local and local['schema_version'] == 'ai-navigation-binding/1.0' else None
    guard = check_transactions(root, project_path, runtime_union(defaults, pinned_local), read_bytes)
    source = 'project' if pinned_local else 'global'
    metadata = {'source': source, 'transaction_guard': guard}
    if defaults:
        metadata.update(system_name='MOSS', system_version=defaults['system_version'],
                        master_enabled=defaults['enabled'], default_enabled=defaults['default_enabled'],
                        project_override=local is not None)
    effective_enabled = (defaults is None or defaults['enabled']) and (
        local['enabled'] if local is not None else defaults['default_enabled'])
    # An explicit master-off is a usable fallback even if a protocol was removed.
    # Journal checks remain active, so this cannot expose a half-written memory.
    if not effective_enabled:
        metadata.update(status='disabled', disabled_by='master' if defaults and not defaults['enabled'] else 'project' if local else 'default')
        return metadata, {}
    if defaults:
        shared = pinned_protocol(root, defaults, canonical_name, read_bytes, global_scope=True)
    binding = pinned_local or defaults
    validate_policy(binding)
    if pinned_local:
        protocol, protocol_path, adoption_path, extras = pinned_protocol(project_path, binding, canonical_name, read_bytes)
        extras['navigation.json'] = read_bytes(binding_path)
        if defaults:
            extras['global/navigation/adoption.json'] = shared[3]['global/navigation/adoption.json']
    else:
        protocol, protocol_path, adoption_path, extras = shared
        if local:
            extras['navigation.json'] = read_bytes(binding_path)
    if defaults:
        extras['global/navigation/moss.json'] = read_bytes(root / 'global/navigation/moss.json')
    for name in (RECORDS[key] for key in ('identity', 'goal', 'current_state', 'permissions')):
        content = read_bytes(relative_file(project_path, name))
        if not content.strip():
            raise NavigationReadError(f'Required navigation record is empty: {name}')
        extras[name] = content
    return {**metadata, 'status': 'loaded' if bootstrap else 'inspected', 'version': protocol['version'],
            'protocol_path': str(protocol_path), 'protocol_sha256': protocol['sha256'],
            'authorization_ref': str(adoption_path), 'authorization_scope': 'recorded-user-request-only',
            'memory_write_policy': binding['memory_write_policy'],
            'on_demand_records': {key: RECORDS[key] for key in ('decisions', 'evidence')},
            'archive_body_loading': binding['archive_body_loading'], 'transaction_guard': guard}, extras
