"""Coordinator regressions for cancellation and malformed local configuration."""

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cross_browser_duplicate_tab_guard as guard


class CoordinatorCancellationTests(unittest.TestCase):
    def setUp(self):
        self.coordinator = guard.Coordinator(guard.BrowserLauncher(dry_run=True))
        self.sender = guard.FakeSender()
        self.session = self.register(self.sender, "chrome")
        self.target_sender = guard.FakeSender()
        self.target = self.register(self.target_sender, "brave")
        self.url = "https://example.test/cancelled-transfer"
        self.coordinator.handle(self.session, {
            "type": "snapshot", "request_id": "snapshot", "tabs": [self.tab("1")],
        })
        self.coordinator.handle(self.session, {
            "type": "claim", "request_id": "claim", "tab": self.tab("2"),
        })
        self.incident_id = self.sender.response("claim")["incident"]["incident_id"]
        self.coordinator.handle(self.session, {
            "type": "resolve", "request_id": "resolve", "requester_tab_id": "2",
            "incident_id": self.incident_id, "choice": "brave",
        })
        self.transfer_id = self.sender.response("resolve")["transfer_id"]
        self.assertEqual(len(self.coordinator.transfers), 1)

    def register(self, sender, browser):
        return self.coordinator.register_client(sender, {
            "extension_id": guard.EXTENSION_ID, "browser": browser,
            "profile_id": browser, "incognito_context": False,
        }, "native")

    def tab(self, tab_id):
        return {"tab_id": tab_id, "url": self.url, "window_id": 1}

    def assert_late_arrival_preserves_copies(self):
        self.coordinator.handle(self.target, {
            "type": "claim", "request_id": "late-arrival", "tab": self.tab("3"),
        })
        self.assertNotEqual(
            self.target_sender.response("late-arrival")["result"], "transfer_completed",
        )
        messages = self.sender.messages + self.target_sender.messages
        self.assertFalse(any(message.get("action") == "close_tab" for message in messages))
        self.assertIn((self.session.context_key, "1"), self.coordinator.tabs)
        self.assertNotIn(self.transfer_id, self.coordinator.transfers)

    def test_cancelled_transfer_cannot_close_existing_tabs_on_late_arrival(self):
        self.coordinator.handle(self.session, {
            "type": "cancel_incident", "request_id": "cancel", "incident_id": self.incident_id,
        })
        self.assert_late_arrival_preserves_copies()
        self.assertEqual(self.coordinator.incidents[self.incident_id].status, "cancelled")
        self.assertIsNone(self.coordinator.incidents[self.incident_id].transfer_id)

    def test_closing_initiator_cancels_its_transfer(self):
        self.coordinator.handle(self.session, {
            "type": "tab_closed", "request_id": "closed", "tab_id": "2",
        })
        self.assert_late_arrival_preserves_copies()
        self.assertEqual(self.coordinator.incidents[self.incident_id].status, "cancelled")

    def test_cancelled_incident_rejects_stale_transfer_completion(self):
        incident = self.coordinator.incidents[self.incident_id]
        incident.status = "cancelled"
        transfer = self.coordinator.transfers[self.transfer_id]
        self.assertEqual(self.coordinator._complete_transfer_locked(
            transfer, (self.session.context_key, "1"),
        ), [])
        self.assertNotIn(self.transfer_id, self.coordinator.transfers)
        self.assertEqual(incident.status, "cancelled")
        self.assertIsNone(incident.transfer_id)

    def test_other_context_cannot_cancel_transfer(self):
        self.coordinator.handle(self.target, {
            "type": "cancel_incident", "request_id": "not-owner", "incident_id": self.incident_id,
        })
        self.assertIn(self.transfer_id, self.coordinator.transfers)
        self.assertEqual(self.coordinator.incidents[self.incident_id].status, "transferring")


class ConfigValidationTests(unittest.TestCase):
    def test_non_object_json_is_reported_as_invalid_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            with patch.object(guard, "config_path", return_value=path):
                for value in [None, [], "invalid", 42, True]:
                    with self.subTest(value=value):
                        path.write_text(json.dumps(value), encoding="utf-8")
                        with self.assertRaisesRegex(guard.GuardError, "configuration is invalid"):
                            guard.load_config(create=False)

    def test_create_repairs_non_object_config(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text("[]", encoding="utf-8")
            with patch.object(guard, "config_path", return_value=path), patch.object(
                guard, "choose_ws_port", return_value=49473,
            ):
                result = guard.load_config(create=True)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), result)
            self.assertEqual(result["ws_port"], 49473)
            self.assertGreaterEqual(len(result["token"]), 32)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)



class PrivateDirectoryTests(unittest.TestCase):
    def test_owned_directory_is_private(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runtime"
            path.mkdir(mode=0o755)
            guard.ensure_private_dir(path)
            self.assertEqual(path.stat().st_mode & 0o777, 0o700)

    def test_final_symlink_is_rejected_without_changing_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "target"
            target.mkdir(mode=0o755)
            target.chmod(0o755)
            link = root / "runtime"
            link.symlink_to(target, target_is_directory=True)
            with self.assertRaises(OSError):
                guard.ensure_private_dir(link)
            self.assertEqual(target.stat().st_mode & 0o777, 0o755)

    def test_parent_symlink_is_supported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "parent"
            target.mkdir()
            link = root / "parent-link"
            link.symlink_to(target, target_is_directory=True)
            guard.ensure_private_dir(link / "runtime")
            self.assertEqual((target / "runtime").stat().st_mode & 0o777, 0o700)

    def test_wrong_owner_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runtime"
            path.mkdir()
            with patch.object(guard.os, "getuid", return_value=os.getuid() + 1):
                with self.assertRaises(PermissionError):
                    guard.ensure_private_dir(path)

    def test_permission_failure_is_not_swallowed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runtime"
            with patch.object(guard.os, "fchmod", side_effect=PermissionError("denied")):
                with self.assertRaises(PermissionError):
                    guard.ensure_private_dir(path)


class UninstallManifestTests(unittest.TestCase):
    def test_only_matching_installer_external_manifest_is_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            extension = root / "extension"
            directories = {}
            records = {
                "owned": {"external_directory": str(extension)},
                "store": {"external_update_url": "https://clients2.google.com/service/update2/crx"},
                "other": {"external_directory": str(root / "other-extension")},
                "custom": {"external_directory": str(extension), "custom_setting": True},
                "malformed": None,
            }
            for name, record in records.items():
                path = root / name
                path.mkdir()
                (path / f"{guard.EXTENSION_ID}.json").write_text(
                    json.dumps(record) if record is not None else "broken", encoding="utf-8",
                )
                directories[name] = [path]
            with patch.object(guard, "external_extension_dirs", return_value=directories), patch.object(
                guard, "extension_dir", return_value=extension,
            ):
                guard._remove_owned_external_extensions()
            self.assertFalse((root / "owned" / f"{guard.EXTENSION_ID}.json").exists())
            for name in records.keys() - {"owned"}:
                self.assertTrue((root / name / f"{guard.EXTENSION_ID}.json").exists(), name)



class StandaloneInstallTests(unittest.TestCase):
    def test_standalone_install_writes_every_manifest_icon(self):
        source_icons = Path(guard.__file__).resolve().parent / "extension" / "icons"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            downloaded_script = root / "download" / "cross_browser_duplicate_tab_guard.py"
            downloaded_script.parent.mkdir()
            downloaded_script.write_bytes(Path(guard.__file__).read_bytes())
            installed = root / "installed"
            installed_extension = installed / "extension"
            self.assertFalse((downloaded_script.parent / "extension").exists())
            with contextlib.ExitStack() as stack:
                stack.enter_context(patch.object(guard, "__file__", str(downloaded_script)))
                stack.enter_context(patch.object(guard, "app_dir", return_value=installed))
                stack.enter_context(patch.object(guard, "extension_dir", return_value=installed_extension))
                stack.enter_context(patch.object(guard, "installed_script_path", return_value=installed / downloaded_script.name))
                stack.enter_context(patch.object(guard, "load_config", return_value={"token": "x" * 48, "ws_port": 49473}))
                stack.enter_context(patch.object(guard, "install_native_manifests", return_value=[]))
                stack.enter_context(patch.object(guard, "install_external_extensions", return_value=[]))
                stack.enter_context(patch.object(guard, "install_autostart", return_value="test"))
                stack.enter_context(patch.object(guard, "daemon_is_reachable", return_value=True))
                start_daemon = stack.enter_context(patch.object(guard, "start_daemon_process"))
                stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
                self.assertEqual(guard.install(), 0)
                start_daemon.assert_not_called()
            manifest = json.loads((installed_extension / "manifest.json").read_text(encoding="utf-8"))
            icons = set(manifest["icons"].values()) | set(manifest["action"]["default_icon"].values())
            self.assertTrue(icons)
            for relative in icons:
                with self.subTest(icon=relative):
                    self.assertEqual(
                        (installed_extension / relative).read_bytes(),
                        (source_icons / Path(relative).name).read_bytes(),
                    )
            self.assertTrue((installed / downloaded_script.name).exists())


if __name__ == "__main__":
    unittest.main()
