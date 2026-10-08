"""Source-centric storage regressions; private synthetic fixtures only."""
import test_context_library as fixtures
import context_library as lib
import conversation_catalog as catalog
import unittest
from pathlib import Path


class CatalogTests(unittest.TestCase):
    setUp = fixtures.ArchiveTests.setUp
    tearDown = fixtures.ArchiveTests.tearDown
    payload = fixtures.ArchiveTests.payload

    def test_pending_can_be_saved_then_linked_without_copy(self):
        result = lib.publish(self.root, None, self.payload())
        self.assertEqual(result['assignment'], 'pending')
        self.assertEqual(len(catalog.enumerate_catalog(self.root, unassigned=True)), 1)
        catalog.link_project(self.root, 'alpha', result['conversation_key'])
        catalog.link_project(self.root, 'beta', result['conversation_key'])
        self.assertEqual(catalog.enumerate_catalog(self.root, unassigned=True), [])
        self.assertEqual(lib.enumerate_revisions(self.root, 'alpha')[0][0], Path(result['path']))
        self.assertEqual(lib.enumerate_revisions(self.root, 'beta')[0][0], Path(result['path']))

    def test_unlinked_project_cannot_see_new_conversation(self):
        lib.publish(self.root, 'alpha', self.payload())
        self.assertEqual(lib.enumerate_revisions(self.root, 'beta'), [])

    def test_extended_metadata_and_source_artifact_roundtrip(self):
        payload = self.payload()
        payload['records'][0].update(parent_id=None, children=['m2'], branch_id='left', custom_metadata={'unknown': 3})
        payload.update(conversation_metadata={'selected_branch':'left'}, exported_at='2026-10-08T00:00:00Z', source_declared_completeness={'missing_attachments':['a']}, source_artifact={'format':'synthetic','content':{'visible':'original'}})
        result = lib.publish(self.root, 'alpha', payload)
        manifest, records = lib.validate_revision(Path(result['path']), 'alpha')
        self.assertEqual(records[0]['children'], ['m2'])
        self.assertEqual(records[0]['custom_metadata'], {'unknown':3})
        self.assertEqual(manifest['source_declared_completeness'], payload['source_declared_completeness'])
        self.assertEqual(manifest['conversation_metadata'], payload['conversation_metadata'])
        self.assertTrue((Path(result['path'])/'source-artifact.json').is_file())

    def test_source_artifact_is_redacted_and_tampering_detected(self):
        payload = self.payload()
        credential='sk-'+'z'*32
        payload['source_artifact']={'format':'utf8-text','content':credential}
        result=lib.publish(self.root,None,payload)
        path=Path(result['path'])
        self.assertNotIn(credential,(path/'source-artifact.json').read_text())
        (path/'source-artifact.json').write_text('{}')
        with self.assertRaises(lib.LibraryError):lib.validate_revision(path)
