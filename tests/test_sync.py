"""End-to-end tests: fake Claude and Codex session files in a temp HOME."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

SYNC = os.path.join(os.path.dirname(__file__), "..", "sync.py")


def ts(minutes_ago):
    t = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return t.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def write_jsonl(path, rows, mode="w"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, mode) as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


class SyncTest(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.claude = os.path.join(self.home, ".claude/projects/-tmp-proj/s1.jsonl")
        self.codex = os.path.join(self.home, ".codex/sessions/2026/10/02/rollout-x.jsonl")

    def run_sync(self, me, session="s"):
        out = subprocess.run(
            [sys.executable, SYNC, "inject", "--me", me],
            input=json.dumps({"session_id": session}),
            capture_output=True, text=True, env={**os.environ, "HOME": self.home})
        self.assertEqual(out.returncode, 0)
        if not out.stdout.strip():
            return ""
        return json.loads(out.stdout)["hookSpecificOutput"]["additionalContext"]

    def test_claude_sees_codex_voice_and_text(self):
        write_jsonl(self.codex, [
            {"type": "session_meta", "timestamp": ts(5), "payload": {"cwd": "/tmp/proj", "source": "vscode"}},
            {"type": "realtime_item", "timestamp": ts(4), "payload": {"type": "transcript_segment", "role": "user", "text": "the codeword is PVC"}},
            {"type": "realtime_item", "timestamp": ts(4), "payload": {"type": "transcript_segment", "role": "assistant", "text": "Got it, PVC."}},
            {"type": "response_item", "timestamp": ts(3), "payload": {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "# AGENTS.md instructions (injected, must be skipped)"}]}},
            {"type": "response_item", "timestamp": ts(3), "payload": {"type": "message", "role": "developer", "content": [{"type": "input_text", "text": "system stuff"}]}},
        ])
        ctx = self.run_sync("claude")
        self.assertIn("Codex voice", ctx)
        self.assertIn("the codeword is PVC", ctx)
        self.assertNotIn("AGENTS.md", ctx)
        self.assertNotIn("system stuff", ctx)

    def test_codex_sees_claude_and_skips_tool_noise(self):
        write_jsonl(self.claude, [
            {"type": "user", "timestamp": ts(3), "cwd": "/tmp/proj", "message": {"role": "user", "content": "what's my code word?"}},
            {"type": "assistant", "timestamp": ts(3), "cwd": "/tmp/proj", "message": {"role": "assistant", "content": [
                {"type": "thinking", "thinking": "private"}, {"type": "tool_use", "name": "Bash"}, {"type": "text", "text": "Your code word is PVC."}]}},
            {"type": "user", "timestamp": ts(2), "cwd": "/tmp/proj", "message": {"role": "user", "content": [{"type": "tool_result", "content": "noise"}]}},
            {"type": "user", "timestamp": ts(2), "cwd": "/tmp/proj", "isSidechain": True, "message": {"role": "user", "content": "subagent prompt"}},
        ])
        ctx = self.run_sync("codex")
        self.assertIn("Saksham: what's my code word?".split(":")[1].strip(), ctx)
        self.assertIn("Your code word is PVC.", ctx)
        for noise in ("private", "noise", "subagent prompt"):
            self.assertNotIn(noise, ctx)

    def test_only_new_turns_on_next_message(self):
        write_jsonl(self.codex, [
            {"type": "session_meta", "timestamp": ts(5), "payload": {"cwd": "/tmp/proj"}},
            {"type": "realtime_item", "timestamp": ts(4), "payload": {"type": "transcript_segment", "role": "user", "text": "first"}},
        ])
        self.assertIn("first", self.run_sync("claude"))
        self.assertEqual(self.run_sync("claude"), "")  # nothing new
        write_jsonl(self.codex, [
            {"type": "realtime_item", "timestamp": ts(0), "payload": {"type": "transcript_segment", "role": "user", "text": "second"}},
        ], mode="a")
        ctx = self.run_sync("claude")
        self.assertIn("second", ctx)
        self.assertNotIn("first", ctx)

    def test_old_sessions_ignored_and_bad_input_never_fails(self):
        write_jsonl(self.codex, [
            {"type": "realtime_item", "timestamp": ts(60 * 24), "payload": {"type": "transcript_segment", "role": "user", "text": "yesterday"}},
        ])
        self.assertEqual(self.run_sync("claude"), "")
        out = subprocess.run([sys.executable, SYNC, "inject", "--me", "claude"], input="not json",
                             capture_output=True, text=True, env={**os.environ, "HOME": self.home})
        self.assertEqual(out.returncode, 0)


if __name__ == "__main__":
    unittest.main()
