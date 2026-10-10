"""MOSS bootstrap and explicitly requested switches; never rewrite project state."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile


import moss_startup as startup
reader = startup._reader

def digest(data):
    return hashlib.sha256(data).hexdigest() if data is not None else 'absent'


def project_identity(root, requested, capture):
    """Resolve only registered identity; a disable must not need protocol bodies."""
    if requested == '_template':
        raise ValueError('_template is not a project')
    matches = []
    for path in (root / 'projects').iterdir():
        if path.name.startswith('.') or path.name == '_template':
            continue
        startup.physical(path, kind='dir')
        manifest = reader._object(capture(path / 'project-manifest.json'), path / 'project-manifest.json')
        if (manifest.get('schema_version') != '2.0' or manifest.get('index_file') != 'memory-index.md'
                or not isinstance(manifest.get('canonical_name'), str)
                or not manifest['canonical_name'].strip()
                or not isinstance(manifest.get('aliases', []), list)
                or any(not isinstance(name, str) for name in manifest.get('aliases', []))):
            raise ValueError('Malformed registered project identity')
        if requested in [path.name, manifest['canonical_name'], *manifest.get('aliases', [])]:
            matches.append((path, manifest['canonical_name']))
    if len(matches) != 1:
        raise ValueError(f'Project identity must resolve uniquely; matches={len(matches)}')
    return matches[0]


def validate_local(value, canonical, defaults):
    if (value.get('project') != canonical or type(value.get('enabled')) is not bool
            or value.get('schema_version') not in ('ai-navigation-binding/1.0', 'moss-project-override/1.0')):
        raise ValueError('Invalid project navigation identity or schema')
    if value['schema_version'] == 'ai-navigation-binding/1.0':
        reader.validate_policy(value)
        reader.validate_protocol_reference(value)
        return value
    if set(value) != {'schema_version', 'project', 'enabled'} or defaults is None:
        raise ValueError('Invalid project override or missing MOSS defaults')
    return None


def gap(exc):
    return {'status': getattr(exc, 'status', 'partial'), 'message': str(exc),
            'diagnostics': getattr(exc, 'diagnostics', {}), 'protocol_loaded': False}


def control(config, action, expected=None, *, project=None, global_scope=False):
    if action not in ('status', 'enable', 'disable'):
        raise ValueError('Unsupported action')
    if bool(project) == bool(global_scope):
        raise ValueError('Select exactly one --project or --global')
    config = startup.physical(config, kind='file')
    pointer_bytes = config.read_bytes()
    pointer = reader._object(pointer_bytes, config)
    if pointer.get('schema_version') != 'ai-memory-workspace/1.0' or not isinstance(pointer.get('memory_root'), str):
        raise ValueError('Invalid memory root pointer')
    from workspace import load_workspace
    _, root, _ = load_workspace(config.parent)
    if config.name != 'workspace.json':
        raise ValueError('Use this workspace marker only')
    with reader.vault_lock(root):
        if startup.physical(config, kind='file').read_bytes() != pointer_bytes:
            raise ValueError('Memory pointer changed while waiting; no switch changed')
        captured = {config: pointer_bytes}

        def capture(path):
            path = startup.physical(path, kind='file')
            if path not in captured:
                captured[path] = path.read_bytes()
            return captured[path]

        defaults = reader.global_defaults(root, capture)
        defaults_absent = defaults is None
        if global_scope:
            if defaults is None:
                raise ValueError('MOSS defaults are not installed')
            target = root / 'global/navigation/moss.json'
            value = defaults
            bindings = [defaults]
            for project_dir in sorted((root / 'projects').iterdir()):
                if project_dir.name.startswith('.') or project_dir.name == '_template':
                    continue
                startup.physical(project_dir, kind='dir')
                binding_path = reader.absolute_path(project_dir / 'navigation.json')
                if not binding_path.exists():
                    continue
                local = reader._object(capture(binding_path), binding_path)
                manifest = reader._object(capture(project_dir / 'project-manifest.json'), project_dir / 'project-manifest.json')
                if (manifest.get('schema_version') != '2.0' or manifest.get('index_file') != 'memory-index.md'
                        or not isinstance(manifest.get('canonical_name'), str) or not manifest['canonical_name'].strip()):
                    raise ValueError('Invalid registered identity prevents a global switch')
                pinned = validate_local(local, manifest.get('canonical_name'), defaults)
                if pinned is not None:
                    bindings.append(pinned)
            transaction = reader.check_transactions(root, root / 'projects', reader.runtime_union(*bindings), capture)
            info = {'scope': 'global', 'system_name': 'MOSS', 'system_version': defaults['system_version'],
                    'effective_enabled': defaults['enabled'], 'default_enabled': defaults['default_enabled'],
                    'transaction_guard': transaction}
            if action == 'enable':
                reader.pinned_protocol(root, defaults, None, capture, global_scope=True)
            elif action == 'status' and defaults['enabled']:
                try:
                    reader.pinned_protocol(root, defaults, None, capture, global_scope=True)
                    info['protocol_status'] = 'verified'
                except (OSError, ValueError, UnicodeError) as exc:
                    info.update(effective_enabled=None, protocol_status='unavailable', snapshot_error=gap(exc))
        else:
            project_path, canonical = project_identity(root, project, capture)
            target = reader.absolute_path(project_path / 'navigation.json')
            if target.exists():
                value = reader._object(capture(target), target)
            else:
                if defaults is None:
                    raise ValueError('No MOSS defaults or project navigation are installed')
                value = {'schema_version': 'moss-project-override/1.0', 'project': canonical,
                         'enabled': defaults['default_enabled']}
            local = validate_local(value, canonical, defaults)
            transaction = reader.check_transactions(root, project_path, reader.runtime_union(defaults, local), capture)
            effective = value['enabled'] and (defaults is None or defaults['enabled'])
            info = {'scope': 'project', 'canonical_name': canonical,
                    'effective_enabled': effective, 'transaction_guard': transaction}

            def inspect_protocols():
                if defaults:
                    reader.pinned_protocol(root, defaults, canonical, capture, global_scope=True)
                if local:
                    reader.pinned_protocol(project_path, local, canonical, capture)

            if action == 'enable':
                inspect_protocols()
                for name in (reader.RECORDS[key] for key in ('identity', 'goal', 'current_state', 'permissions')):
                    if not capture(reader.relative_file(project_path, name)).strip():
                        raise ValueError(f'Required navigation record is empty: {name}')
            elif action == 'status':
                if effective:
                    try:
                        inspect_protocols()
                    except (OSError, ValueError, UnicodeError) as exc:
                        info.update(effective_enabled=None, snapshot_error=gap(exc))
                if 'snapshot_error' not in info:
                    snapshot = startup._snapshot(root, config, pointer_bytes,
                        {'status': 'loaded', 'memory_root': str(root), 'read_only': True}, project, None, False)
                    info['navigation'] = snapshot['navigation']
        target = reader.absolute_path(target)
        before = capture(target) if target.exists() else None
        before_hash = digest(before)
        info.update(path=str(target), enabled=value['enabled'], sha256=before_hash, changed=False)
        if action == 'status':
            for path, raw in captured.items():
                if startup.physical(path, kind='file').read_bytes() != raw:
                    raise ValueError(f'Input changed during status; retry: {path}')
            return info
        if expected != before_hash:
            raise ValueError('Expected version does not match; inspect current status before retrying')
        value = dict(value, enabled=action == 'enable')
        after = (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
        if before is not None and reader._object(before, target)['enabled'] == value['enabled']:
            # Preserve existing formatting and avoid a meaningless rewrite.
            return info
        fd, temp = tempfile.mkstemp(prefix='.moss-', suffix='.tmp', dir=target.parent)
        try:
            with os.fdopen(fd, 'wb') as output:
                os.fchmod(output.fileno(), stat.S_IMODE(target.stat().st_mode) if before is not None else 0o600)
                output.write(after)
                output.flush()
                os.fsync(output.fileno())
            for path, raw in captured.items():
                if startup.physical(path, kind='file').read_bytes() != raw:
                    raise ValueError(f'Input changed during switch; no overwrite: {path}')
            if before is None and (target.exists() or target.is_symlink()):
                raise ValueError('Project override appeared during switch; no overwrite')
            if defaults_absent:
                default_path = reader.absolute_path(root / 'global/navigation/moss.json')
                if default_path.exists():
                    raise ValueError('Global defaults appeared during switch; no overwrite')
            reader.absolute_path(target)
            os.replace(temp, target)
            parent_fd = os.open(target.parent, os.O_RDONLY)
            try:
                os.fsync(parent_fd)
            finally:
                os.close(parent_fd)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)
        if startup.physical(target, kind='file').read_bytes() != after:
            raise ValueError('Switch readback failed; inspect current file before retrying')
        info.update(enabled=value['enabled'], before_sha256=before_hash, sha256=digest(after), changed=True)
        if global_scope:
            info['effective_enabled'] = value['enabled']
        else:
            info['effective_enabled'] = value['enabled'] and (defaults is None or defaults['enabled'])
            # Pre-change navigation diagnostics must not be mistaken for current status.
            info.pop('navigation', None)
        return info
