#!/usr/bin/env python3
"""Keep Claude Code and Codex on the same page.

Runs as a UserPromptSubmit hook on both agents:
    sync.py inject --me claude   (in .claude/settings.json)
    sync.py inject --me codex    (in .codex/hooks.json)

Before each message it reads only the NEW lines of the other agent's session
files (Codex voice transcripts included) and injects them as context.
Stdlib only. Never blocks the prompt: any error exits 0 with no output.
"""

import glob
import json
import os
import sys
from datetime import datetime, timedelta, timezone

HOME = os.path.expanduser("~")
STATE_DIR = os.path.join(HOME, ".agent-sync")
CLAUDE_GLOB = os.path.join(HOME, ".claude/projects/*/*.jsonl")
CODEX_GLOB = os.path.join(HOME, ".codex/sessions/*/*/*/*.jsonl")

CATCHUP = timedelta(hours=6)   # first message of a session looks back this far
TAIL_BYTES = 3_000_000         # on catch-up, read at most this much of a big file
MAX_TURNS = 20
USER_CHARS, REPLY_CHARS, TOTAL_CHARS = 300, 450, 5000
USER_NAME = os.environ.get("AGENT_SYNC_USER", "User")

INJECTED_PREFIXES = ("<", "# AGENTS.md", "Caveat:", "[Request interrupted")


def parse_ts(s):
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def texts(content, kinds):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") for b in content
                         if isinstance(b, dict) and b.get("type") in kinds)
    return ""


def is_injected(text):
    return text.lstrip().startswith(INJECTED_PREFIXES)


def parse_claude_line(obj):
    t = obj.get("type")
    if t not in ("user", "assistant") or obj.get("isSidechain") or obj.get("isMeta"):
        return None
    msg = obj.get("message") or {}
    if t == "user":
        text = texts(msg.get("content"), {"text"})
    else:
        text = texts(msg.get("content"), {"text"})
    text = text.strip()
    if not text or is_injected(text):
        return None
    return {"ts": obj.get("timestamp"), "role": t, "text": text,
            "project": obj.get("cwd", ""), "voice": False}


def parse_codex_line(obj, meta):
    p = obj.get("payload") or {}
    ts = obj.get("timestamp")
    if obj.get("type") == "session_meta":
        meta["cwd"] = p.get("cwd", "")
        meta["sub"] = isinstance(p.get("source"), dict)
        return None
    if meta.get("sub"):
        return None
    if obj.get("type") == "realtime_item" and p.get("type") == "transcript_segment":
        role, text, voice = p.get("role"), (p.get("text") or "").strip(), True
    elif obj.get("type") == "response_item" and p.get("type") == "message":
        role = p.get("role")
        if role not in ("user", "assistant"):
            return None
        text = texts(p.get("content"), {"input_text", "output_text"}).strip()
        voice = False
    else:
        return None
    if role not in ("user", "assistant") or not text or is_injected(text):
        return None
    return {"ts": ts, "role": role, "text": text,
            "project": meta.get("cwd", ""), "voice": voice}


def read_new(path, offset, since, is_codex):
    """Return (turns, new_offset) for lines after `offset` newer than `since`."""
    size = os.path.getsize(path)
    if offset > size:          # file was rewritten
        offset = 0
    turns, meta = [], {}
    with open(path, "rb") as f:
        if is_codex and offset > 0:
            # session_meta is line 1; needed for cwd/subagent flags
            first = f.readline()
            try:
                parse_codex_line(json.loads(first), meta)
            except Exception:
                pass
        f.seek(offset)
        if offset and offset != size:
            f.seek(offset - 1)
            if f.read(1) != b"\n":
                f.readline()   # skip a partial line
        for raw in f:
            if not raw.endswith(b"\n"):
                break          # line still being written; pick it up next time
            offset_line = raw
            try:
                obj = json.loads(offset_line)
            except Exception:
                continue
            turn = parse_codex_line(obj, meta) if is_codex else parse_claude_line(obj)
            if turn:
                ts = parse_ts(turn["ts"] or "")
                if ts and ts > since:
                    turn["dt"] = ts
                    turns.append(turn)
        new_offset = f.tell()
    return turns, new_offset


def load_state(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def save_state(path, state):
    os.makedirs(STATE_DIR, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, path)


def fmt(turns, other):
    label = "Codex" if other == "codex" else "Claude"
    lines = []
    for t in turns:
        local = t["dt"].astimezone().strftime("%H:%M")
        proj = os.path.basename(t["project"].rstrip("/")) or "?"
        how = " voice" if t["voice"] else ""
        if t["role"] == "user":
            lines.append(f"[{label}{how} {local} · {proj}] {USER_NAME}: {t['text'][:USER_CHARS]}")
        else:
            lines.append(f"[{label}{how} {local} · {proj}] {label}: {t['text'][:REPLY_CHARS]}")
    body = "\n".join(lines)
    if len(body) > TOTAL_CHARS:
        body = "…" + body[-TOTAL_CHARS:]
    return (f"[agent-sync] Since your last message, {USER_NAME} talked to {label} "
            f"(the other agent on their team). Treat this as shared context; "
            f"don't repeat it back unless asked. For older history use the "
            f"`reference` MCP (recall / search_sessions).\n{body}")


def inject(me, session_id):
    other = "codex" if me == "claude" else "claude"
    pattern = CODEX_GLOB if other == "codex" else CLAUDE_GLOB
    now = datetime.now(timezone.utc)
    state_path = os.path.join(STATE_DIR, f"{me}-{session_id}.json")
    state = load_state(state_path)
    first = state is None
    if first:
        state = {"since": (now - CATCHUP).isoformat(), "offsets": {}}
    since = parse_ts(state["since"])
    cutoff_mtime = since.timestamp()

    turns = []
    for path in glob.glob(pattern):
        try:
            if os.path.getmtime(path) < cutoff_mtime:
                continue
            offset = state["offsets"].get(path)
            if offset is None:
                size = os.path.getsize(path)
                offset = max(0, size - TAIL_BYTES) if first else 0
            new, state["offsets"][path] = read_new(path, offset, since, other == "codex")
            turns.extend(new)
        except Exception:
            continue

    turns.sort(key=lambda t: t["dt"])
    turns = turns[-MAX_TURNS:]
    if turns:
        state["since"] = turns[-1]["dt"].isoformat()
    save_state(state_path, state)
    return fmt(turns, other) if turns else ""


def main():
    try:
        args = sys.argv[1:]
        me = args[args.index("--me") + 1] if "--me" in args else "claude"
        try:
            hook = json.load(sys.stdin)
        except Exception:
            hook = {}
        session_id = hook.get("session_id") or "unknown"
        context = inject(me, session_id)
        if context:
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": context}}))
    except Exception:
        pass
    sys.exit(0)


if __name__ == "__main__":
    main()
