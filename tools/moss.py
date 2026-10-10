"""Explicit MOSS adoption, version-checked switches and bounded handoff export."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile

import context_library as lib
import moss_control
import moss_export
import navigation_reader as reader
import workspace

PROTOCOL_VERSION = 'ai-navigation/1.0.0-rc.1'


def install(path, user_request):
    if not isinstance(user_request, str) or not user_request.strip():
        raise ValueError('Record the actual request authorizing workspace-wide adoption')
    path, memory, _ = workspace.load_workspace(path)
    with reader.vault_lock(memory):
        target = lib.checked_path(memory / 'global/navigation', must_exist=False)
        if target.exists():
            raise ValueError('MOSS is already installed; existing adoption was preserved')
        inputs = {}
        projects = []
        for folder in sorted((memory / 'projects').iterdir()):
            if folder.name.startswith('.') or folder.name == '_template':
                continue
            workspace.project_name(folder.name)
            lib.private_directory(folder, create=False)
            for name in workspace.STARTUP_FILES + ('project-brief.md',):
                source = lib.regular_file(folder / name)
                inputs[source] = source.read_bytes()
                if not inputs[source].strip():
                    raise ValueError('Required project record is empty')
            manifest = reader._object(inputs[folder / 'project-manifest.json'], folder)
            if (manifest.get('canonical_name') != folder.name
                    or manifest.get('schema_version') != '2.0'
                    or manifest.get('index_file') != 'memory-index.md'):
                raise ValueError('Project identity is invalid')
            # Existing explicit bindings must be valid before publishing defaults.
            # A disabled binding still needs a recognized identity and policy.
            local = folder / 'navigation.json'
            if local.exists() or local.is_symlink():
                raw = lib.regular_file(local).read_bytes()
                inputs[local] = raw
                binding = reader._object(raw, local)
                moss_control.validate_local(binding, folder.name, None)
                reader.navigation_snapshot(memory, folder, folder.name,
                    lambda p: inputs.setdefault(p, lib.regular_file(p).read_bytes()), bootstrap=True)
            projects.append(folder.name)
        if not projects:
            raise ValueError('Register a project before adopting MOSS')
        runtime = lib.private_directory(path / 'moss-runtime', create=True)
        guard = {'runtime_roots': [str(runtime)]}
        reader.check_transactions(memory, memory / 'projects', guard, lambda p: p.read_bytes())
        protocol = lib.regular_file(workspace.TEMPLATES / 'navigation/protocol.md').read_bytes()
        protocol_hash = lib.digest(protocol)
        adoption = {'schema_version': 'moss-adoption/1.0', 'system_name': 'MOSS',
                    'scope': 'all-registered-and-future', 'protocol_version': PROTOCOL_VERSION,
                    'protocol_sha256': protocol_hash, 'user_request': user_request,
                    'adopted_at': datetime.now(timezone.utc).isoformat()}
        defaults = {'schema_version': 'moss-defaults/1.0', 'system_name': 'MOSS',
                    'system_version': '1.0.0', 'enabled': True, 'default_enabled': True,
                    'adopted_protocol': {'version': PROTOCOL_VERSION,
                        'path': 'global/navigation/versions/1.0.0-rc.1/protocol.md',
                        'sha256': protocol_hash, 'authorization_ref': 'global/navigation/adoption.json'},
                    'records': reader.RECORDS, 'memory_write_policy': {'mode': 'explicit-only', 'authorization_ref': None},
                    'archive_body_loading': 'explicit-request-only', 'transaction_guard': guard}
        with tempfile.TemporaryDirectory(prefix='.moss-install-', dir=target.parent) as temporary:
            staging = Path(temporary)
            nav = staging / 'navigation'
            lib.private_directory(nav, create=True)
            workspace.write_files(nav, {'moss.json': lib.canonical_bytes(defaults) + b'\n',
                'adoption.json': lib.canonical_bytes(adoption) + b'\n',
                'versions/1.0.0-rc.1/protocol.md': protocol})
            for source, before in inputs.items():
                if lib.regular_file(source).read_bytes() != before:
                    raise ValueError('Project changed during installation; retry after inspection')
            lib.checked_path(target, must_exist=False)
            if target.exists():
                raise ValueError('Navigation appeared during installation; no overwrite')
            os.rename(nav, target)
            lib.fsync_directory(target.parent)
    # Validate publication through the same reader used by clients.
    for project in projects:
        snapshot = workspace.startup(path, project)
        if snapshot.get('navigation', {}).get('status') != 'loaded':
            raise ValueError('Installed adoption did not deliver a complete snapshot')
    return {'status': 'installed', 'protocol_version': PROTOCOL_VERSION,
            'protocol_sha256': protocol_hash, 'projects': projects,
            'future_registered_projects_inherit': True, 'capture_configuration_changed': False,
            'diagnosis_configuration_changed': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    setup = commands.add_parser('install')
    setup.add_argument('--workspace', type=Path, required=True)
    setup.add_argument('--user-request', required=True)
    for action in ('bootstrap', 'status', 'enable', 'disable', 'export'):
        command = commands.add_parser(action)
        command.add_argument('--workspace', type=Path, required=True)
        scope = command.add_mutually_exclusive_group(required=True)
        scope.add_argument('--project')
        if action in ('status', 'enable', 'disable'):
            scope.add_argument('--global', dest='global_scope', action='store_true')
        if action in ('enable', 'disable'):
            command.add_argument('--expect-sha256', required=True)
        if action == 'export':
            command.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        path, _, _ = workspace.load_workspace(args.workspace)
        if args.action == 'install':
            result = install(path, args.user_request)
        elif args.action == 'bootstrap':
            import moss_startup
            result = moss_startup.resolve(path / 'workspace.json', project=args.project, bootstrap=True)
        elif args.action == 'export':
            workspace.explicit_workspace(args.output, exists=False)
            result = moss_export.export(path / 'workspace.json', args.project, args.output)
        else:
            result = {'status': 'ok', **moss_control.control(path / 'workspace.json', args.action,
                getattr(args, 'expect_sha256', None), project=args.project,
                global_scope=getattr(args, 'global_scope', False))}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get('status') in ('installed', 'loaded', 'ok', 'exported') else 1
    except (OSError, ValueError, lib.LibraryError) as exc:
        print(json.dumps({'status': 'error', 'message': str(exc)}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
