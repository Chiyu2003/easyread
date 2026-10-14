"""Long-running lab runner: poll Drive task manifests and execute Docker over SSH."""
from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from . import config, experiments, google_drive
from .store import read_json, write_json_atomic


def _render(profile: dict, params: dict, output_dir: Path, paper_id: str) -> list[str]:
    values = {k: str(v) for k, v in params.items()}
    values.update({"paper_id": paper_id, "output_dir": str(output_dir)})
    for name, definition in profile.get("params", {}).items():
        if name not in values and "default" in definition:
            values[name] = str(definition["default"])
    command = []
    for arg in profile["command_template"]:
        try:
            command.append(arg.format_map(values))
        except KeyError as e:
            raise ValueError(f"Unknown command template placeholder: {e.args[0]}") from e
    return command


def _remote_command(profile: dict, params: dict, paper_id: str, remote_output: str) -> str:
    docker = ["docker", "run", "--rm", "--network", profile.get("network", "none"), "-v", f"{remote_output}:{profile['output_path']}"]
    for mount in profile.get("mounts", []):
        docker.extend(["-v", f"{mount['host']}:{mount['container']}"])
    docker.extend([profile["docker_image"], *_render(profile, params, Path(profile["output_path"]), paper_id)])
    return shlex.join(docker)


def _run_one(task: dict, state: dict, state_file: Path, once=False) -> dict:
    task_id = str(task.get("id", ""))
    if not task_id:
        return {"id": task_id, "error": "Task has no ID"}
    previous = state.get("seen", {}).get(task_id)
    if previous:
        if previous.get("state") == "running" and task.get("state") == "queued":
            task.update({"state": "failed", "finished": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                         "exit_code": 255, "log": "Runner restarted after claiming this task; automatic rerun is disabled to prevent duplicate experiments."})
            google_drive.update_task(task["file_id"], task)
            state["seen"][task_id] = {"state": "failed", "at": task["finished"], "exit_code": 255}
            write_json_atomic(state_file, state)
            return {"id": task_id, "state": "failed", "exit_code": 255}
        return {"id": task_id, "duplicate": True}
    file_id = task.get("file_id")
    task.update({"state": "running", "started": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 "runner_host": os.uname().nodename if hasattr(os, "uname") else os.environ.get("COMPUTERNAME", "runner")})
    state.setdefault("seen", {})[task_id] = {"state": "running", "at": task["started"]}
    write_json_atomic(state_file, state)  # durable idempotency before SSH starts
    google_drive.update_task(file_id, task)
    run_dir = config.HOME / "experiments" / "outputs" / task_id
    run_dir.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / "run.log"
    code, outputs, log = 1, [], ""
    try:
        profile = experiments.validate_profile(task["profile"])
        remote_output = profile["host_output_path"].rstrip("/") + "/" + task_id
        ssh = ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", profile["ssh_alias"]]
        subprocess.run([*ssh, shlex.join(["mkdir", "-p", remote_output])], check=True, timeout=30,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
        remote = _remote_command(profile, task.get("params") or {}, str(task.get("paper_id") or ""), remote_output)
        proc = subprocess.run([*ssh, remote], check=False, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", timeout=int(task.get("timeout", 86400)))
        code, log = proc.returncode, proc.stdout
        downloaded = subprocess.run(["scp", "-r", f"{profile['ssh_alias']}:{remote_output}/.", str(run_dir)], check=False,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", timeout=600)
        if downloaded.returncode:
            log += "\nOutput retrieval failed: " + downloaded.stdout
        outputs = [str(p.relative_to(run_dir)) for p in run_dir.rglob("*") if p.is_file() and not p.is_symlink()
                   and p.resolve().is_relative_to(run_dir.resolve()) and p != log_path]
    except subprocess.TimeoutExpired as e:
        raw = e.stdout or b""
        partial = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
        code, log = 124, partial + f"\nExperiment timed out after {e.timeout} seconds."
    except subprocess.CalledProcessError as e:
        raw = e.stdout or b""
        partial = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
        code, log = e.returncode or 255, partial + f"\nSSH command failed: {e}"
    except Exception as e:  # failures are recorded as runs, including SSH/network failures
        raw = getattr(e, "stdout", b"") or b""
        partial = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
        code, log = 255, partial + f"\n{type(e).__name__}: {e}"
    log_path.write_text(log, encoding="utf-8")
    task.update({"state": "succeeded" if code == 0 else "failed", "finished": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                 "exit_code": code, "log": log[-80000:], "outputs": outputs})
    output_files = []
    try:
        output_files.append(google_drive.upload_task_output(task_id, "run.log", log.encode("utf-8")))
        for rel in outputs:
            p = run_dir / rel
            output_files.append(google_drive.upload_task_output(task_id, rel, p.read_bytes()))
        task["output_files"] = [{"name": "run.log", "file_id": output_files[0]["id"]}]
        task["output_files"].extend({"name": name, "file_id": ref["id"]} for name, ref in zip(outputs, output_files[1:]))
    except Exception as e:
        task["state"], task["exit_code"] = "failed", 254
        task["log"] = (task.get("log", "") + f"\nOutput upload failed: {e}")[-80000:]
    seen_result = {"state": task["state"], "at": task["finished"], "exit_code": task["exit_code"],
                   "finished": task["finished"], "log": task.get("log", ""), "outputs": outputs,
                   "output_files": task.get("output_files", [])}
    state["seen"][task_id] = seen_result
    write_json_atomic(state_file, state)
    google_drive.update_task(file_id, task)
    return {"id": task_id, "state": task["state"], "exit_code": task["exit_code"], "outputs": outputs}


def poll_once(state_file: Path | None = None) -> list[dict]:
    state_file = state_file or config.HOME / "experiments" / "runner-state.json"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state = read_json(state_file, {}) or {"seen": {}}
    result = []
    lock_path = state_file.with_suffix(".lock")
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        try:
            stale = json.loads(lock_path.read_text(encoding="utf-8"))
            pid = int(stale.get("pid", 0))
            created = float(stale.get("created", 0))
            try:
                os.kill(pid, 0)
                alive = True
            except ProcessLookupError:
                alive = False
            except PermissionError:
                alive = True
            if alive or time.time() - created < 26 * 60 * 60:
                return [{"busy": True}]
            lock_path.unlink(missing_ok=True)
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except (OSError, ValueError, TypeError):
            try:
                if time.time() - lock_path.stat().st_mtime < 26 * 60 * 60:
                    return [{"busy": True}]
                lock_path.unlink(missing_ok=True)
                fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except OSError:
                return [{"busy": True}]
    os.write(fd, json.dumps({"pid": os.getpid(), "created": time.time()}).encode())
    os.close(fd)
    try:
        state = read_json(state_file, {}) or {"seen": {}}
        # Reconcile a completed local result if the runner lost its connection
        # before Drive received the final manifest update.
        for task in google_drive.all_tasks():
            recorded = state.get("seen", {}).get(task.get("id", ""))
            if task.get("state") == "running" and recorded and recorded.get("state") == "running":
                task.update({"state": "failed", "finished": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                             "exit_code": 255, "log": "Runner restarted while this task was running; automatic rerun is disabled to prevent duplicate experiments."})
                state["seen"][task["id"]] = {"state": "failed", "at": task["finished"], "exit_code": 255,
                                              "finished": task["finished"], "log": task["log"], "outputs": task.get("outputs", [])}
                write_json_atomic(state_file, state)
                try:
                    google_drive.update_task(task["file_id"], task)
                    result.append({"id": task["id"], "state": "failed", "exit_code": 255})
                except Exception as e:
                    result.append({"id": task["id"], "error": str(e)})
                continue
            if task.get("state") == "running" and recorded and recorded.get("state") in ("succeeded", "failed"):
                task.update({k: recorded.get(k) for k in ("state", "finished", "exit_code", "log", "outputs")})
                try:
                    google_drive.update_task(task["file_id"], task)
                    result.append({"id": task["id"], "state": task["state"], "reconciled": True})
                except Exception as e:
                    result.append({"id": task["id"], "error": str(e)})
        for task in google_drive.pending_tasks():
            try:
                result.append(_run_one(task, state, state_file))
            except Exception as e:
                result.append({"id": task.get("id", ""), "error": str(e)})
    finally:
        lock_path.unlink(missing_ok=True)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="poll and process queued jobs once")
    parser.add_argument("--poll-seconds", type=int, default=30)
    args = parser.parse_args(argv)
    if args.once:
        print(json.dumps(poll_once(), ensure_ascii=False))
        return 0
    while True:
        try:
            for result in poll_once():
                print(json.dumps(result, ensure_ascii=False), flush=True)
        except Exception as e:
            print(f"runner poll failed: {e}", file=sys.stderr, flush=True)
        time.sleep(max(5, args.poll_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
