# agent-sync

Claude Code and Codex, on the same page every message.

Tell Codex something (by voice, even). Switch to Claude Code. It already knows. And the other way round.

![Told Codex a codeword by voice. Claude Code knew it a minute later without reading any files.](docs/demo.png)

## Why

My whole setup is built around Claude Code: an Obsidian vault, memory files, hooks that load my context every session. But the voice mode in the ChatGPT app (where Codex lives) is the one I actually want to talk to.

Giving Codex the same files was easy (an `AGENTS.md` that says "read what Claude reads"). The hard part: neither agent knew what I had just said to the other.

## How it works

- A `UserPromptSubmit` hook on each agent runs `sync.py` before every message you send.
- It reads only the **new** lines of the other agent's session logs (`~/.claude/projects`, `~/.codex/sessions`), including Codex **voice transcripts**.
- It hands them over as context. Cursors live in `~/.agent-sync/`. The first message of a new session catches up on the last 6 hours.
- One Python file, standard library only. No server, no database, no dependencies. About 0.05 seconds per message.

## Pairs with reference

For older history, use [reference](https://github.com/Kuberwastaken/reference) by [@Kuberwastaken](https://github.com/Kuberwastaken): one MCP server that lets each agent search the other's past sessions.

They are two halves of one idea. **agent-sync pushes what just happened. reference lets them search everything before that.**

## Install

```bash
git clone https://github.com/saksham10arora-dotcom/agent-sync ~/agent-sync
```

Use the absolute path to `sync.py` in both hooks below.

**Claude Code:** add to `.claude/settings.json` in your project (or `~/.claude/settings.json` for every project):

```json
{
  "hooks": {
    "UserPromptSubmit": [
      { "hooks": [ { "type": "command", "command": "python3 /ABSOLUTE/PATH/agent-sync/sync.py inject --me claude", "timeout": 5 } ] }
    ]
  }
}
```

**Codex:** add to `.codex/hooks.json` in the same project:

```json
{
  "hooks": {
    "UserPromptSubmit": [
      { "hooks": [ { "type": "command", "command": "python3 /ABSOLUTE/PATH/agent-sync/sync.py inject --me codex", "timeout": 5 } ] }
    ]
  }
}
```

**Then approve it in Codex.** Codex silently skips hooks you haven't trusted. Run `codex` in that project, type `/hooks`, press `t`. Editing the hook later means approving it again.

Optional: put your name in the transcript lines with `AGENT_SYNC_USER=YourName python3 ...` in both commands.

## Try it

1. Tell Codex: *"the codeword is PVC."*
2. In Claude Code: *"what's my codeword? don't read any files."*

## Limits

- **Codex voice starts blind.** The ChatGPT app starts its voice model with `includeStartupContext: false`, so it never sees synced context directly. It gets it when it hands a task to the Codex agent behind it. Ask in tasks: *"check what I did with Claude and tell me..."*
- **Session formats are undocumented** and can change with any update. The tests pin the current shapes; if a sync goes quiet after an update, that's the first suspect.
- Long messages are trimmed (300 characters for yours, 450 for replies), up to 20 turns per message.
- **Privacy:** it runs locally and makes no network calls, but the injected text goes to your model provider as part of the prompt, like anything else you type.
- Tested on macOS with Claude Code 2.1.286 and Codex 0.154.0 (CLI and the ChatGPT desktop app). Linux uses the same paths and should work. Windows is untested.

## Tests

```bash
python3 -m unittest tests/test_sync.py
```

## License

MIT
