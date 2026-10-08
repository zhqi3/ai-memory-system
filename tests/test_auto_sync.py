"""Synthetic incremental polling, provenance, and maintenance-loop regressions."""
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import test_context_library as fixtures
import context_library as lib
import auto_sync as sync
import conversation_catalog as catalog


class AutoSyncTests(unittest.TestCase):
    setUp = fixtures.ArchiveTests.setUp
    tearDown = fixtures.ArchiveTests.tearDown

    def row(self, text, role='user', ident='m1'):
        if role == 'user':
            return {'type': 'event_msg', 'timestamp': '2026-10-08T00:00:00Z',
                    'payload': {'type': 'item_completed', 'item': {'type': 'UserMessage', 'id': ident, 'content': [{'type': 'text', 'text': text}]}}}
        return {'type': 'response_item', 'timestamp': '2026-10-08T00:00:00Z',
                'payload': {'type': 'message', 'id': ident, 'role': role,
                            'content': [{'type': 'input_text' if role == 'user' else 'output_text', 'text': text}]}}

    def source(self, identity='chat-1', cwd=None, origin='vscode'):
        folder = self.base / 'sessions'
        folder.mkdir(exist_ok=True)
        path = folder / ('rollout-fixture-' + identity + '.jsonl')
        self.write(path, [{'type': 'session_meta', 'payload': {'id': identity, 'source': origin, 'cwd': str(cwd or self.base)}}, self.row('A visible request')])
        return path

    def write(self, path, rows, mode='wb'):
        with path.open(mode) as stream:
            stream.write(b''.join(lib.canonical_bytes(row) + b'\n' for row in rows))

    def settings(self, path=None, **extra):
        sources = [] if path is None else [{'kind': 'codex', 'provider': 'codex', 'conversation_id': 'chat-1', 'path': str(path), 'title': 'Fixture', 'projects': ['alpha'], 'enabled': True}]
        value = {'schema_version': 'ai-context-auto-sync/1.0', 'enabled': True, 'sources': sources,
                 'heartbeat_conversation_id': 'chat-1', 'codex_discovery_roots': [], 'workspace_routes': [], **extra}
        sync.atomic_json(self.root / 'sync-config.json', value)
        return value

    def revisions(self):
        return catalog.enumerate_catalog(self.root)

    def test_first_sync_append_and_repeat_preserve_old(self):
        path = self.source(); self.settings(path)
        first = sync.sync_files(self.root)
        self.assertEqual(first['sources'][0]['status'], 'imported')
        old, manifest = self.revisions()[0]
        old_bytes = (old / 'messages.jsonl').read_bytes()
        self.assertEqual(sync.sync_files(self.root)['sources'][0]['status'], 'unchanged-file')
        self.write(path, [self.row('A visible answer', 'assistant', 'm2')], 'ab')
        second = sync.sync_files(self.root)
        self.assertEqual(second['sources'][0]['record_count'], 2)
        self.assertEqual(len(self.revisions()), 2)
        self.assertEqual((old / 'messages.jsonl').read_bytes(), old_bytes)

    def test_private_runtime_append_does_not_add_visible_revision(self):
        path = self.source(); self.settings(path); sync.sync_files(self.root)
        self.write(path, [{'type': 'response_item', 'payload': {'type': 'reasoning', 'text': 'PRIVATE FIXTURE'}}], 'ab')
        result = sync.sync_files(self.root)
        self.assertEqual(result['sources'][0]['status'], 'unchanged-visible-content')
        self.assertEqual(len(self.revisions()), 1)

    def test_heartbeat_tail_waits_until_next_human_turn(self):
        path = self.source(); self.settings(path)
        self.write(path, [self.row('answer', 'assistant', 'a1')], 'ab')
        sync.sync_files(self.root)
        self.write(path, [self.row(sync.MARKER + '\nRefresh sources', ident='auto1'), self.row('Maintenance result', 'assistant', 'a2')], 'ab')
        result = sync.sync_files(self.root)
        self.assertEqual(result['sources'][0]['status'], 'unchanged-visible-content')
        self.write(path, [self.row(sync.MARKER + '\nRefresh sources', ident='auto2')], 'ab')
        self.assertEqual(sync.sync_files(self.root)['sources'][0]['status'], 'unchanged-visible-content')
        self.write(path, [self.row('Next real question', ident='u2'), self.row('New answer', 'assistant', 'a3')], 'ab')
        before = path.read_bytes()
        result = sync.sync_files(self.root)
        self.assertEqual(result['sources'][0]['status'], 'imported')
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(len(self.revisions()), 2)

    def test_tool_quoted_marker_is_not_a_boundary(self):
        path = self.source()
        self.write(path, [{'type': 'response_item', 'payload': {'type': 'function_call_output', 'call_id': 'c1', 'output': sync.MARKER}}], 'ab')
        self.assertEqual(sync.before_automation_tail(path.read_bytes()), path.read_bytes())

    def test_partial_tail_is_retried_after_completion(self):
        path = self.source(); self.settings(path)
        with path.open('ab') as stream: stream.write(b'{"type":"response_item"')
        sync.sync_files(self.root)
        with path.open('ab') as stream: stream.write(b',"payload":{"type":"message","role":"assistant","id":"a1","content":"tail answer"}}\n')
        self.assertEqual(sync.sync_files(self.root)['sources'][0]['record_count'], 2)

    def test_wrong_conversation_never_advances_checkpoint(self):
        path = self.source('wrong'); self.settings(path)
        result = sync.sync_files(self.root)
        self.assertEqual(result['sources'][0]['status'], 'error')
        self.assertEqual(sync.read_state(self.root / 'sync')['sources'], {})
        self.assertEqual(self.revisions(), [])

    def test_missing_source_does_not_block_other_sources(self):
        path = self.source(); settings = self.settings(path)
        settings['sources'].insert(0, {**settings['sources'][0], 'conversation_id': 'missing', 'path': str(self.base / 'missing.jsonl')})
        sync.atomic_json(self.root / 'sync-config.json', settings)
        result = sync.sync_files(self.root)
        self.assertEqual([r['status'] for r in result['sources']], ['error', 'imported'])

    def test_failed_replay_is_retried_before_checkpoint(self):
        path = self.source(); self.settings(path)
        with patch.object(sync, 'verify_source', side_effect=lib.LibraryError('synthetic verification failure')):
            self.assertEqual(sync.sync_files(self.root)['sources'][0]['status'], 'error')
        self.assertEqual(sync.read_state(self.root / 'sync')['sources'], {})
        with patch.object(sync, 'verify_source', wraps=sync.verify_source) as verifier:
            self.assertEqual(sync.sync_files(self.root)['status'], 'ok')
            self.assertEqual(verifier.call_count, 1)

    def test_concurrent_sync_is_locked(self):
        self.settings()
        with sync.locked(self.root):
            with self.assertRaises(BlockingIOError):sync.sync_files(self.root)

    def test_source_symlink_is_rejected(self):
        path = self.source(); linked = self.base / 'linked.jsonl'; linked.symlink_to(path)
        self.settings(linked)
        self.assertEqual(sync.sync_files(self.root)['sources'][0]['status'], 'error')
        self.assertEqual(self.revisions(), [])

    def test_paused_configuration_writes_no_snapshot(self):
        self.settings(self.source(), enabled=False)
        self.assertEqual(sync.sync_files(self.root), {'status': 'paused'})
        self.assertEqual(self.revisions(), [])

    def test_workspace_discovery_enrolls_only_unique_primary(self):
        workspace = self.base / 'project-alpha'
        self.source('new-primary', workspace)
        self.source('subagent', workspace, {'subagent': {'parent_thread_id': 'new-primary'}})
        self.source('unknown-project')
        self.settings(codex_discovery_roots=[str(self.base / 'sessions')], workspace_routes=[{'path': str(workspace), 'project': 'alpha'}])
        result = sync.sync_files(self.root)
        self.assertEqual(result['newly_registered'], [{'conversation_id': 'new-primary', 'project': 'alpha'}])
        self.assertEqual([r['conversation_id'] for r in result['new_candidates']], ['unknown-project'])
        self.assertEqual(len(self.revisions()), 1)
        self.assertEqual(sync.sync_files(self.root)['new_candidates'], [])

    def test_ambiguous_workspace_only_produces_metadata_candidate(self):
        self.source('ambiguous', self.base)
        self.settings(codex_discovery_roots=[str(self.base / 'sessions')], workspace_routes=[{'path': str(self.base), 'project': p} for p in ['alpha', 'beta']])
        result = sync.sync_files(self.root)
        self.assertEqual(len(result['new_candidates']), 1)
        self.assertEqual(self.revisions(), [])

    def test_archived_source_relocation_keeps_identity(self):
        path = self.source(); self.settings(path, codex_discovery_roots=[str(path.parent)])
        sync.sync_files(self.root)
        moved = path.parent / ('rollout-archived-chat-1.jsonl'); path.rename(moved)
        self.assertIn(sync.sync_files(self.root)['sources'][0]['status'], {'unchanged-file', 'unchanged-visible-content'})
        self.assertEqual(sync.config(self.root)['sources'][0]['path'], str(moved))

    def api_bundle(self, text='Original', more=False):
        return {'schema_version': 'ai-context-read-thread/1.0', 'pages': [{
            'schemaVersion': 1, 'thread': {'id': 'api-1', 'kind': 'chatgpt', 'title': 'Fixture'},
            'turns': [{'id': 'turn-1', 'items': [{'type': 'userMessage', 'id': 'u1', 'content': [{'type': 'text', 'text': text}]}]}],
            'page': {'order': 'newest_first', 'hasMore': more, 'nextCursor': 'cursor' if more else None}, 'attachments': []}]}

    def setup_api(self):
        value = self.settings()
        value['sources'] = [{'kind': 'read_thread', 'provider': 'chatgpt', 'conversation_id': 'api-1', 'title': 'Fixture', 'projects': ['alpha'], 'enabled': True}]
        sync.atomic_json(self.root / 'sync-config.json', value)
        path = self.base / 'api.json'
        path.write_text(json.dumps(self.api_bundle()))
        return path

    def test_api_identical_refresh_deduplicates_but_edit_is_retained(self):
        path = self.setup_api()
        self.assertEqual(sync.import_api(self.root, 'api-1', path)['sources'][0]['status'], 'imported')
        path.write_text(json.dumps(self.api_bundle(), indent=4))
        self.assertEqual(sync.import_api(self.root, 'api-1', path)['sources'][0]['status'], 'unchanged-visible-content')
        path.write_text(json.dumps(self.api_bundle('Edited')))
        result = sync.import_api(self.root, 'api-1', path)
        self.assertEqual(result['sources'][0]['selection'], 'explicit-version-required')
        self.assertEqual(len(self.revisions()), 2)

    def test_api_partial_or_unregistered_capture_is_rejected(self):
        path = self.setup_api(); path.write_text(json.dumps(self.api_bundle(more=True)))
        with self.assertRaises(lib.LibraryError):sync.import_api(self.root, 'api-1', path)
        with self.assertRaises(lib.LibraryError):sync.import_api(self.root, 'not-authorized', path)
        self.assertEqual(self.revisions(), [])

    def test_added_project_link_is_applied_when_source_unchanged(self):
        path = self.source(); settings = self.settings(path); sync.sync_files(self.root)
        settings['sources'][0]['projects'].append('beta'); sync.atomic_json(self.root / 'sync-config.json', settings)
        sync.sync_files(self.root)
        self.assertEqual(len(catalog.enumerate_catalog(self.root, 'beta')), 1)

    def test_registered_external_export_updates(self):
        path = self.base / 'external.json'
        document = {'schema_version': 'ai-context-transcript/2.0', 'provider': 'other-ai',
                    'conversation_id': 'external-1', 'title': 'External fixture',
                    'records': [{'id': 'e1', 'role': 'user', 'content': 'Original'}]}
        path.write_text(json.dumps(document))
        settings = self.settings()
        settings['sources'] = [{'kind': 'transcript', 'provider': 'other-ai', 'conversation_id': 'external-1',
                                'path': str(path), 'title': 'External fixture', 'projects': ['alpha'], 'enabled': True}]
        sync.atomic_json(self.root / 'sync-config.json', settings)
        self.assertEqual(sync.sync_files(self.root)['sources'][0]['status'], 'imported')
        document['records'].append({'id': 'e2', 'role': 'assistant', 'content': 'New original reply'})
        path.write_text(json.dumps(document))
        self.assertEqual(sync.sync_files(self.root)['sources'][0]['record_count'], 2)
        self.assertEqual(len(self.revisions()), 2)

    def test_append_during_capture_does_not_checkpoint_new_file_stat(self):
        path = self.source(); self.settings(path)
        original = sync.file_payload
        def append_after_snapshot(source, settings):
            payload = original(source, settings)
            self.write(path, [self.row('Arrived after snapshot', 'assistant', 'late')], 'ab')
            return payload
        with patch.object(sync, 'file_payload', side_effect=append_after_snapshot):
            self.assertEqual(sync.sync_files(self.root)['status'], 'ok')
        checkpoint = sync.read_state(self.root / 'sync')['sources'][sync.key_for('codex', 'chat-1')]
        self.assertNotIn('file_signature', checkpoint)
        self.assertEqual(sync.sync_files(self.root)['sources'][0]['record_count'], 2)


if __name__ == '__main__':
    unittest.main()
