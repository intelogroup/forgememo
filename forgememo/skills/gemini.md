# forgememo-skill-version: 3
# Forgemem Memory Skill

You have access to Forgemem, your persistent long-term memory across sessions and projects.

## Setup (first time only)

If forgememo is not yet initialized, ask the user to run this in a real terminal:

```bash
pip install forgememo
forgememo init
```

Then tell the user: "Forgemem is installed. Please restart Gemini CLI to activate the memory MCP connection."

Do NOT try to bypass first-run setup with `forgememo init --yes` or a non-TTY session. The user must choose a provider interactively.

## When to Use Forgemem

**At session start (recall context):**
```
search_memories(query="<current task>", workspace_root="<repo-path>")
```

**When you need details for a memory:**
```
get_memory_details(ids=["d:42"], workspace_root="<repo-path>")
```

**When you need temporal context:**
```
get_memory_timeline(anchor_id="d:42", workspace_root="<repo-path>")
```

**At end of session (write structured summary):**
```
save_session_summary(
  request="<what the user asked for>",
  workspace_root="<repo-path>",
  investigation="<what you checked>",
  learnings="<key technical learnings>",
  next_steps="<what to do next>",
  concepts=["pattern","gotcha"]
)
```

## Principles

- Call Forgemem before repeating something that might have been tried before
- Keep summaries concrete: include file paths, errors, and the fix
- Use `workspace_root` to scope memory to the current repository
