import tempfile
import unittest
from pathlib import Path
from unittest import mock

from easyread import experiments, runner


def profile(**overrides):
    return {"id": "baseline", "name": "Baseline", "ssh_alias": "lab-gpu", "docker_image": "lab/model:latest",
            "command_template": ["python", "train.py", "--seed", "{seed}"],
            "params": {"seed": {"type": "integer", "default": 3}}, "mounts": [],
            "output_path": "/output", "host_output_path": "/var/tmp/experiments", **overrides}


class ExperimentProfileTest(unittest.TestCase):
    def test_validate_profile_and_render_safe_arguments(self):
        p = experiments.validate_profile(profile())
        command = runner._remote_command(p, {"seed": "1; touch /tmp/not-run"}, "paper-1", "/var/tmp/experiments/run-1")
        self.assertIn("/var/tmp/experiments/run-1:/output", command)
        self.assertIn("'1; touch /tmp/not-run'", command)
        self.assertIn("--network none", command)

    def test_rejects_unsafe_ssh_alias_and_mount(self):
        with self.assertRaises(ValueError):
            experiments.validate_profile(profile(ssh_alias="lab; rm -rf /"))
        with self.assertRaises(ValueError):
            experiments.validate_profile(profile(mounts=[{"host": "relative", "container": "/data"}]))

    def test_rejects_run_parameter_with_wrong_type(self):
        p = profile()
        with self.assertRaisesRegex(ValueError, "must be a integer"):
            experiments.new_run(p, "", {"seed": "not-an-integer"})

    def test_runner_marks_interrupted_task_failed_without_rerunning(self):
        state_path = Path(tempfile.mkdtemp()) / "state.json"
        state = {"seen": {"task-1": {"state": "running"}}}
        task = {"id": "task-1", "file_id": "drive-file", "state": "queued"}
        with mock.patch.object(runner.google_drive, "update_task") as update:
            result = runner._run_one(task, state, state_path)
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["exit_code"], 255)
        update.assert_called_once()

    def test_runner_recovers_a_drive_manifest_left_running_after_restart(self):
        state_path = Path(tempfile.mkdtemp()) / "state.json"
        state_path.write_text('{"seen":{"task-2":{"state":"running"}}}', encoding="utf-8")
        task = {"id": "task-2", "file_id": "drive-file", "state": "running"}
        with mock.patch.object(runner.google_drive, "all_tasks", return_value=[task]), \
             mock.patch.object(runner.google_drive, "pending_tasks", return_value=[]), \
             mock.patch.object(runner.google_drive, "update_task") as update:
            result = runner.poll_once(state_path)
        self.assertEqual(result[0]["state"], "failed")
        self.assertEqual(task["exit_code"], 255)
        update.assert_called_once()


if __name__ == "__main__":
    unittest.main()
