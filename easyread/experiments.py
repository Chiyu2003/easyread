"""Experiment profiles and run manifests shared by EasyRead and its runner."""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .store import read_json, write_json_atomic

PROFILE_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$")
SSH_ALIAS = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$")
PARAM = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]{0,63}$")
PARAM_TYPES = {"string", "integer", "number", "boolean"}


def _validate_param_value(name: str, value, value_type: str) -> None:
    valid = {
        "string": lambda x: isinstance(x, str),
        "integer": lambda x: isinstance(x, int) and not isinstance(x, bool),
        "number": lambda x: isinstance(x, (int, float)) and not isinstance(x, bool),
        "boolean": lambda x: isinstance(x, bool),
    }[value_type](value)
    if not valid:
        raise ValueError(f"Parameter {name} must be a {value_type}")


def profiles_path() -> Path:
    return config.HOME / "experiments" / "profiles.json"


def runs_path() -> Path:
    return config.HOME / "experiments" / "runs"


def validate_profile(profile: dict) -> dict:
    if not isinstance(profile, dict):
        raise ValueError("Profile must be a JSON object")
    item = dict(profile)
    pid = str(item.get("id") or uuid.uuid4().hex[:12])
    if not PROFILE_ID.fullmatch(pid):
        raise ValueError("Profile id may contain letters, numbers, _ and -")
    alias = str(item.get("ssh_alias", ""))
    if not SSH_ALIAS.fullmatch(alias):
        raise ValueError("SSH alias must match an alias in the runner host's OpenSSH config")
    image = str(item.get("docker_image", ""))
    if not image or not re.fullmatch(r"[\w./:@+-]+", image):
        raise ValueError("Docker image is required and may contain only image name characters")
    command = item.get("command_template", [])
    if not isinstance(command, list) or not command or any(not isinstance(x, str) or len(x) > 4096 for x in command):
        raise ValueError("Command template must be a non-empty array of arguments")
    params = item.get("params", {})
    if not isinstance(params, dict) or any(not PARAM.fullmatch(str(k)) or not isinstance(v, dict) for k, v in params.items()):
        raise ValueError("Parameter names must start with a letter and contain only letters, numbers or _")
    for name, definition in params.items():
        value_type = definition.get("type", "string")
        if value_type not in PARAM_TYPES:
            raise ValueError(f"Parameter {name} type must be one of: {', '.join(sorted(PARAM_TYPES))}")
        if "required" in definition and not isinstance(definition["required"], bool):
            raise ValueError(f"Parameter {name} required must be true or false")
        if "default" in definition:
            _validate_param_value(name, definition["default"], value_type)
    mounts = item.get("mounts", [])
    if not isinstance(mounts, list):
        raise ValueError("Mounts must be an array")
    for mount in mounts:
        if not isinstance(mount, dict) or not str(mount.get("host", "")).startswith("/") or not str(mount.get("container", "")).startswith("/") or any(c in str(mount.get("host", "")) + str(mount.get("container", "")) for c in "\r\n:"):
            raise ValueError("Each mount needs absolute host and container paths")
    output = str(item.get("output_path", "/workspace/output"))
    if not output.startswith("/"):
        raise ValueError("Output path must be absolute inside the container")
    host_output = str(item.get("host_output_path", "/var/tmp/easyread-runs"))
    if not host_output.startswith("/") or not re.fullmatch(r"/[A-Za-z0-9_./-]+", host_output):
        raise ValueError("Host output path must be an absolute path on the SSH host")
    network = str(item.get("network", "none"))
    if network not in ("none", "bridge", "host"):
        raise ValueError("Docker network must be none, bridge or host")
    return {"id": pid, "name": str(item.get("name") or pid)[:120], "ssh_alias": alias,
            "docker_image": image, "command_template": command, "params": params,
            "mounts": mounts, "output_path": output, "host_output_path": host_output, "network": network}


def list_profiles() -> list[dict]:
    data = read_json(profiles_path(), {}) or {}
    return list(data.get("profiles", {}).values())


def save_profile(profile: dict) -> dict:
    item = validate_profile(profile)
    path = profiles_path()
    data = read_json(path, {}) or {"profiles": {}}
    data.setdefault("profiles", {})[item["id"]] = item
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(path, data)
    return item


def get_profile(profile_id: str) -> dict:
    data = read_json(profiles_path(), {}) or {}
    profile = (data.get("profiles") or {}).get(profile_id)
    if not profile:
        raise ValueError("Experiment profile was not found")
    return validate_profile(profile)


def new_run(profile: dict, paper_id: str, params: dict) -> dict:
    item = validate_profile(profile)
    if not isinstance(params, dict) or set(params) - set(item["params"]):
        raise ValueError("Run parameters must match the profile definitions")
    for name, definition in item["params"].items():
        if name not in params:
            if "default" in definition:
                params[name] = definition["default"]
            elif definition.get("required", True):
                raise ValueError(f"Missing required parameter: {name}")
        if name in params:
            _validate_param_value(name, params[name], definition.get("type", "string"))
    run_id = uuid.uuid4().hex
    run = {"id": run_id, "profile_id": item["id"], "profile": item, "paper_id": str(paper_id),
           "params": params, "state": "queued", "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "started": "", "finished": "", "exit_code": None, "log": "", "outputs": []}
    folder = runs_path()
    folder.mkdir(parents=True, exist_ok=True)
    write_json_atomic(folder / f"{run_id}.json", run)
    return run


def list_runs(limit=40) -> list[dict]:
    folder = runs_path()
    if not folder.is_dir():
        return []
    runs = []
    for path in folder.glob("*.json"):
        item = read_json(path, {}) or {}
        if item:
            runs.append({k: v for k, v in item.items() if k != "profile"})
    return sorted(runs, key=lambda x: x.get("created", ""), reverse=True)[:limit]


def save_run(run: dict):
    path = runs_path() / f"{run['id']}.json"
    write_json_atomic(path, run)

