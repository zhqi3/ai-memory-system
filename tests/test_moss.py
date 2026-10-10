"""Synthetic public-workspace invariants; no provider or private host data."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import context_library as lib
import moss
import moss_control as control
import moss_export
import moss_startup as startup
import navigation_reader as reader
import workspace


class MossTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve())
        self.base = Path(self.temp.name)
        self.data = self.base / 'data'
        workspace.init_workspace(self.data, 'demo')
        self.memory = self.data / 'AI-Memory'
        self.marker = self.data / 'workspace.json'

    def tearDown(self):
        self.temp.cleanup()

    def install(self):
        return moss.install(self.data, 'Synthetic explicit adoption request')

    def hashes(self, path):
        return {p.relative_to(path).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in path.rglob('*') if p.is_file()}

    def status(self, project='demo'):
        return control.control(self.marker, 'status', project=project)

    def switch(self, action, project='demo'):
        return control.control(self.marker, action, self.status(project)['sha256'], project=project)

    def journal(self, project='demo', verified=False, rolled_back=False):
        folder = self.data / 'moss-runtime/evidence/runtime-memory-operations'
        folder.mkdir(parents=True, exist_ok=True)
        value = {'schema_version':'1.0', 'operation_id':'a'*32, 'request_id':'b'*32,
            'trigger':'explicit_user_request', 'user_request_sha256':'c'*64,
            'timestamp':'2026-01-01T00:00:00Z', 'verified':verified, 'rolled_back':rolled_back,
            'error':'Synthetic interrupted write', 'changes':[{
                'target':str(self.memory / 'projects' / project / 'handoff.md'),
                'mode':'replace_current', 'scope':'project', 'canonical_project':project,
                'existed_before':True, 'before_sha256':'d'*64, 'after_sha256':'e'*64}]}
        file = folder / ('a'*32+'.json')
        file.write_text(json.dumps(value))
        return file

    def test_opt_in_install_preserves_memory_and_capture_settings(self):
        before=self.hashes(self.memory / 'projects');config=(self.data/'AI-Context/sync-config.json').read_bytes()
        result=self.install()
        self.assertEqual(result['status'],'installed')
        self.assertEqual(before,self.hashes(self.memory/'projects'))
        self.assertEqual(config,(self.data/'AI-Context/sync-config.json').read_bytes())
        with self.assertRaises(ValueError):self.install()

    def test_loaded_protocol_is_actual_body_and_archive_independent(self):
        self.install();(self.data/'AI-Context').rename(self.data/'unavailable')
        snapshot=workspace.startup(self.data,'demo')
        self.assertEqual(snapshot['file_count'],9)
        protocol=next(x['content'] for x in snapshot['files'] if x['path'].endswith('/protocol.md'))
        self.assertIn('首次确定目标',protocol)
        self.assertEqual(hashlib.sha256(protocol.encode()).hexdigest(),snapshot['navigation']['protocol_sha256'])
        self.assertFalse(snapshot['archive_bodies_loaded'])

    def test_project_disable_enable_uses_fresh_version(self):
        self.install();initial=self.status();self.switch('disable')
        snapshot=workspace.startup(self.data,'demo')
        self.assertEqual(snapshot['navigation']['status'],'disabled');self.assertEqual(snapshot['file_count'],5)
        before=self.hashes(self.memory)
        with self.assertRaises(ValueError):control.control(self.marker,'enable',initial['sha256'],project='demo')
        self.assertEqual(before,self.hashes(self.memory));self.switch('enable')
        self.assertEqual(workspace.startup(self.data,'demo')['navigation']['status'],'loaded')

    def test_new_project_inherits_and_override_does_not_affect_others(self):
        self.install();workspace.add_project(self.data,'second');self.switch('disable')
        self.assertEqual(workspace.startup(self.data,'second')['navigation']['status'],'loaded')
        self.assertEqual(workspace.startup(self.data,'demo')['navigation']['status'],'disabled')

    def test_master_disable_overrides_project_enable(self):
        self.install();self.switch('disable');self.switch('enable')
        state=control.control(self.marker,'status',global_scope=True)
        control.control(self.marker,'disable',state['sha256'],global_scope=True)
        self.assertEqual(workspace.startup(self.data,'demo')['navigation']['disabled_by'],'master')
        self.assertFalse(self.status()['effective_enabled'])

    def test_protocol_hash_conflict_delivers_no_partial_body_and_can_disable(self):
        self.install();file=self.memory/'global/navigation/versions/1.0.0-rc.1/protocol.md'
        file.write_text('Synthetic tampered protocol')
        result=startup.resolve(self.marker,project='demo',bootstrap=True)
        self.assertEqual(result['status'],'version-conflict');self.assertNotIn('contents',result)
        with self.assertRaises(lib.LibraryError):workspace.startup(self.data,'demo')
        state=control.control(self.marker,'status',global_scope=True)
        control.control(self.marker,'disable',state['sha256'],global_scope=True)
        self.assertEqual(workspace.startup(self.data,'demo')['file_count'],5)

    def test_adoption_mismatch_delivers_no_partial_body(self):
        self.install();file=self.memory/'global/navigation/adoption.json';value=json.loads(file.read_text())
        value['protocol_version']='ai-navigation/other';file.write_text(json.dumps(value))
        result=startup.resolve(self.marker,project='demo',bootstrap=True)
        self.assertEqual(result['status'],'version-conflict');self.assertNotIn('contents',result)

    def test_missing_required_goal_is_not_silently_omitted(self):
        self.install();(self.memory/'projects/demo/project-brief.md').unlink()
        result=startup.resolve(self.marker,project='demo',bootstrap=True)
        self.assertEqual(result['status'],'partial');self.assertNotIn('contents',result)

    def test_interrupted_transaction_blocks_active_and_disabled_snapshots(self):
        self.install();self.journal()
        result=startup.resolve(self.marker,project='demo',bootstrap=True)
        self.assertEqual(result['status'],'partial');self.assertNotIn('contents',result)
        self.assertIn('operations',result['navigation']['diagnostics'])
        defaults=self.memory/'global/navigation/moss.json';value=json.loads(defaults.read_text());value['enabled']=False
        defaults.write_text(json.dumps(value))
        self.assertEqual(startup.resolve(self.marker,project='demo',bootstrap=True)['status'],'partial')

    def test_unrelated_transaction_does_not_block_selected_project(self):
        self.install();workspace.add_project(self.data,'second');self.journal('second')
        self.assertEqual(workspace.startup(self.data,'demo')['navigation']['status'],'loaded')
        with self.assertRaises(lib.LibraryError):workspace.startup(self.data,'second')

    def test_completed_journal_does_not_require_historical_current_hash(self):
        self.install();self.journal(verified=True)
        self.assertEqual(workspace.startup(self.data,'demo')['navigation']['status'],'loaded')

    def test_rolled_back_journal_is_terminal(self):
        self.install();self.journal(rolled_back=True)
        self.assertEqual(workspace.startup(self.data,'demo')['navigation']['status'],'loaded')

    def test_unknown_journal_schema_fails_without_contents(self):
        self.install();file=self.journal();value=json.loads(file.read_text());value['schema_version']='unknown'
        file.write_text(json.dumps(value));result=startup.resolve(self.marker,project='demo',bootstrap=True)
        self.assertEqual(result['status'],'partial');self.assertNotIn('contents',result)

    def test_reader_busy_is_bounded_and_returns_no_body(self):
        self.install()
        with reader.vault_lock(self.memory):
            result=startup.resolve(self.marker,project='demo',bootstrap=True)
        self.assertEqual(result['status'],'partial');self.assertNotIn('contents',result)
        self.assertTrue(result['navigation']['diagnostics']['retryable'])

    def test_private_handoff_preserves_delivery_hashes_and_does_not_send(self):
        self.install();file=self.memory/'projects/demo/handoff.md';file.write_bytes(b'\xef\xbb\xbf'+file.read_bytes())
        output=self.base/'handoff.json';result=moss_export.export(self.marker,'demo',output)
        data=json.loads(output.read_text());self.assertFalse(result['sent']);self.assertEqual(output.stat().st_mode&0o777,0o600)
        self.assertFalse(data['scope']['live_local_access']);self.assertFalse(data['scope']['new_action_authority'])
        self.assertNotEqual(data['delivered_utf8_sha256']['handoff.md'],data['snapshot']['sha256']['handoff.md'])
        for name,body in data['snapshot']['contents'].items():
            self.assertEqual(hashlib.sha256(body.encode()).hexdigest(),data['delivered_utf8_sha256'][name])
        before=output.read_bytes()
        with self.assertRaises(FileExistsError):moss_export.export(self.marker,'demo',output)
        self.assertEqual(before,output.read_bytes())

    def test_disabled_handoff_is_rejected(self):
        self.install();self.switch('disable')
        with self.assertRaises(ValueError):moss_export.export(self.marker,'demo',self.base/'out.json')
        self.assertFalse((self.base/'out.json').exists())

    def test_symlink_protocol_is_rejected(self):
        self.install();file=self.memory/'global/navigation/versions/1.0.0-rc.1/protocol.md'
        external=self.base/'external';external.write_text('Synthetic unrelated file');file.unlink();file.symlink_to(external)
        result=startup.resolve(self.marker,project='demo',bootstrap=True)
        self.assertEqual(result['status'],'partial');self.assertNotIn('contents',result)

    def test_duplicate_project_alias_is_rejected_without_guessing(self):
        self.install();workspace.add_project(self.data,'second')
        file=self.memory/'projects/second/project-manifest.json';data=json.loads(file.read_text());data['aliases']=['demo'];file.write_text(json.dumps(data))
        result=startup.resolve(self.marker,project='demo',bootstrap=True)
        self.assertEqual(result['status'],'partial');self.assertNotIn('contents',result)

    def test_install_does_not_overwrite_project_binding(self):
        project=self.memory/'projects/demo/navigation.json';project.write_text('{"synthetic":"existing"}')
        # An invalid existing explicit binding must prevent successful adoption.
        with self.assertRaises(ValueError):self.install()
        self.assertEqual(project.read_text(),'{"synthetic":"existing"}')
        self.assertFalse((self.memory/'global/navigation').exists())

    def test_cli_from_unrelated_cwd_and_empty_home(self):
        home=self.base/'home';home.mkdir();env=dict(os.environ,HOME=str(home),PYTHONDONTWRITEBYTECODE='1')
        command=[sys.executable,'-B',str(workspace.REPO_ROOT/'tools/moss.py')]
        for args in [['install','--user-request','Synthetic adoption'],['bootstrap','--project','demo'],['export','--project','demo','--output',str(self.base/'out.json')]]:
            result=subprocess.run([*command,*args,'--workspace',str(self.data)],cwd=self.base,env=env,capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual(list(home.iterdir()),[])


if __name__=='__main__':unittest.main()
