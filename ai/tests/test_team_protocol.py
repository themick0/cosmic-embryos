from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from embryo_harness import team_protocol as protocol


class TeamProtocolTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="team protocol ")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.workspace = self.root / "workspace"
        self.inbox, self.outbox = self.root / "inbox", self.root / "outbox"
        for path in (self.workspace, self.inbox, self.outbox):
            path.mkdir()
        previous = Path.cwd()
        os.chdir(self.workspace)
        self.addCleanup(os.chdir, previous)
        for name in ("ai/prompts/competition.md", "ai/prompts/common.md", "ai/harness.py"):
            path = self.workspace / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("Fixture source\n")
        self.request = {"version": 1, "id": uuid.uuid4().hex, "kind": "preflight", "role": "worker-a",
                        "workspace": str(self.workspace), "nonce": uuid.uuid4().hex,
                        "created_at": "2026-10-18T20:30:00+00:00",
                        "probe_sha256": hashlib.sha256(Path(protocol.__file__).read_bytes()).hexdigest()}

    def handle(self, request_id=None):
        return protocol.handle_preflight(self.workspace, self.inbox, self.outbox, "worker-a",
                                         request_id or self.request["id"])

    def test_publish_is_immutable_idempotent_and_rejects_changed_content(self):
        path = protocol.publish(self.inbox, self.request)
        original = path.read_bytes()
        inode = path.stat().st_ino
        self.assertEqual(protocol.publish(self.inbox, dict(reversed(list(self.request.items())))), path)
        self.assertEqual(path.stat().st_ino, inode)
        self.assertFalse(path.stat().st_mode & 0o222)
        with self.assertRaisesRegex(protocol.ProtocolError, "different content"):
            protocol.publish(self.inbox, dict(self.request, nonce=uuid.uuid4().hex))
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(list(self.inbox.iterdir()), [path])

    def test_interrupted_publish_never_exposes_partial_message(self):
        with patch.object(protocol.os, "link", side_effect=OSError("simulated interruption")):
            with self.assertRaisesRegex(OSError, "simulated"):
                protocol.publish(self.inbox, self.request)
        self.assertEqual(list(self.inbox.iterdir()), [])
        path = protocol.publish(self.inbox, self.request)
        self.assertEqual(protocol.read_message(path), self.request)

    def test_named_preflight_only_and_duplicate_reuses_original_result(self):
        protocol.publish(self.inbox, self.request)
        unrelated = self.inbox / (uuid.uuid4().hex + ".json")
        unrelated.write_text("broken unrelated request")
        with patch.object(protocol.shutil, "which", return_value="/fixture/tool"):
            result = self.handle()
        self.assertFalse(result["runtime_ready"])
        self.assertTrue(all(result["checks"].values()))
        canonical = json.dumps(self.request, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        self.assertEqual(result["request_sha256"], hashlib.sha256(canonical).hexdigest())
        self.assertEqual(protocol.parse_preflight_result(result, self.request), result)
        (self.workspace / "ai/harness.py").unlink()
        with patch.object(protocol.shutil, "which", side_effect=AssertionError("must not recompute")), \
                patch.object(protocol, "_regular_probe", side_effect=AssertionError("must not recompute")):
            self.assertEqual(self.handle(), result)
        self.assertEqual(len(list(self.outbox.iterdir())), 1)

    def test_wrong_role_workspace_or_named_id_is_rejected(self):
        for changes in ({"role": "uploader"}, {"workspace": str(self.root)}, {"id": uuid.uuid4().hex}):
            with self.subTest(changes=changes):
                path = self.inbox / (self.request["id"] + ".json")
                path.write_text(json.dumps(dict(self.request, **changes)))
                with self.assertRaisesRegex(protocol.ProtocolError, "does not match"):
                    self.handle()
                path.unlink()
        self.assertEqual(list(self.outbox.iterdir()), [])

    def test_handler_requires_actual_workspace_cwd(self):
        protocol.publish(self.inbox, self.request)
        os.chdir(self.root)
        with self.assertRaisesRegex(protocol.ProtocolError, "declared workspace"):
            self.handle()
        self.assertEqual(list(self.outbox.iterdir()), [])

    def test_existing_response_identity_tamper_is_rejected(self):
        protocol.publish(self.inbox, self.request)
        with patch.object(protocol.shutil, "which", return_value=None):
            response = self.handle()
        response["nonce"] = uuid.uuid4().hex
        path = self.outbox / (self.request["id"] + ".json")
        path.chmod(0o644)
        path.write_text(json.dumps(response))
        with self.assertRaisesRegex(protocol.ProtocolError, "identity"):
            self.handle()

    def test_executing_source_hash_mismatch_stops_before_probes(self):
        protocol.publish(self.inbox, dict(self.request, probe_sha256="0" * 64))
        with patch.object(protocol.shutil, "which", side_effect=AssertionError("must not probe")):
            with self.assertRaisesRegex(protocol.ProtocolError, "Executing preflight code"):
                self.handle()
        self.assertEqual(list(self.outbox.iterdir()), [])

    def test_existing_result_is_bound_to_all_request_fields(self):
        path = protocol.publish(self.inbox, self.request)
        with patch.object(protocol.shutil, "which", return_value=None):
            self.handle()
        path.chmod(0o644)
        path.write_text(json.dumps(dict(self.request, created_at="2026-10-18T20:31:00+00:00")))
        with self.assertRaisesRegex(protocol.ProtocolError, "canonical request hash"):
            self.handle()

    def test_symlink_messages_and_mailboxes_are_rejected(self):
        actual = protocol.publish(self.inbox, self.request)
        linked = self.outbox / actual.name
        linked.symlink_to(actual)
        with self.assertRaisesRegex(protocol.ProtocolError, "Symlinks"):
            protocol.read_message(linked)
        with self.assertRaisesRegex(protocol.ProtocolError, "Symlinks"):
            protocol.publish(self.outbox, self.request)
        directory_link = self.root / "linked-inbox"
        directory_link.symlink_to(self.inbox, target_is_directory=True)
        with self.assertRaisesRegex(protocol.ProtocolError, "Symlinks"):
            protocol.publish(directory_link, self.request)

    def test_oversized_and_invalid_json_messages_are_rejected(self):
        path = self.inbox / "invalid.json"
        for payload in (b"x" * (protocol.MAX_BYTES + 1), b"[]", b"{", b'{"a":1,"a":2}',
                        b'{"a":NaN}', b"\xff"):
            with self.subTest(payload=payload[:20]):
                path.write_bytes(payload)
                with self.assertRaises(protocol.ProtocolError):
                    protocol.read_message(path)
        with self.assertRaisesRegex(protocol.ProtocolError, "64 KiB"):
            protocol.publish(self.outbox, dict(self.request, extra="x" * protocol.MAX_BYTES))
        self.assertEqual(list(self.outbox.iterdir()), [])

    def test_request_parser_rejects_bad_fields_and_types(self):
        variants = [dict(self.request, version=True), dict(self.request, kind="research"),
                    dict(self.request, role=[]), dict(self.request, workspace="relative"),
                    dict(self.request, created_at="2026-10-18T20:30:00+03:00"),
                    dict(self.request, created_at="2026-02-30T20:30:00Z"),
                    dict(self.request, probe_sha256="invalid"),
                    dict(self.request, id="../escape"), dict(self.request, nonce=None),
                    dict(self.request, extra="unexpected")]
        missing = dict(self.request)
        del missing["nonce"]
        variants.append(missing)
        for message in variants:
            with self.subTest(message=message):
                with self.assertRaises(protocol.ProtocolError):
                    protocol.parse_preflight_request(message)

    def test_preflight_does_not_follow_source_symlink_or_claim_runtime_ready(self):
        path = self.workspace / "ai/harness.py"
        path.unlink()
        path.symlink_to(self.workspace / "ai/prompts/common.md")
        protocol.publish(self.inbox, self.request)
        with patch.object(protocol.shutil, "which", return_value=None):
            response = self.handle()
        self.assertFalse(response["checks"]["harness"])
        self.assertFalse(response["checks"]["codex_available"])
        response["runtime_ready"] = True
        with self.assertRaisesRegex(protocol.ProtocolError, "runtime ready"):
            protocol.parse_preflight_result(response, self.request)


if __name__ == "__main__":
    unittest.main()
