## SaveState

Use the `save-state` skill only when the user explicitly asks to save, load, check, note, checkpoint, close out, or otherwise persist project state.

Treat `.savestate/CURRENT.md` as the authoritative rolling handoff for a cold start. Do not begin work merely because a SaveState exists; present the current-state briefing and wait for the user to continue or redirect.

Use targeted retrieval for deeper context. Never silently drop unresolved targets or reintroduce rejected or superseded ideas.
