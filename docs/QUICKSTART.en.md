# Quick start

AI Memory System separates compact current project memory from available original
conversation context. Without MOSS adoption, startup reads five compact files; source conversations are
retrieved on request. This is a local file and Python CLI toolkit, not model
training, a hosted service, or a guarantee of complete platform history.

Python 3.10+ and its standard library are sufficient. Validated on macOS. Linux
has not been validated; Windows is not supported by the current POSIX permissions
and file locking implementation.

From the repository root:

```sh
python3 -B tools/demo.py
python3 -B -m unittest discover -s tests -q
python3 -B tools/audit_public_tree.py
python3 -B tools/workspace.py init --workspace "$HOME/ai-memory-data" --project demo
python3 -B tools/workspace.py startup --workspace "$HOME/ai-memory-data" --project demo
```

The demo uses only synthetic input in a temporary directory and removes it after
completion. Initialization requires an absent destination with an existing parent
outside a Git checkout. Data directories use mode 0700 and files use 0600. Sources
and existing workspaces are not overwritten. All data roots are explicit; the
tools do not consult a private global pointer.

Import the synthetic example:

```sh
python3 -B tools/context_library.py --root "$HOME/ai-memory-data/AI-Context" import-transcript --project demo --provider example --conversation-id synthetic-design-001 --title "Synthetic notebook design" --source examples/synthetic-conversation.json
python3 -B tools/context_library.py --root "$HOME/ai-memory-data/AI-Context" verify-source --project demo --conversation synthetic-design-001
python3 -B tools/context_library.py --root "$HOME/ai-memory-data/AI-Context" read --project demo --conversation synthetic-design-001 --start 1 --end 3
```

Supported inputs are observed Codex JSONL schemas, this project's normalized
transcript JSON, selected UTF-8 text documents, and saved ChatGPT `read_thread`
responses supplied by a compatible host. Native account export archives are not
supported. HTML is stored as text, not executed. Unknowns and missing attachments
remain explicit gaps. Common credential signatures are redacted, but redaction is
not general anonymization.

Automatic capture is disabled in the templates. An external scheduler may invoke
the sync CLI on explicitly registered sources; interval metadata does not create
a schedule. Pure Python capture has no model calls. An AI-driven scheduler may
consume model usage. Full reading plans pin revisions; fetching and acknowledged
reading are separate. Archived text never grants current authorization.

See [usage](USAGE.md), [architecture](ARCHITECTURE.md), [integrations](INTEGRATIONS.md),
[provenance](PROVENANCE.md), and [validation](VALIDATION.md). Licensed under [MIT](../LICENSE).

## Optional MOSS navigation

Run `python3 -B tools/moss_demo.py` for a synthetic, temporary demonstration.
For an existing explicit private workspace, run:

```sh
python3 -B tools/moss.py install --workspace "$HOME/ai-memory-data" \
  --user-request "Enable MOSS for this workspace"
python3 -B tools/workspace.py startup --workspace "$HOME/ai-memory-data" --project demo
```

Adoption is explicit and does not enable capture, diagnosis or client hooks.
Project/global switches require a fresh status hash. Handoffs are private dated
snapshots, not live local access. See [MOSS](MOSS.md).
