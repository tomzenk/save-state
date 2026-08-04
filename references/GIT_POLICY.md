# SaveState Git policy

## Default

After a successful state transaction, SaveState runs a normal Git commit restricted to `.savestate/`.

```text
chore(savestate): <session summary>
```

## Safety properties

- Project code is never included automatically.
- Unrelated staged changes remain staged.
- Normal repository hooks and signing policies are respected.
- Git commit failure does not roll back a valid state save.
- Outside Git, state files still work normally.

## Why code and state remain separate

A context save must not unexpectedly decide that unfinished source code is ready to commit. SaveState records dirty worktree paths and test evidence, while normal development commits preserve code boundaries chosen by the user or team.

## Configuration

```json
{
  "git": {
    "auto_commit_state": true,
    "commit_notes": false
  }
}
```

Set `auto_commit_state` to false when repository policy requires manual commits.
