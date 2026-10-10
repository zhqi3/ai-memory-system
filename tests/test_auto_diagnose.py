"""Synthetic cost, concurrency and metadata boundaries. No live model calls."""
import json
import io
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import test_context_library as fixtures
import auto_diagnose as diagnose
import auto_sync as sync
import sync_health as health


class DiagnosisTests(unittest.TestCase):
    setUp = fixtures.ArchiveTests.setUp
    tearDown = fixtures.ArchiveTests.tearDown

    def prepare(self, incidents=None, enabled=True, automatic=True):
        settings = {'enabled': enabled, 'sources': [{'conversation_id': 'source-1',
                    'enabled': True, 'kind': 'codex'}], 'exception_diagnosis': {
            'mode': 'automatic' if automatic else 'on-demand', 'automatic_ai_enabled': automatic,
            'codex_executable': '/synthetic/codex', 'max_attempts_per_24h': 4}}
        sync.atomic_json(self.root / 'sync-config.json', settings)
        folder = self.root / 'sync'
        folder.mkdir(mode=0o700)
        (folder / 'health').mkdir(mode=0o700)
        self.queue(incidents or [])

    def queue(self, incidents):
        sync.atomic_json(self.root / 'sync/health/pending.json', {
            'schema_version': health.SCHEMA, 'status': 'attention' if incidents else 'ok', 'incidents': incidents})

    def incident(self, identity='failure-1', **extra):
        return {'id': identity, 'kind': 'source-failure', 'conversation_id': 'source-1',
                'severity': 'attention', 'action': 'diagnose', 'first_receipt': 'first.json',
                'last_receipt': 'last.json', 'error_type': 'LibraryError', **extra}

    def runner(self):
        return Mock(return_value={'status': 'completed', 'usage': {'input_tokens': 100, 'output_tokens': 30},
                                  'diagnosis': {'summary': 'Synthetic only'}})

    def test_disabled_and_globally_paused_never_call_model(self):
        self.prepare([self.incident()], automatic=False)
        runner = self.runner()
        self.assertEqual(diagnose.dispatch(self.root, runner)['status'], 'disabled')
        settings = json.loads((self.root / 'sync-config.json').read_text())
        settings['enabled'] = False
        settings['exception_diagnosis'].update(mode='automatic', automatic_ai_enabled=True)
        sync.atomic_json(self.root / 'sync-config.json', settings)
        self.assertEqual(diagnose.dispatch(self.root, runner)['status'], 'disabled')
        runner.assert_not_called()

    def test_healthy_retry_and_project_assignment_never_call_model(self):
        self.prepare()
        runner = self.runner()
        diagnose.dispatch(self.root, runner)
        self.queue([self.incident(action='retry-next-run', severity='retry'),
                    self.incident('candidate', kind='project-association', action='ask-user-project')])
        self.assertEqual(diagnose.dispatch(self.root, runner)['status'], 'idle')
        runner.assert_not_called()

    def test_disabled_source_with_stale_queue_never_calls_model(self):
        self.prepare([self.incident()]); runner = self.runner()
        settings = json.loads((self.root / 'sync-config.json').read_text())
        settings['sources'][0]['enabled'] = False
        sync.atomic_json(self.root / 'sync-config.json', settings)
        self.assertEqual(diagnose.dispatch(self.root, runner)['status'], 'idle')
        runner.assert_not_called()

    def test_budget_uses_rolling_24_hours_not_calendar_day(self):
        self.prepare([self.incident(str(i)) for i in range(5)])
        runner = self.runner()
        with patch.object(diagnose, 'timestamp', return_value='2026-10-08T23:59:00+00:00'):
            for _ in range(4):
                diagnose.dispatch(self.root, runner)
        with patch.object(diagnose, 'timestamp', return_value='2026-10-09T00:01:00+00:00'):
            self.assertEqual(diagnose.dispatch(self.root, runner)['status'], 'daily-limit')
        with patch.object(diagnose, 'timestamp', return_value='2026-10-10T00:01:00+00:00'):
            self.assertEqual(diagnose.dispatch(self.root, runner)['attempts'], 1)

    def test_same_episode_is_called_once_despite_new_receipt_and_count(self):
        self.prepare([self.incident()])
        runner = self.runner()
        result = diagnose.dispatch(self.root, runner)
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(Path(result['report']).stat().st_mode & 0o777, 0o600)
        self.queue([self.incident(last_receipt='new.json', consecutive_failures=8)])
        self.assertEqual(diagnose.dispatch(self.root, runner)['status'], 'idle')
        self.assertEqual(runner.call_count, 1)

    def test_recovery_then_recurrence_and_different_error_are_new_episodes(self):
        self.prepare([self.incident()]); runner = self.runner()
        diagnose.dispatch(self.root, runner)
        self.queue([]); diagnose.dispatch(self.root, runner)
        self.queue([self.incident(first_receipt='recurrence.json')]); diagnose.dispatch(self.root, runner)
        self.queue([self.incident(first_receipt='recurrence.json', error_type='ValueError')])
        diagnose.dispatch(self.root, runner)
        self.assertEqual(runner.call_count, 3)

    def test_failed_attempt_does_not_automatically_retry(self):
        self.prepare([self.incident()]); runner = Mock(side_effect=RuntimeError('synthetic'))
        self.assertEqual(diagnose.dispatch(self.root, runner)['status'], 'failed')
        self.assertEqual(diagnose.dispatch(self.root, runner)['status'], 'idle')
        self.assertEqual(runner.call_count, 1)

    def test_crash_after_claim_never_double_charges(self):
        self.prepare([self.incident()]); runner = Mock(side_effect=KeyboardInterrupt)
        with self.assertRaises(KeyboardInterrupt):
            diagnose.dispatch(self.root, runner)
        self.assertEqual(diagnose.dispatch(self.root, runner)['status'], 'idle')
        self.assertEqual(runner.call_count, 1)

    def test_daily_limit_and_one_call_per_run(self):
        self.prepare([self.incident(str(i)) for i in range(6)])
        runner = self.runner()
        for _ in range(4):
            self.assertEqual(diagnose.dispatch(self.root, runner)['attempts'], 1)
        self.assertEqual(diagnose.dispatch(self.root, runner)['status'], 'daily-limit')
        self.assertEqual(runner.call_count, 4)

    def test_lock_prevents_concurrent_diagnosis(self):
        self.prepare([self.incident()])
        with diagnose.diagnosis_lock(self.root):
            self.assertEqual(diagnose.maybe_dispatch(self.root)['status'], 'already-running')

    def test_prompt_omits_source_body_paths_titles_and_raw_error(self):
        source = self.base / 'original.txt'; source.write_text('PRIVATE FIXTURE BODY')
        settings = {'sources': [{'conversation_id': 'source-1', 'kind': 'codex',
                                'path': str(source), 'title': 'PRIVATE FIXTURE TITLE'}]}
        payload = diagnose.diagnosis_payload(self.incident(detail='PRIVATE FIXTURE ERROR'), settings)
        encoded = json.dumps(payload)
        self.assertNotIn('PRIVATE', encoded)
        self.assertNotIn(str(source), encoded)
        self.assertNotIn('source-1', encoded)
        self.assertTrue(payload['file_observation']['exists'])

    def test_cli_has_readonly_no_tools_ephemeral_flags(self):
        args = diagnose.cli_arguments({'codex_executable': '/synthetic/codex'}, self.base,
                                      self.base/'schema', self.base/'output')
        self.assertIn('--ephemeral', args)
        self.assertEqual(args[args.index('--sandbox')+1], 'read-only')
        self.assertIn('approval_policy="never"', args)
        self.assertIn('web_search="disabled"', args)
        self.assertIn('mcp_servers={}', args)
        for feature in ('shell_tool', 'apps', 'plugins', 'hooks', 'memories', 'multi_agent'):
            self.assertEqual(args[args.index(feature)-1], '--disable')

    def test_symlink_queue_is_rejected_without_model_call(self):
        self.prepare([self.incident()])
        queue = self.root/'sync/health/pending.json'
        moved = self.base/'moved.json'; queue.rename(moved); queue.symlink_to(moved)
        runner = self.runner()
        with self.assertRaises(Exception):
            diagnose.dispatch(self.root, runner)
        runner.assert_not_called()

    def test_sync_cli_escalates_then_dispatches_once_after_releasing_writer_lock(self):
        self.prepare()
        settings = json.loads((self.root / 'sync-config.json').read_text())
        settings.update(schema_version='ai-context-auto-sync/1.0', codex_discovery_roots=[], workspace_routes=[])
        settings['sources'] = [{'conversation_id': 'source-1', 'enabled': True, 'kind': 'codex',
            'provider': 'codex', 'title': 'Synthetic unavailable source', 'projects': ['alpha'],
            'path': str(self.base / 'missing.jsonl')}]
        sync.atomic_json(self.root / 'sync-config.json', settings)
        def runner(options, payload):
            with sync.locked(self.root):
                pass
            self.assertFalse(payload['file_observation']['exists'])
            return {'status': 'completed', 'diagnosis': {'summary': 'Synthetic missing file'}}
        with patch.object(diagnose, 'run_cli', side_effect=runner) as model, \
                patch('sys.argv', ['auto_sync.py', '--root', str(self.root), 'sync-files']), \
                patch('sys.stdout', new_callable=io.StringIO):
            self.assertEqual(sync.main(), 1)
            self.assertEqual(model.call_count, 0)
            self.assertEqual(sync.main(), 1)
            self.assertEqual(model.call_count, 1)
            self.assertEqual(sync.main(), 1)
            self.assertEqual(model.call_count, 1)


if __name__ == '__main__':
    unittest.main()
