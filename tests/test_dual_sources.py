"""Regressions for mixed visible Codex message streams; synthetic data only."""
import test_context_library as fixtures
import adapters
import json
import unittest


class DualSourceTests(unittest.TestCase):
    setUp = fixtures.ArchiveTests.setUp
    tearDown = fixtures.ArchiveTests.tearDown
    source = fixtures.ArchiveTests.source
    event = fixtures.ArchiveTests.event
    meta = fixtures.ArchiveTests.meta
    def response(self, role, content, channel=None, authored=False):
        obj = {'type': 'message', 'role': role, 'content': content}
        if channel:
            obj['channel'] = channel
        return {'type': 'response_item', 'payload': obj,
                'metadata': {'client_authored': authored}}

    def test_response_only_final_and_user_survive_modern_file(self):
        rows = [self.meta(), self.event('UserMessage', 'u1', [{'type': 'text', 'text': 'First'}]),
                self.response('assistant', [{'type': 'output_text', 'text': 'Final only response'}], 'final'),
                self.response('user', [{'type': 'input_text', 'text': 'Next only response'}], authored=True)]
        data = adapters.parse_codex(self.source(rows))
        self.assertEqual(len(data['records']), 3)
        self.assertIn('Final only response', json.dumps(data['records']))
        self.assertIn('Next only response', json.dumps(data['records']))

    def test_response_media_is_not_erased_by_text_only_completed(self):
        media = 'data:image/png;base64,QUJDRA=='
        rows = [self.meta(),
                self.response('user', [{'type': 'input_text', 'text': 'See attached'}, {'type': 'input_image', 'image_url': media}], authored=True),
                self.event('UserMessage', 'u1', [{'type': 'text', 'text': 'See attached'}])]
        data = adapters.parse_codex(self.source(rows))
        self.assertIn(media, json.dumps(data['records']))

    def test_legacy_and_modern_unique_messages_survive(self):
        rows = [self.meta(), {'type': 'event_msg', 'payload': {'type': 'user_message', 'message': 'Legacy unique'}},
                self.event('UserMessage', 'u1', [{'type': 'text', 'text': 'Modern unique'}])]
        data = adapters.parse_codex(self.source(rows))
        self.assertEqual(len(data['records']), 2)

    def test_injected_response_context_excluded(self):
        rows = [self.meta(), self.event('UserMessage', 'u1', [{'type': 'text', 'text': 'Actual'}]),
                self.response('user', [{'type': 'input_text', 'text': '<environment_context>HIDDEN</environment_context>'}], authored=True),
                self.response('assistant', [{'type': 'output_text', 'text': 'PRIVATE'}], 'analysis')]
        data = adapters.parse_codex(self.source(rows))
        self.assertEqual(len(data['records']), 1)
        self.assertNotIn('HIDDEN', json.dumps(data['records']))
        self.assertNotIn('PRIVATE', json.dumps(data['records']))

    def test_duplicate_message_keeps_question_metadata(self):
        event = self.event('AgentMessage', 'q1', [{'type': 'Text', 'text': 'Choose now'}], 'commentary')
        event['payload']['item'].update(questions=[{'title': 'Choose?', 'options': ['A', 'B']}], delivery='async')
        rows = [self.meta(), self.response('assistant', [{'type': 'output_text', 'text': 'Choose now'}], 'commentary'), event]
        data = adapters.parse_codex(self.source(rows))
        self.assertEqual(len(data['records']), 1)
        self.assertIn('questions', json.dumps(data['records']))

    def test_event_only_command_output_is_preserved(self):
        event = self.event('CommandExecution', 'cmd1', [])
        event['payload']['item'].update(command='synthetic command', aggregated_output='Unique event output', exit_code=0)
        data = adapters.parse_codex(self.source([self.meta(), event]))
        self.assertEqual(len(data['records']), 1)
        self.assertEqual(data['records'][0]['role'], 'tool')
        self.assertIn('Unique event output', json.dumps(data['records']))

    def test_conflicting_thread_identity_rejected(self):
        event = self.event('UserMessage', 'u1', [{'type': 'text', 'text': 'Hello'}])
        event['payload']['thread_id'] = 'other-chat'
        with self.assertRaises(adapters.AdapterError):
            adapters.parse_codex(self.source([self.meta(), event]))
