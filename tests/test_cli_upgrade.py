"""Exercise CLI dispatch for pending capture and explicit reading coverage."""
import test_context_library as fixtures
import context_library as lib
import unittest


class UpgradeCliTests(unittest.TestCase):
    setUp=fixtures.ArchiveTests.setUp
    tearDown=fixtures.ArchiveTests.tearDown
    payload=fixtures.ArchiveTests.payload

    def call(self,*args):
        return lib.run(lib.build_parser().parse_args(['--root',str(self.root),*args]))

    def test_cli_pending_link_and_read_preserve_one_copy(self):
        result=lib.publish(self.root,None,self.payload())
        self.assertEqual(self.call('list','--unassigned')['revision_count'],1)
        self.call('link','--project','alpha','--conversation',result['conversation_key'])
        self.assertEqual(self.call('list','--unassigned')['revision_count'],0)
        self.assertEqual(self.call('read','--project','alpha','--conversation','chat-1')['end'],1)

    def test_cli_review_requires_explicit_read_ack(self):
        lib.publish(self.root,'alpha',self.payload())
        plan=self.call('review-create','--project','alpha','--mode','full')
        ident=plan['review_id']
        self.call('review-fetch','--review',ident,'--conversation','chat-1','--start','1','--end','1')
        self.assertFalse(self.call('review-report','--review',ident)['plan_body_read_complete'])
        self.call('review-ack','--review',ident,'--conversation','chat-1','--start','1','--end','1')
        self.assertTrue(self.call('review-report','--review',ident)['plan_body_read_complete'])

    def test_cli_source_verification_does_not_overclaim_legacy(self):
        lib.publish(self.root,'alpha',self.payload())
        result=self.call('verify-source','--project','alpha','--conversation','chat-1')
        self.assertEqual(result['status'],'unavailable')
