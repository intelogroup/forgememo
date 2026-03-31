# forgememo-skill-version: 3
# Forgemem Memory Skill

You have access to Forgemem, your persistent long-term memory across sessions and projects.

## Setup (first time only)

If forgememo is not yet initialized, ask the user to run this in a real terminal:

```bash
pip install forgememo
forgememo init
```

Then tell the user: "Forgemem is installed. Please restart Claude Code to activate the memory MCP connection."

Do NOT try to bypass first-run setup with `forgememo init --yes` or a non-TTY session. The user must choose a provider interactively.

## At Session Start

Before starting any work, recall relevant context with MCP:
```
search_memories(query="<current project or task>", workspace_root="<repo-path>")
```

## During a Session

**When you need details for a memory:**
```
get_memory_details(ids=["d:42"], workspace_root="<repo-path>")
```

**When you need temporal context:**
```
get_memory_timeline(anchor_id="d:42", workspace_root="<repo-path>")
```

## At End of Session

Write a structured session summary via MCP (daemon write path):
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

## When Asked About Past Work

```
search_memories(query="<query>", workspace_root="<repo-path>")
get_memory_details(ids=["d:1","s:3"], workspace_root="<repo-path>")
```

**To mine all repos for recent learnings (requires configured provider):**
```
forgememo mine
```

## Principles

- Always search Forgemem before repeating something that might have already been tried
- Keep summaries concrete: include file paths, errors, and the fix
- Use `workspace_root` to scope results to the current repo
- MCP tools are read-only except `save_session_summary`
