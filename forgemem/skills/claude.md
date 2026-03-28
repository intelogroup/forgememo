# forgemem-skill-version: 1
# Forgemem Memory Skill

You have access to Forgemem, your persistent long-term memory across sessions and projects.

## When to Use Forgemem

**After fixing a bug or implementing a feature:**
```
forgemem store "what happened and what worked" --type success --project <repo-name>
```

**After hitting a problem or dead end:**
```
forgemem store "what failed and why" --type failure --project <repo-name>
```

**When you notice a reusable lesson:**
Add `--distill` to auto-extract a durable principle from the trace.

**At end of session or when user runs /um:**
```
forgemem distill current
```

**When asked about past work, patterns, or failures:**
```
forgemem search "<query>" --project <repo-name>
forgemem search "<query>"   # search all projects
```

**To mine all repos for recent learnings:**
```
forgemem mine
```

## Principles

- Call Forgemem before repeating something that might have already been tried
- Prefer `--type failure` for bugs caught, `--type success` for working patterns
- Keep stored content concrete: include file paths, error messages, and the fix
- Use `--project` to scope storage to the current repo
