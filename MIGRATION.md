# Migration to SaveState v3

## From the earlier Markdown-only SaveState package

Do not overwrite an existing `.savestate/` without a backup.

1. Commit or copy the existing `.savestate/` directory.
2. Install the v3 skill under `.claude/skills/save-state/`.
3. Preserve the old state as a reference snapshot.
4. Ask Claude to create a new v3 SaveState by reading:
   - old `CURRENT.md`;
   - vision and goals;
   - technical state and lessons;
   - active stage and phase files;
   - relevant logs and Git history.
5. Use one initial v3 delta to seed canonical structured data.
6. Run `doctor`.

V3 deliberately does not guess at arbitrary legacy Markdown structure. Semantic migration should preserve user intent and phase evidence rather than perform a lossy automatic parse.

## From the alternative delta-engine implementation

Retain useful Markdown and history as migration sources, but do not reuse its metadata directly.

V3 changes important semantics:

- sparse handoff fields are genuinely preserved;
- dry run is non-writing;
- invalid saves are transactionally discarded;
- pending notes resolve by exact ID;
- indexes rebuild from canonical JSON;
- repository hooks are not bypassed;
- session boundaries do not use idle-time guesses.
