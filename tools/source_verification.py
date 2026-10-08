"""Reparse preserved visible source without trusting a summary or external path."""
from pathlib import Path
import json
from context_library import LibraryError


class SourceReplayError(LibraryError):
    """Intact stored files whose retained source cannot reproduce the records."""


def verify_source(path: Path, project=None):
    import context_library as lib
    manifest, stored = lib.validate_revision(path, project)
    if not manifest.get('source_artifact'):
        return {'status': 'unavailable', 'revision': manifest['revision'],
                'reason': 'Legacy revision has external provenance only; no self-contained source artifact.'}
    artifact = lib.read_json(path / 'source-artifact.json')
    adapter = lib.load_adapters()
    try:
        payload = adapter.replay_source_artifact(artifact, manifest['provider'],
                                                 manifest['conversation_id'], manifest.get('title') or '')
    except Exception as exc:
        raise SourceReplayError(f'Preserved source could not be replayed ({type(exc).__name__}); source contents were not printed.') from None
    regenerated_manifest, regenerated = lib.prepare_payload(payload, None, manifest.get('title'))
    def visible(record):
        # Original byte offsets and paths remain in the stored records; replay
        # uses the preserved copy and may have changed length after redaction.
        return {key: value for key, value in record.items() if key != 'source_refs'}
    if [visible(record) for record in stored] != [visible(record) for record in regenerated]:
        raise SourceReplayError('Source replay does not reproduce the archived visible messages and structure.')
    for key in ('conversation_metadata', 'exported_at', 'source_declared_completeness'):
        if manifest.get(key) != regenerated_manifest.get(key):
            raise SourceReplayError('Source replay does not reproduce the archived source metadata.')
    coverage = [{'record_id': record['id'], 'source_refs': record['source_refs'],
                 'visible_record_sha256': lib.digest(lib.canonical_bytes(visible(record)))} for record in stored]
    source_coverage = []
    if artifact['format'] == 'ai-context-codex-visible-source/1.0':
        for entry in artifact['content']['source_rows']:
            source_ref = entry['source_ref']
            ids = [record['id'] for record in stored if any(
                ref.get('line') == source_ref.get('line') and ref.get('byte_start') == source_ref.get('byte_start')
                for ref in record['source_refs'])]
            if not ids:
                raise SourceReplayError('A preserved public source row has no archived message mapping.')
            source_coverage.append({'source_ref': source_ref, 'record_ids': ids, 'status': 'mapped'})
    elif artifact['format'] in {'ai-context-transcript/1.0', 'ai-context-transcript/2.0'}:
        document = json.loads(artifact['content'])
        for index, source_record in enumerate(document['records']):
            ids = [record['id'] for record in stored if any(ref.get('record_index') == index for ref in record['source_refs'])]
            if not ids:
                raise SourceReplayError('A declared visible source record has no archived message mapping.')
            source_coverage.append({'record_index': index, 'source_id': source_record['id'], 'record_ids': ids, 'status': 'mapped'})
    elif artifact['format'] == 'ai-context-read-thread/1.0':
        document = json.loads(artifact['content'])
        for page_index, page in enumerate(document['pages']):
            for turn_index, turn in enumerate(page['turns']):
                for item_index, item in enumerate(turn['items']):
                    ids = [record['id'] for record in stored if any(
                        (ref.get('page_index'), ref.get('turn_index'), ref.get('item_index')) == (page_index, turn_index, item_index)
                        for ref in record['source_refs'])]
                    if not ids and item.get('type') in {'userMessage', 'agentMessage'}:
                        raise SourceReplayError('A public API message has no archived message mapping.')
                    source_coverage.append({'page_index': page_index, 'turn_index': turn_index, 'item_index': item_index,
                                            'record_ids': ids, 'status': 'mapped' if ids else 'unparsed-public-item'})
    else:
        source_coverage = [{'source_document': True, 'record_ids': [record['id'] for record in stored], 'status': 'mapped'}]
    return {'status': 'verified-source-replay', 'revision': manifest['revision'],
            'record_count': len(stored), 'coverage': coverage, 'source_coverage': source_coverage,
            'scope': 'All archived visible records and retained metadata match the preserved source projection.',
            'limitations': ['This verifies extraction from the preserved permitted source, not absent platform history.',
                            'Private engine context and source omissions remain outside the preserved source scope.']}
