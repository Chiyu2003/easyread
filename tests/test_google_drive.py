import copy
import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from easyread import google_drive


class GoogleDriveSyncTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "library"
        self.root.mkdir()
        self.state = Path(self.temp.name) / "google-drive.json"
        self.remote = {}
        self.contents = {}
        self.next_id = 0
        self.patches = [
            mock.patch.object(google_drive, "state_path", return_value=self.state),
            mock.patch.object(google_drive, "_root", side_effect=lambda create=False: "folder"),
            mock.patch.object(google_drive, "files", side_effect=lambda: copy.deepcopy(self.remote)),
            mock.patch.object(google_drive, "_download", side_effect=lambda file_id: self.contents[file_id]),
            mock.patch.object(google_drive, "_upload", side_effect=self.upload),
        ]
        for patcher in self.patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def upload(self, folder, rel, data, existing):
        self.next_id += 1
        file_id = existing["id"] if existing else f"file-{self.next_id}"
        self.contents[file_id] = data
        self.remote[rel] = {"id": file_id, "name": rel, "md5Checksum": hashlib.md5(data).hexdigest(),
                            "appProperties": {"easyreadPath": rel}}
        return self.remote[rel]

    def test_first_sync_uploads_local_files(self):
        (self.root / "paper.json").write_text('{"title":"one"}', encoding="utf-8")
        result = google_drive.sync(self.root)
        self.assertEqual(result["uploaded"], 1)
        self.assertIn("paper.json", self.remote)
        self.assertTrue(google_drive.read_json(self.state, {}).get("last_sync"))

    def test_divergent_first_sync_preserves_remote_conflict_and_uses_local(self):
        local = b"local edit"
        remote = b"remote edit"
        (self.root / "paper.json").write_bytes(local)
        self.contents["remote-1"] = remote
        self.remote["paper.json"] = {"id": "remote-1", "name": "paper.json", "md5Checksum": hashlib.md5(remote).hexdigest(),
                                      "appProperties": {"easyreadPath": "paper.json"}}
        result = google_drive.sync(self.root)
        conflicts = list(self.root.glob("paper.json.conflict-*"))
        self.assertEqual(result["conflicts"], ["paper.json"])
        self.assertEqual(result["uploaded"], 1)
        self.assertEqual(conflicts[0].read_bytes(), remote)
        self.assertEqual(self.contents["remote-1"], local)

    def test_remote_only_change_downloads(self):
        local = b"base"
        remote = b"new remote"
        (self.root / "paper.json").write_bytes(local)
        self.contents["remote-1"] = remote
        self.remote["paper.json"] = {"id": "remote-1", "name": "paper.json", "md5Checksum": hashlib.md5(remote).hexdigest(),
                                      "appProperties": {"easyreadPath": "paper.json"}}
        google_drive.write_json_atomic(self.state, {"folder_id": "folder", "sync_base": {
            "paper.json": {"local": hashlib.md5(local).hexdigest(), "remote": hashlib.md5(local).hexdigest()}}})
        result = google_drive.sync(self.root)
        self.assertEqual(result["downloaded"], 1)
        self.assertEqual((self.root / "paper.json").read_bytes(), remote)

    def test_local_delete_removes_unchanged_drive_copy(self):
        self.state.write_text('{"folder_id":"folder","sync_base":{"paper.json":{"local":"base","remote":"base"}}}', encoding="utf-8")
        self.remote["paper.json"] = {"id": "remote-1", "name": "paper.json", "md5Checksum": "base",
                                      "appProperties": {"easyreadPath": "paper.json"}}
        with mock.patch.object(google_drive, "_api", return_value={}) as api:
            result = google_drive.sync(self.root)
        api.assert_called_once_with("DELETE", "/files/remote-1")
        self.assertEqual(result["deleted"], 1)


if __name__ == "__main__":
    unittest.main()
