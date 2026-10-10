"""Synthetic metadata-only retry, escalation and incident deduplication."""
import json
import unittest
from unittest.mock import patch

import test_context_library as fixtures
import auto_sync as sync
import sync_health as health


class HealthTests(unittest.TestCase):
    def settings(self, enabled=True):
        return {'enabled': enabled, 'sources': [
            {'conversation_id': 'file-1', 'enabled': True},
            {'conversation_id': 'api-1', 'enabled': True}]}

    def outcome(self, result=None, identity='file-1', error=None, selection=None):
        source = {'conversation_id': identity, 'status': result or 'unchanged-file'}
        if error:
            source.update(status='error', error_type=error)
        if selection:
            source.update(selection=selection, revision='synthetic-revision')
        return {'status': 'errors' if error else 'ok', 'sources': [source]}

    def report(self, *runs, settings=None, state=None):
        return health.build_report([(str(i), r) for i, r in enumerate(runs)],
                                   settings or self.settings(), state or {})

    def test_healthy_run_never_needs_ai(self):
        result = self.report(self.outcome())
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['model_calls'], 0)
        self.assertEqual(result['incidents'], [])

    def test_transient_retry_then_one_persistent_incident(self):
        run = self.outcome(error='FileNotFoundError')
        self.assertEqual(self.report(run)['incidents'][0]['action'], 'retry-next-run')
        result = self.report(run, run, run)
        self.assertEqual(len(result['incidents']), 1)
        self.assertEqual(result['incidents'][0]['action'], 'diagnose')
        self.assertEqual(result['incidents'][0]['consecutive_failures'], 3)

    def test_success_resets_failure_streak(self):
        bad = self.outcome(error='PermissionError')
        result = self.report(bad, bad, self.outcome(), bad)
        self.assertEqual(result['incidents'][0]['consecutive_failures'], 1)
        self.assertEqual(result['status'], 'retry')
        self.assertEqual(self.report(bad, self.outcome())['incidents'], [])

    def test_api_success_does_not_clear_unrelated_file_error(self):
        result = self.report(self.outcome(error='LibraryError'), self.outcome(identity='api-1'))
        self.assertEqual(result['incidents'][0]['conversation_id'], 'file-1')

    def test_verification_error_requires_attention_immediately(self):
        result = self.report(self.outcome(error='SourceReplayError'))
        self.assertEqual(result['incidents'][0]['action'], 'diagnose')

    def test_duplicate_receipt_does_not_inflate_retry(self):
        run = self.outcome(error='TimeoutError')
        result = health.build_report([('same', run), ('same', run)], self.settings(), {})
        self.assertEqual(result['incidents'][0]['consecutive_failures'], 1)

    def test_unchanged_file_does_not_resolve_version_conflict(self):
        conflict = self.outcome(result='imported', selection='explicit-version-required')
        self.assertEqual(self.report(conflict, self.outcome())['incidents'][0]['kind'], 'version-conflict')
        fixed = self.outcome(result='imported', selection='verified-coverage')
        self.assertEqual(self.report(conflict, fixed)['incidents'], [])

    def test_unknown_project_requires_user_and_disappears_after_registration(self):
        state = {'candidates': {'new-1': {'status': 'needs-project-association'}}}
        result = self.report(self.outcome(), state=state)
        self.assertEqual(result['incidents'][0]['action'], 'ask-user-project')
        settings = self.settings()
        settings['sources'].append({'conversation_id': 'new-1', 'enabled': False})
        self.assertEqual(self.report(self.outcome(), settings=settings, state=state)['incidents'], [])

    def test_disabled_source_error_is_not_actionable(self):
        settings = self.settings()
        settings['sources'][0]['enabled'] = False
        self.assertEqual(self.report(self.outcome(error='LibraryError'), settings=settings)['incidents'], [])

    def test_global_pause_preserves_incidents_without_active_status(self):
        result = self.report(self.outcome(error='LibraryError'), settings=self.settings(False))
        self.assertEqual(result['status'], 'paused')
        self.assertEqual(len(result['incidents']), 1)

    def test_global_error_only_clears_after_same_scope_success(self):
        bad = {'status': 'error', 'scope': 'files', 'error_type': 'LibraryError'}
        self.assertEqual(len(self.report(bad, self.outcome(identity='api-1'))['incidents']), 1)
        good = {**self.outcome(), 'newly_registered': []}
        self.assertEqual(self.report(bad, good)['incidents'], [])


class HealthFilesTests(unittest.TestCase):
    setUp = fixtures.ArchiveTests.setUp
    tearDown = fixtures.ArchiveTests.tearDown

    def setup_metadata(self):
        sync.atomic_json(self.root / 'sync-config.json', {
            'schema_version': 'ai-context-auto-sync/1.0', 'enabled': True,
            'sources': [{'conversation_id': 'file-1', 'enabled': True}]})
        folder = self.root / 'sync/runs'
        folder.parent.mkdir(mode=0o700)
        folder.mkdir(mode=0o700)
        return folder

    def test_refresh_deduplicates_attention_without_reading_archive_bodies(self):
        runs = self.setup_metadata()
        sync.atomic_json(runs / '001.json', {'status': 'errors', 'sources': [
            {'conversation_id': 'file-1', 'status': 'error', 'error_type': 'LibraryError'}]})
        with patch.object(sync, 'file_payload', side_effect=AssertionError('No source bodies')):
            self.assertEqual(health.refresh(self.root)['new_attention_count'], 1)
            self.assertEqual(health.refresh(self.root)['new_attention_count'], 0)
        saved = self.root / 'sync/health/pending.json'
        self.assertEqual(saved.stat().st_mode & 0o777, 0o600)
        self.assertEqual(len(json.loads(saved.read_text())['incidents']), 1)

    def test_bad_receipt_is_flagged_without_hiding_valid_incidents(self):
        runs = self.setup_metadata()
        (runs / 'bad.json').write_text('{invalid')
        (runs / 'bad.json').chmod(0o600)
        result = health.refresh(self.root)
        self.assertEqual(result['status'], 'attention')
        self.assertEqual(result['pending_count'], 1)

    def test_same_second_receipts_use_finish_time_not_random_filename_order(self):
        runs = self.setup_metadata()
        sync.atomic_json(runs / '20261009T000000-z.json', {'status': 'errors',
            'finished_at': '2026-10-09T00:00:00.100000+00:00', 'sources': [
            {'conversation_id': 'file-1', 'status': 'error', 'error_type': 'LibraryError'}]})
        sync.atomic_json(runs / '20261009T000000-a.json', {'status': 'ok',
            'finished_at': '2026-10-09T00:00:00.200000+00:00', 'sources': [
            {'conversation_id': 'file-1', 'status': 'unchanged-file'}]})
        self.assertEqual(health.refresh(self.root)['pending_count'], 0)


if __name__ == '__main__':
    unittest.main()
