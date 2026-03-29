# forgemem-skill-version: 1
# Forgemem Memory Skill

You have access to Forgemem, your persistent long-term memory across sessions and projects.

## Setup (first time only)

If forgemem is not yet initialized, ask the user to run this in a real terminal:

```bash
pip install forgemem
forgemem init
```

Then tell the user: "Forgemem is installed. Please restart Gemini CLI to activate the memory MCP connection."

Do NOT try to bypass first-run setup with `forgemem init --yes` or a non-TTY session. The user must choose a provider interactively.

## When to Use Forgemem

**After fixing a bug or implementing a feature:**
Run in terminal: `forgemem store "<what happened>" --type success --project <repo-name>`

**After hitting a problem or dead end:**
Run in terminal: `forgemem store "<what failed and why>" --type failure --project <repo-name>`

**When you notice a reusable lesson:**
Add `--distill` flag to auto-extract a durable principle.

**At end of session:**
Run: `forgemem distill current`

**When asked about past work or patterns:**
Run: `forgemem search "<query>"`

**To mine all repos for recent learnings:**
Run: `forgemem mine`

## Principles

- Call Forgemem before repeating something that might have been tried before
- Keep stored content concrete: include file names, error messages, and the resolution
- Use `--project <repo>` to scope memory to the current repository
