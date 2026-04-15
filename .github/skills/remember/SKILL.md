---
name: remember
description: >
  Promote reusable project knowledge to the right memory surface.
  Activate when user says: remember this, save this, store this,
  remember, memorize, keep this for later.
hint: "[what to remember]"
allowed-tools: [readFile, editFiles, search, codebase]
---

# Remember

Promote durable, reusable knowledge into the right memory surface instead of leaving it buried in chat history.

## When to Use
- User wants to preserve knowledge discovered during a session
- Organizing scattered findings into structured memory
- Cleaning up duplicate or conflicting stored information

## When NOT to Use
- Ephemeral task notes → just keep in conversation
- Already documented in code or docs → reference directly

## Memory Surfaces

| Surface | Use For | Durability |
|---------|---------|------------|
| **Project memory** | Durable team/project knowledge | Permanent |
| **Session context** | Short-lived working notes | Session only |
| **Docs / Instructions** | Conventions, instructions | Permanent |

## Workflow

1. Gather the relevant session findings
2. Classify each item:
   - Durable project fact
   - Temporary working note
   - Operator preference or instruction
   - Duplicate / stale / conflicting information
3. Propose the best destination for each item
4. Write or update only the appropriate memory surface
5. Call out duplicates or conflicts that should be cleaned up

## Rules

- Do not dump everything into one store
- Prefer project memory for durable team knowledge
- Keep entries concise and actionable
- If something is uncertain, mark it as uncertain rather than storing it as fact

## Output

- What was stored
- Where it was stored
- Any duplicates/conflicts found
