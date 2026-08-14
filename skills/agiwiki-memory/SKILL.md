---
name: agiwiki-memory
description: Use a local AGIWiki factual-memory catalog through the find_memory and get_memory tools. Use when a task may depend on the user's installed manuals, notes, code knowledge, concepts, procedures, or troubleshooting memories, especially when an exact source-backed local fact should be checked before answering or acting.
---

# AGIWiki Memory

Use the user's active, local Memory Packs as factual context. Treat them as
derived memory with source references, not as a replacement for the original
material or current external state.

## Workflow

1. Call `find_memory` with a concise description of the current information
   need. Add workspace scope only when the task or user supplies it.
2. If the tools are unavailable, say that AGIWiki is not connected; do not
   reinterpret tool absence as an empty catalog or search repository files as
   a substitute.
3. If no match is returned, say that local factual memory has no match and use
   the user's normal fallback. Never invent a memory.
4. For a promising result, call `get_memory` with its exact `entry_id` and,
   when supplied, `entry_version_id` or `pack_id`.
5. Check `kind`, `applies_to`, prerequisites, warnings, verification guidance,
   and source references before using the memory.
6. Cite the memory title and its portable source locator when explaining a
   conclusion. Open the original source separately when the decision is
   consequential or the memory is insufficient.

## Safety boundaries

- Do not ask AGIWiki to execute a procedure. It only returns factual memory.
- Do not broaden the active workspace scope through tool arguments.
- Do not edit installed Packs. Ask the user to update Workspace JSON, rebuild,
  install, and explicitly activate a new Pack.
- Do not treat a summary as proof of live state, price, availability, account
  status, or another fact that can change after the source edition.
- Stop and request confirmation before carrying out destructive or privileged
  steps even when a memory describes them.
