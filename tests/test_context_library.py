"""Synthetic integrity, source-boundary, and regression checks; no private data."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import adapters
import context_library as lib


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='context-test-', dir=Path(tempfile.gettempdir()).resolve())
        self.base = Path(self.temp.name)
        self.root = self.base / 'library'
        self.root.mkdir(mode=0o700)
        self.memory = self.base / 'memory'
        for name in ('alpha', 'beta'):
            p = self.memory / 'projects' / name
            p.mkdir(parents=True)
            (p / 'project-manifest.json').write_text(json.dumps({'schema_version': '2.0', 'canonical_name': name}))
        (self.root / 'library.json').write_text(json.dumps({'memory_root': str(self.memory)}))

    def tearDown(self):
        self.temp.cleanup()

    def payload(self, text='原话\n第二行，不压缩。'):
        return {'provider': 'example', 'conversation_id': 'chat-1', 'title': '示例',
                'records': [{'id': 'm1', 'role': 'user', 'channel': 'message', 'timestamp': None,
                             'content': text, 'source_refs': [{'path': '/original', 'line': 1}]}],
                'source': {'path': '/original', 'snapshot_sha256': 'a' * 64},
                'completeness': {'complete': False, 'scope': 'snapshot'}}

    def source(self, rows, tail=b''):
        path = self.base / 'rollout.jsonl'
        raw = b''.join((json.dumps(r, ensure_ascii=False) + '\n').encode() for r in rows) + tail
        path.write_bytes(raw)
        return path

    def event(self, kind, ident, content, phase=None):
        item = {'type': kind, 'id': ident, 'content': content}
        if phase:
            item['phase'] = phase
        return {'type': 'event_msg', 'timestamp': '2026-10-08T00:00:00Z',
                'payload': {'type': 'item_completed', 'thread_id': 'chat-1', 'item': item}}

    def meta(self):
        return {'type': 'session_meta', 'payload': {'id': 'chat-1'}}

    def test_roundtrip_exact_text_and_private_modes(self):
        data = self.payload()
        result = lib.publish(self.root, 'alpha', data)
        path = Path(result['path'])
        manifest, records = lib.validate_revision(path, 'alpha')
        self.assertEqual(records[0]['content'], data['records'][0]['content'])
        self.assertFalse(manifest['completeness']['overall_complete'])
        for file in path.iterdir():
            self.assertEqual(file.stat().st_mode & 0o777, 0o600)
        self.assertEqual(path.stat().st_mode & 0o777, 0o700)

    def test_repeated_import_deduplicates(self):
        first = lib.publish(self.root, 'alpha', self.payload())
        second = lib.publish(self.root, 'alpha', self.payload())
        self.assertEqual(second['status'], 'deduplicated')
        self.assertEqual(first['path'], second['path'])

    def test_structured_records_roundtrip_after_json_key_sorting(self):
        payload = self.payload()
        payload['records'][0]['content'] = {'z': [{'type': 'text', 'text': '中文\n逐字保留'}], 'a': {'output': '工具原文', 'call_id': 'c1'}}
        result = lib.publish(self.root, 'alpha', payload)
        manifest, records = lib.validate_revision(Path(result['path']), 'alpha')
        self.assertEqual(records[0]['content'], payload['records'][0]['content'])
        self.assertEqual(lib.publish(self.root, 'alpha', payload)['status'], 'deduplicated')

    def test_bad_generated_view_is_not_published(self):
        actual = lib.transcript_bytes
        calls = [0]
        def unstable(manifest, records):
            calls[0] += 1
            return actual(manifest, records) + (b'BAD' if calls[0] == 1 else b'')
        with patch.object(lib, 'transcript_bytes', side_effect=unstable):
            with self.assertRaises(lib.LibraryError):
                lib.publish(self.root, 'alpha', self.payload())
        self.assertEqual(lib.enumerate_revisions(self.root, 'alpha'), [])

    def test_changed_record_new_revision_preserves_old(self):
        first = lib.publish(self.root, 'alpha', self.payload())
        original = (Path(first['path']) / 'messages.jsonl').read_bytes()
        second = lib.publish(self.root, 'alpha', self.payload('新的原话'))
        self.assertNotEqual(first['revision'], second['revision'])
        self.assertEqual((Path(first['path']) / 'messages.jsonl').read_bytes(), original)

    def test_project_isolation_and_unknown_project(self):
        a = lib.publish(self.root, 'alpha', self.payload())
        b = lib.publish(self.root, 'beta', self.payload())
        self.assertEqual(a['path'], b['path'])
        self.assertEqual(len(lib.enumerate_revisions(self.root, 'alpha')), 1)
        self.assertEqual(len(lib.enumerate_revisions(self.root, 'beta')), 1)
        for name in ('../alpha', '_template', 'unknown'):
            with self.assertRaises(lib.LibraryError):
                lib.publish(self.root, name, self.payload())

    def test_redaction_does_not_touch_source(self):
        secret = 'sk-' + 'a' * 32
        text = 'public ' + secret
        payload = self.payload(text)
        result = lib.publish(self.root, 'alpha', payload)
        manifest, records = lib.validate_revision(Path(result['path']), 'alpha')
        self.assertEqual(payload['records'][0]['content'], text)
        self.assertNotIn(secret, records[0]['content'])
        self.assertEqual(manifest['redaction_count'], 1)

    def test_forbidden_engine_record_not_stored(self):
        payload = self.payload()
        payload['records'].append({'id': 'private', 'role': 'assistant', 'channel': 'analysis', 'content': 'EXCLUDED_SENTINEL'})
        result = lib.publish(self.root, 'alpha', payload)
        self.assertNotIn('EXCLUDED_SENTINEL', (Path(result['path']) / 'messages.jsonl').read_text())

    def test_tampered_file_detected(self):
        result = lib.publish(self.root, 'alpha', self.payload())
        path = Path(result['path'])
        with (path / 'messages.jsonl').open('ab') as f:
            f.write(b'{}\n')
        with self.assertRaises(lib.LibraryError):
            lib.validate_revision(path, 'alpha')

    def test_list_reads_metadata_only(self):
        lib.publish(self.root, 'alpha', self.payload())
        with patch.object(lib, 'read_records', side_effect=AssertionError('body loaded')):
            self.assertEqual(len(lib.enumerate_revisions(self.root, 'alpha')), 1)

    def test_symlink_source_rejected(self):
        target = self.base / 'plain.txt'
        target.write_text('text')
        alias = self.base / 'alias.txt'
        alias.symlink_to(target)
        with self.assertRaises(adapters.AdapterError):
            adapters.parse_transcript(alias, 'example', 'chat', 'title')

    def test_modern_messages_questions_no_response_duplicates(self):
        user = self.event('UserMessage', 'u1', [{'type': 'text', 'text': '问题'}])
        assistant = self.event('AgentMessage', 'a1', [{'type': 'Text', 'text': '答复'}], 'final_answer')
        question = self.event('AgentMessage', 'q1', [], 'commentary')
        question['payload']['item'].update(questions=[{'title': '选项?', 'options': ['A', 'B']}], delivery='async')
        duplicate = {'type': 'response_item', 'payload': {'type': 'message', 'role': 'user', 'content': [{'text': '问题'}]}}
        data = adapters.parse_codex(self.source([self.meta(), user, duplicate, assistant, question]))
        self.assertEqual([r['id'] for r in data['records']], ['u1', 'a1', 'q1'])
        self.assertEqual(data['records'][-1]['content']['questions'][0]['title'], '选项?')

    def test_tool_output_structure_and_visible_named_fields(self):
        output = [{'type': 'text', 'text': 'tool result'}, {'analysis': 'visible report', 'summary_text': 'visible summary', 'role': 'system'}]
        row = {'type': 'response_item', 'payload': {'type': 'custom_tool_call_output', 'id': 't1', 'call_id': 'c1', 'output': output}}
        data = adapters.parse_codex(self.source([self.meta(), row]))
        self.assertEqual(data['records'][0]['content']['output'], output)

    def test_private_engine_message_excluded(self):
        hidden = self.event('AgentMessage', 'hidden', [{'type': 'Text', 'text': 'PRIVATE_SENTINEL'}], 'analysis')
        visible = self.event('UserMessage', 'u1', [{'type': 'text', 'text': 'visible'}])
        data = adapters.parse_codex(self.source([self.meta(), hidden, visible]))
        self.assertEqual([r['id'] for r in data['records']], ['u1'])

    def test_partial_tail_is_reported_not_parsed(self):
        row = self.event('UserMessage', 'u1', [{'type': 'text', 'text': 'whole'}])
        data = adapters.parse_codex(self.source([self.meta(), row], b'{"partial":'))
        self.assertEqual(data['source']['trailing_bytes_ignored'], 11)
        self.assertEqual(len(data['records']), 1)

    def test_malformed_complete_line_fails(self):
        path = self.source([self.meta()], b'{bad}\n')
        with self.assertRaises(adapters.AdapterError):
            adapters.parse_codex(path)

    def test_duplicate_json_key_fails(self):
        path = self.source([], b'{"type":"x","type":"y"}\n')
        with self.assertRaises(adapters.AdapterError):
            adapters.parse_codex(path)

    def test_compaction_item_counted(self):
        data = adapters.parse_codex(self.source([self.meta(), self.event('ContextCompaction', 'compact', [])]))
        self.assertEqual(data['completeness']['compaction_event_count'], 1)

    def test_message_revision_repeat_coalesces(self):
        data = adapters.parse_codex(self.source([self.meta(), self.event('AgentMessage', 'a1', [{'text': 'A'}]), self.event('AgentMessage', 'a1', [{'text': 'B'}]), self.event('AgentMessage', 'a1', [{'text': 'B'}])]))
        self.assertEqual(len(data['records']), 2)
        self.assertEqual(len({r['id'] for r in data['records']}), 2)

    def test_media_is_preserved_in_canonical_records(self):
        encoded = 'data:image/png;base64,QUJDRA=='
        row = self.event('UserMessage', 'u1', [{'type': 'image', 'image_url': encoded}])
        data = adapters.parse_codex(self.source([self.meta(), row]))
        self.assertIn(encoded, json.dumps(data))
        self.assertGreater(data['completeness']['attachments']['inline_payloads_preserved'], 0)
        self.assertFalse(data['completeness']['attachments']['external_files_copied'])

    def test_media_roundtrip_and_display_projection(self):
        payload = self.payload()
        encoded = 'data:image/png;base64,QUJDRA=='
        payload['records'][0]['content'] = [
            {'type': 'image', 'data': 'QUJDRA=='},
            {'type': 'image', 'image_url': encoded},
            {'type': 'text', 'text': '普通原文，包括 ' + encoded + ' 也应逐字保留。'},
        ]
        result = lib.publish(self.root, 'alpha', payload)
        manifest, records = lib.validate_revision(Path(result['path']), 'alpha')
        self.assertEqual(records[0]['content'], payload['records'][0]['content'])
        self.assertEqual(manifest['completeness']['inline_media_preserved'], 2)
        view = (Path(result['path']) / 'transcript.md').read_text()
        self.assertIn('inline_media_display_omitted', view)
        self.assertIn('普通原文，包括 ' + encoded, view)
        self.assertEqual(lib.publish(self.root, 'alpha', payload)['status'], 'deduplicated')

    def test_fake_media_credentials_are_redacted(self):
        payload = self.payload()
        secret = 'sk-' + 'a' * 32
        payload['records'][0]['content'] = {'type': 'image', 'data': secret}
        result = lib.publish(self.root, 'alpha', payload)
        manifest, records = lib.validate_revision(Path(result['path']), 'alpha')
        self.assertNotIn(secret, json.dumps(records))
        self.assertEqual(manifest['redaction_count'], 1)
        self.assertEqual(manifest['completeness']['inline_media_preserved'], 0)

    def test_old_manifest_view_keeps_reference_marker(self):
        manifest, records = lib.prepare_payload(self.payload(), 'alpha', None)
        del manifest['completeness']['inline_media_preserved']
        records[0]['content'] = {'omitted_inline_media': True, 'sha256': 'a' * 64}
        view = lib.transcript_bytes(manifest, records).decode()
        self.assertIn('Original attachments are not copied.', view)
        self.assertIn('omitted_inline_media', view)
        self.assertNotIn('inline_media_display_omitted', view)

    def test_plaintext_exact_import(self):
        path = self.base / 'chat.txt'
        path.write_bytes('用户：一\r\n助手：二\n'.encode())
        data = adapters.parse_transcript(path, 'other', 'chat', 'title')
        self.assertEqual(data['records'][0]['content']['text'], '用户：一\r\n助手：二\n')

    def test_normalized_json_and_native_json_rejection(self):
        path = self.base / 'chat.json'
        obj = {'schema_version': 'ai-context-transcript/1.0', 'provider': 'chatgpt', 'conversation_id': 'chat-1', 'title': 'Title', 'records': self.payload()['records']}
        path.write_text(json.dumps(obj, ensure_ascii=False))
        data = adapters.parse_transcript(path, 'chatgpt', 'chat-1', 'Title')
        self.assertEqual(len(data['records']), 1)
        with self.assertRaises(adapters.AdapterError):
            adapters.parse_transcript(path, 'other', 'chat-1', 'Title')
        path.write_text(json.dumps({'mapping': {}}))
        with self.assertRaises(adapters.AdapterError):
            adapters.parse_transcript(path, 'chatgpt', 'chat-1', 'Title')


if __name__ == '__main__':
    unittest.main(verbosity=2)
