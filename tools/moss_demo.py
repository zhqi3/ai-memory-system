"""Synthetic MOSS lifecycle; no providers, personal configuration or model calls."""
import hashlib
import json
from pathlib import Path
import tempfile

import moss
import moss_control
import moss_export
import workspace


def run_demo():
    with tempfile.TemporaryDirectory(prefix='moss-demo-', dir=Path(tempfile.gettempdir()).resolve()) as temp:
        base = Path(temp)
        path = base / 'data'
        workspace.init_workspace(path, 'demo')
        memory = path / 'AI-Memory'
        before = {p: hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in (memory / 'projects').rglob('*') if p.is_file()}
        moss.install(path, 'Synthetic request to adopt MOSS in this temporary workspace')
        active = workspace.startup(path, 'demo')
        if active['navigation']['status'] != 'loaded' or active['file_count'] != 9:
            raise ValueError('Active snapshot scope is incomplete')
        handoff = moss_export.export(path / 'workspace.json', 'demo', base / 'handoff.json')
        status = moss_control.control(path / 'workspace.json', 'status', project='demo')
        moss_control.control(path / 'workspace.json', 'disable', status['sha256'], project='demo')
        disabled = workspace.startup(path, 'demo')
        if disabled['navigation']['status'] != 'disabled' or disabled['file_count'] != 5:
            raise ValueError('Disabled snapshot did not preserve compact fallback')
        status = moss_control.control(path / 'workspace.json', 'status', project='demo')
        moss_control.control(path / 'workspace.json', 'enable', status['sha256'], project='demo')
        workspace.add_project(path, 'second')
        if workspace.startup(path, 'second')['navigation']['status'] != 'loaded':
            raise ValueError('New registered project did not inherit adoption')
        if any(hashlib.sha256(p.read_bytes()).hexdigest() != value for p, value in before.items()):
            raise ValueError('Existing compact project memory was changed')
        return {'status': 'passed', 'fixture': 'synthetic only', 'active_files': 9,
                'disabled_files': 5, 'new_project_inherits': True, 'original_memory_unchanged': True,
                'handoff_files': handoff['files'], 'archive_bodies_loaded': False,
                'provider_calls': 0, 'temporary_data_removed': True}


if __name__ == '__main__':
    print(json.dumps(run_demo()))
