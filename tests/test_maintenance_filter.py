"""Synthetic app envelopes, source boundaries, and idle polling regressions."""
import json
import unittest
from unittest.mock import patch

import test_context_library as fixtures
import auto_sync as sync
import conversation_catalog as catalog


def row(text, kind='response_item', role='user'):
    if kind == 'user_message':
        return {'type': 'event_msg', 'payload': {'type': kind, 'message': text}}
    if kind == 'item_completed':
        return {'type': 'event_msg', 'payload': {'type': kind, 'item': {
            'type': 'UserMessage', 'content': [{'type': 'text', 'text': text}]}}}
    return {'type': 'response_item', 'metadata': {'client_authored': True},
            'payload': {'type': 'message', 'role': role,
            'content': [{'type': 'input_text' if role == 'user' else 'output_text', 'text': text}]}}


def encoded(*rows):
    return b''.join(json.dumps(value).encode() + b'\n' for value in rows)


def heartbeat(marker=sync.MARKER):
    return ('<heartbeat>\n  <automation_id>fixture</automation_id>\n'
            '  <current_time_iso>2026-10-09T00:00:00Z</current_time_iso>\n'
            '  <instructions>\n' + marker + '\nRefresh approved sources.\n'
            '  </instructions>\n</heartbeat>')


ENVIRONMENT = '<environment_context>\n<cwd>/synthetic/project</cwd>\n</environment_context>'


class MaintenanceBoundaryTests(unittest.TestCase):
    def test_actual_envelope_forms_exclude_idle_tail(self):
        prefix = encoded(row('Project question'), row('Project answer', role='assistant'))
        for kind in ('response_item', 'user_message', 'item_completed'):
            with self.subTest(kind=kind):
                data = prefix + encoded(row(heartbeat(), kind), row(ENVIRONMENT, kind),
                                        row('Maintenance answer', role='assistant'), row(heartbeat(), kind))
                self.assertEqual(sync.before_automation_tail(data), prefix)

    def test_duplicate_event_and_response_envelopes_keep_first_cutoff(self):
        prefix = encoded(row('Project question'))
        data = prefix + encoded(row(heartbeat(), 'user_message'), row(heartbeat()),
                                row(ENVIRONMENT), row('Done', role='assistant'))
        self.assertEqual(sync.before_automation_tail(data), prefix)

    def test_human_resume_includes_intervening_history_without_rewriting_it(self):
        data = encoded(row('Question'), row(heartbeat()), row(ENVIRONMENT),
                       row('Maintenance answer', role='assistant'), row('New human request'),
                       row('New answer', role='assistant'))
        self.assertEqual(sync.before_automation_tail(data), data)
        self.assertEqual(sync.before_automation_tail(data + encoded(row(heartbeat()))), data)

    def test_quotes_inline_mentions_other_jobs_and_marker_prefix_are_kept(self):
        for text in ('Please explain ' + heartbeat(), '````\n' + heartbeat() + '\n````',
                     heartbeat('OTHER_JOB'), sync.MARKER + '_OTHER\nnot this job',
                     'Discuss ' + sync.MARKER, '<heartbeat>malformed</heartbeat>'):
            with self.subTest(text=text):
                data = encoded(row(text))
                self.assertEqual(sync.before_automation_tail(data), data)
                self.assertFalse(sync.maintenance_message(text))

    def test_assistant_and_tool_markers_never_cut_history(self):
        data = encoded(row('Question'), row(heartbeat(), role='assistant'),
                       {'type': 'response_item', 'payload': {
                           'type': 'function_call_output', 'output': heartbeat()}})
        self.assertEqual(sync.before_automation_tail(data), data)

    def test_environment_with_added_human_request_reopens_tail(self):
        data = encoded(row('Question'), row(heartbeat()), row(ENVIRONMENT + '\nPlease continue.'))
        self.assertEqual(sync.before_automation_tail(data), data)

    def test_partial_tail_does_not_reopen_existing_boundary(self):
        prefix = encoded(row('Question'))
        partial = b'{"type":"response_item","payload":'
        self.assertEqual(sync.before_automation_tail(prefix + encoded(row(heartbeat())) + partial), prefix)
        self.assertEqual(sync.before_automation_tail(prefix + partial), prefix + partial)


class IdleSyncTests(unittest.TestCase):
    setUp = fixtures.ArchiveTests.setUp
    tearDown = fixtures.ArchiveTests.tearDown

    def setup_source(self):
        path = self.base / 'rollout-synthetic.jsonl'
        path.write_bytes(encoded({'type': 'session_meta', 'payload': {
            'id': 'idle-1', 'source': 'cli', 'cwd': str(self.base)}}, row('Human request')))
        sync.atomic_json(self.root / 'sync-config.json', {
            'schema_version': 'ai-context-auto-sync/1.0', 'enabled': True,
            'heartbeat_conversation_id': 'idle-1', 'codex_discovery_roots': [],
            'workspace_routes': [], 'sources': [{'kind': 'codex', 'provider': 'codex',
            'conversation_id': 'idle-1', 'path': str(path), 'title': 'Synthetic idle',
            'projects': ['alpha'], 'enabled': True}]})
        return path

    def test_repeated_wrapped_maintenance_creates_no_revision_and_never_edits_source(self):
        path = self.setup_source()
        self.assertEqual(sync.sync_files(self.root)['sources'][0]['status'], 'imported')
        old_path, _ = catalog.enumerate_catalog(self.root)[0]
        old_bytes = (old_path / 'messages.jsonl').read_bytes()
        for _ in range(3):
            with path.open('ab') as out:
                out.write(encoded(row(heartbeat()), row(ENVIRONMENT), row('Maintenance', role='assistant')))
            before = path.read_bytes()
            self.assertEqual(sync.sync_files(self.root)['sources'][0]['status'], 'unchanged-visible-content')
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(len(catalog.enumerate_catalog(self.root)), 1)
        self.assertEqual((old_path / 'messages.jsonl').read_bytes(), old_bytes)
        self.assertEqual(sync.sync_files(self.root)['sources'][0]['status'], 'unchanged-file')

    def test_old_checkpoint_rechecks_unchanged_source_once_after_filter_upgrade(self):
        self.setup_source()
        sync.sync_files(self.root)
        state = sync.read_state(self.root / 'sync')
        state['sources'][sync.key_for('codex', 'idle-1')].pop('maintenance_policy')
        sync.atomic_json(self.root / 'sync' / 'state.json', state)
        with patch.object(sync, 'file_payload', wraps=sync.file_payload) as parse:
            sync.sync_files(self.root)
            self.assertEqual(parse.call_count, 1)
            sync.sync_files(self.root)
            self.assertEqual(parse.call_count, 1)


if __name__ == '__main__':
    unittest.main()
