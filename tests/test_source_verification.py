"""Verify independent source replay, not merely a derived-file checksum."""
import test_context_library as fixtures
import context_library as lib
import adapters
import source_verification
import unittest
from pathlib import Path
import json


class SourceVerificationTests(unittest.TestCase):
    setUp = fixtures.ArchiveTests.setUp
    tearDown = fixtures.ArchiveTests.tearDown
    payload = fixtures.ArchiveTests.payload
    source = fixtures.ArchiveTests.source
    event = fixtures.ArchiveTests.event
    meta = fixtures.ArchiveTests.meta

    def test_structured_source_replay_survives_original_deletion(self):
        path=self.base/'structured.json'
        document={'schema_version':'ai-context-transcript/2.0','provider':'example','conversation_id':'c','title':'synthetic',
                  'exported_at':'2026-10-08T00:00:00Z','branch_selection':'left',
                  'completeness':{'complete':False,'missing_attachments':['asset-a']},
                  'records':[{'id':'m1','role':'user','content':'exact visible text','parent_id':None,'children':['m2'],'branch_id':'left'}]}
        path.write_text(json.dumps(document))
        payload=adapters.parse_transcript(path,'example','c','synthetic')
        result=lib.publish(self.root,'alpha',payload)
        path.unlink()
        report=source_verification.verify_source(Path(result['path']),'alpha')
        self.assertEqual(report['status'],'verified-source-replay')
        self.assertEqual(len(report['coverage']),1)

    def test_codex_preserved_public_rows_replay(self):
        path=self.source([self.meta(),self.event('UserMessage','u1',[{'type':'text','text':'hello'}]),
                          self.event('AgentMessage','a1',[{'type':'Text','text':'answer'}],'final_answer')])
        payload=adapters.parse_codex(path)
        result=lib.publish(self.root,'alpha',payload)
        path.unlink()
        self.assertEqual(source_verification.verify_source(Path(result['path']),'alpha')['record_count'],2)

    def test_redacted_source_replays_redacted_records(self):
        path=self.base/'plain.txt';path.write_text('synthetic '+'sk-'+'a'*32)
        result=lib.publish(self.root,'alpha',adapters.parse_transcript(path,'example','c','title'))
        self.assertEqual(source_verification.verify_source(Path(result['path']),'alpha')['status'],'verified-source-replay')

    def test_missing_source_artifact_is_not_reported_as_verified(self):
        result=lib.publish(self.root,'alpha',self.payload())
        self.assertEqual(source_verification.verify_source(Path(result['path']),'alpha')['status'],'unavailable')
