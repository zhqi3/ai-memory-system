"""Source-centric conversation identities and additive project links."""
from pathlib import Path
import json


def core():
    import context_library
    return context_library


def validate_root(root):
    lib = core()
    lib.private_directory(lib.checked_path(root), create=False)
    config = lib.read_json(root / 'library.json')
    memory = config.get('memory_root')
    if not isinstance(memory, str) or not Path(memory).is_absolute():
        raise lib.LibraryError('Library memory_root must be an absolute path.')
    lib.checked_path(Path(memory))


def ensure_path(root, *parts):
    lib = core()
    current = root
    for part in parts:
        current /= part
        lib.private_directory(current, create=True)
    return current


def immutable_json(path, value):
    lib = core()
    if path.exists() or path.is_symlink():
        lib.check_private_file(path)
        if lib.read_json(path) != value:
            raise lib.LibraryError('Catalog identity conflict; existing entry was preserved.')
        return False
    try:
        lib.write_private(path, lib.canonical_bytes(value) + b'\n')
    except FileExistsError:
        return immutable_json(path, value)
    return True


def conversation_keys(root):
    lib = core()
    parent = root / 'conversations'
    lib.checked_path(parent, must_exist=False)
    if not parent.exists():
        return []
    lib.private_directory(parent, create=False)
    result = []
    for path in sorted(parent.iterdir()):
        lib.private_directory(path, create=False)
        if not lib.HEX64.fullmatch(path.name):
            raise lib.LibraryError('Unexpected conversation catalog entry.')
        result.append(path.name)
    return result


def link_project(root, project, key):
    lib = core()
    lib.resolve_project(root, project)
    if key not in conversation_keys(root) or not revisions_for_key(root, key):
        raise lib.LibraryError('No stored conversation exists for this link.')
    directory = ensure_path(root, 'links', lib.project_slug(project))
    created = immutable_json(directory / (key + '.json'), {
        'schema_version': '1.0', 'project': project, 'conversation_key': key,
        'scope': 'conversation; project association does not modify source records'})
    return {'status': 'linked' if created else 'already-linked', 'project': project, 'conversation_key': key}


def links(root, project=None):
    lib = core()
    if project is not None:
        lib.resolve_project(root, project)
    parent = root / 'links'
    lib.checked_path(parent, must_exist=False)
    if not parent.exists():
        return []
    lib.private_directory(parent, create=False)
    directories = [parent / lib.project_slug(project)] if project else sorted(parent.iterdir())
    found = []
    for directory in directories:
        lib.checked_path(directory, must_exist=False)
        if not directory.exists():
            continue
        lib.private_directory(directory, create=False)
        for path in sorted(directory.iterdir()):
            lib.check_private_file(path)
            value = lib.read_json(path)
            name, key = value.get('project'), value.get('conversation_key')
            if not isinstance(name, str) or not isinstance(key, str) or not lib.HEX64.fullmatch(key):
                raise lib.LibraryError('Invalid project link metadata.')
            lib.resolve_project(root, name)
            if directory.name != lib.project_slug(name) or path.name != key + '.json' or project and name != project:
                raise lib.LibraryError('Project link identity does not match its path.')
            found.append(value)
    return found


def revisions_for_key(root, key):
    lib = core()
    if not lib.HEX64.fullmatch(key):
        raise lib.LibraryError('Conversation key must be a full SHA-256.')
    parent = lib.private_directory(root / 'conversations' / key, create=False)
    result = []
    for section in parent.iterdir():
        lib.private_directory(section, create=False)
        if section.name == 'revisions':
            for revision in sorted(section.iterdir()):
                lib.private_directory(revision, create=False)
                if revision.name.startswith('.incoming-'):
                    continue
                if not lib.HEX64.fullmatch(revision.name):
                    raise lib.LibraryError('Unexpected source revision entry.')
                manifest = lib.load_manifest(revision / 'manifest.json', None)
                if manifest['schema_version'] != '2.0':
                    raise lib.LibraryError('New source store expects schema 2.0.')
                result.append((revision, manifest))
        elif section.name == 'legacy-refs':
            for ref in sorted(section.iterdir()):
                lib.check_private_file(ref)
                value = lib.read_json(ref)
                relative = Path(value.get('path', ''))
                if relative.is_absolute() or '..' in relative.parts or len(relative.parts) != 5 or relative.parts[0] != 'projects' or relative.parts[2] != key or relative.parts[3] != 'revisions':
                    raise lib.LibraryError('Invalid legacy revision reference.')
                path = lib.checked_path(root / relative)
                manifest = lib.load_manifest(path / 'manifest.json', value.get('project'))
                if manifest['schema_version'] != '1.0' or value.get('manifest_sha256') != manifest['manifest_sha256'] or ref.name != manifest['revision'] + '.json':
                    raise lib.LibraryError('Legacy reference no longer matches its immutable revision.')
                result.append((path, manifest))
        else:
            raise lib.LibraryError('Unexpected conversation catalog directory.')
    return result


def register_legacy(root, project=None):
    lib = core()
    validate_root(root)
    parent = root / 'projects'
    lib.checked_path(parent, must_exist=False)
    if not parent.exists():
        return {'status': 'registered', 'revision_references': 0, 'conversations': 0}
    lib.private_directory(parent, create=False)
    stored = []
    for directory in sorted(parent.iterdir()):
        lib.private_directory(directory, create=False)
        for conversation in directory.iterdir():
            lib.private_directory(conversation, create=False)
            revisions = lib.private_directory(conversation / 'revisions', create=False)
            for path in revisions.iterdir():
                lib.private_directory(path, create=False)
                if path.name.startswith('.incoming-'):
                    continue
                manifest = lib.load_manifest(path / 'manifest.json', None)
                name = manifest['project']
                if project is not None and name != project:
                    continue
                expected, _ = lib.resolve_project(root, name)
                if directory != expected or manifest['schema_version'] != '1.0':
                    raise lib.LibraryError('Legacy source path does not match project metadata.')
                refs = ensure_path(root, 'conversations', manifest['conversation_key'], 'legacy-refs')
                immutable_json(refs / (manifest['revision'] + '.json'), {
                    'path': str(path.relative_to(root)), 'project': name,
                    'manifest_sha256': manifest['manifest_sha256']})
                stored.append((name, manifest['conversation_key']))
    for name, key in sorted(set(stored)):
        link_project(root, name, key)
    return {'status': 'registered', 'revision_references': len(stored), 'conversations': len(set(key for _, key in stored))}


def enumerate_catalog(root, project=None, unassigned=False):
    validate_root(root)
    keys = conversation_keys(root)
    associations = links(root, project if not unassigned else None)
    if project is not None:
        keys = sorted({item['conversation_key'] for item in associations})
    if unassigned:
        linked = {item['conversation_key'] for item in associations}
        keys = [key for key in keys if key not in linked]
    return [item for key in keys for item in revisions_for_key(root, key)]
