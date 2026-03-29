# forgemem-skill-version: 2
# Forgemem Memory Skill

You have access to Forgemem, your persistent long-term memory across sessions and projects.

## Setup (first time only)

If forgemem is not yet initialized, ask the user to run this in a real terminal:

```bash
pip install forgemem
forgemem init
```

Then tell the user: "Forgemem is installed. Please restart Claude Code to activate the memory MCP connection."

Do NOT try to bypass first-run setup with `forgemem init --yes` or a non-TTY session. The user must choose a provider interactively.

## At Session Start

Before starting any work, recall relevant context:
```
forgemem search "<current project or task>" --project <repo-name>
```

## During a Session

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

## At End of Session

**Preferred path (no API key needed — you are the LLM):**

1. Review what happened this session
2. Call the `mine_session` MCP tool with memories you've extracted:
   - `type`: success|failure|plan|note
   - `content`: what happened (concrete — include file paths, error messages, the fix)
   - `principle`: 1-2 sentence lasting lesson
   - `score`: 1-10 impact
   - `tags`: comma-separated (optional)
3. Call the `distill_session` MCP tool for any undistilled traces you can synthesize:
   - Fetch undistilled traces first: `forgemem search "" --project <repo>` or use `retrieve_memories`
   - For each, provide `trace_id`, `principle`, `score`, `tags`

**If BYOK is configured (background/batch distillation):**
```
forgemem distill current
```

## When Asked About Past Work

```
forgemem search "<query>" --project <repo-name>
forgemem search "<query>"   # search all projects
```

**To mine all repos for recent learnings (requires configured provider):**
```
forgemem mine
```

## Principles

- Always search Forgemem before repeating something that might have already been tried
- Prefer `--type failure` for bugs caught, `--type success` for working patterns
- Keep stored content concrete: include file paths, error messages, and the fix
- Use `--project` to scope storage to the current repo
- `mine_session` and `distill_session` MCP tools work with any AI subscription — no separate API key needed
