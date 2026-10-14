"""Google Drive OAuth and synchronization for the EasyRead-managed library.

Credentials are deliberately kept in the local EasyRead data directory. The
Drive API is called with the narrow ``drive.file`` scope and only touches files
created by this integration.
"""
from __future__ import annotations

import hashlib
import http.server
import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .i18n import tr
from .store import read_json, write_json_atomic

API = "https://www.googleapis.com/drive/v3"
UPLOAD = "https://www.googleapis.com/upload/drive/v3"
TOKEN_URL = "https://oauth2.googleapis.com/token"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
SCOPE = "https://www.googleapis.com/auth/drive.file"
ROOT_NAME = "EasyRead"
_lock = threading.RLock()
_sync_lock = threading.Lock()
_pending: dict = {}


def credential_path() -> Path:
    return Path(os.environ.get("EASYREAD_GOOGLE_OAUTH_FILE") or config.HOME / "google-oauth-client.json")


def state_path() -> Path:
    return config.HOME / "google-drive.json"


def _credentials() -> dict:
    data = read_json(credential_path(), {}) or {}
    client = data.get("installed") or data
    if not client.get("client_id") or not client.get("client_secret"):
        raise ValueError(f"Google OAuth client file is missing or invalid: {credential_path()}")
    return {"client_id": client["client_id"], "client_secret": client["client_secret"]}


def _save_state(data: dict):
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(path, data)
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _token() -> dict:
    data = read_json(state_path(), {}) or {}
    if not data.get("refresh_token"):
        raise ValueError("Connect Google Drive first.")
    if float(data.get("expires_at", 0)) < time.time() + 60:
        creds = _credentials()
        body = urllib.parse.urlencode({"client_id": creds["client_id"], "client_secret": creds["client_secret"],
                                       "refresh_token": data["refresh_token"], "grant_type": "refresh_token"}).encode()
        reply = _http(TOKEN_URL, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
        data["access_token"] = reply["access_token"]
        data["expires_at"] = time.time() + int(reply.get("expires_in", 3600))
        _save_state(data)
    return data


def _http(url: str, *, data=None, headers=None, method=None):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            raw = response.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:1000]
        raise ValueError(f"Google Drive API {e.code}: {detail}") from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise ValueError(f"Google Drive is unavailable: {e}") from e
    return json.loads(raw) if raw else {}


def _api(method: str, path: str, *, query=None, payload=None, media=None):
    token = _token()["access_token"]
    url = API + path
    if query:
        url += "?" + urllib.parse.urlencode(query, quote_via=urllib.parse.quote)
    headers = {"Authorization": f"Bearer {token}"}
    if media is not None:
        boundary = "easyread_" + uuid.uuid4().hex
        meta = json.dumps(payload or {}, ensure_ascii=False).encode()
        body = (b"--" + boundary.encode() + b"\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n" + meta +
                b"\r\n--" + boundary.encode() + b"\r\nContent-Type: application/octet-stream\r\n\r\n" + media +
                b"\r\n--" + boundary.encode() + b"--")
        url = UPLOAD + path + "?" + urllib.parse.urlencode({**(query or {}), "uploadType": "multipart"})
        headers["Content-Type"] = f"multipart/related; boundary={boundary}"
        return _http(url, data=body, headers=headers, method=method)
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode()
        headers["Content-Type"] = "application/json; charset=UTF-8"
    else:
        data = None
    return _http(url, data=data, headers=headers, method=method)


def _list(q: str, fields="files(id,name,mimeType,md5Checksum,appProperties,modifiedTime),nextPageToken") -> list[dict]:
    files, token = [], None
    while True:
        query = {"q": q, "fields": fields, "pageSize": 1000, "spaces": "drive"}
        if token:
            query["pageToken"] = token
        result = _api("GET", "/files", query=query)
        files.extend(result.get("files", []))
        token = result.get("nextPageToken")
        if not token:
            return files


def _root(create=False) -> str:
    state = read_json(state_path(), {}) or {}
    if state.get("folder_id"):
        return state["folder_id"]
    matches = _list("appProperties has { key='easyread' and value='root' } and mimeType='application/vnd.google-apps.folder' and trashed=false")
    folder_id = matches[0]["id"] if matches else ""
    if not folder_id and create:
        folder_id = _api("POST", "/files", query={"fields": "id,name"}, payload={
            "name": ROOT_NAME, "mimeType": "application/vnd.google-apps.folder",
            "appProperties": {"easyread": "root"},
        })["id"]
    if folder_id:
        state["folder_id"] = folder_id
        _save_state(state)
    return folder_id


def status() -> dict:
    data = read_json(state_path(), {}) or {}
    connected = bool(data.get("refresh_token"))
    return {"connected": connected, "last_sync": data.get("last_sync", ""),
            "folder_id": data.get("folder_id", ""), "credential_configured": credential_path().is_file(),
            "credential_path": str(credential_path()), "message": data.get("message", ""),
            "connecting": bool(_pending.get("active")), "error": _pending.get("error", ""),
            "temporary_library": config.temp_library()}


def start_connect() -> dict:
    creds = _credentials()
    with _lock:
        if _pending.get("active"):
            return {"auth_url": _pending["auth_url"]}
        state = secrets.token_urlsafe(24)
        ready = threading.Event()
        result = {"active": True, "state": state, "ready": ready, "error": ""}

        class Callback(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                if urllib.parse.urlparse(self.path).path != "/":
                    self.send_error(404)
                    return
                query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
                if query.get("state", [""])[0] != state:
                    self.send_error(400, "OAuth state mismatch")
                    return
                error = query.get("error", [""])[0]
                code = query.get("code", [""])[0]
                try:
                    if error:
                        raise ValueError(f"Google authorization failed: {error}")
                    if not code:
                        raise ValueError("Google returned no authorization code")
                    token_body = urllib.parse.urlencode({
                        "code": code, "client_id": creds["client_id"], "client_secret": creds["client_secret"],
                        "redirect_uri": redirect, "grant_type": "authorization_code",
                    }).encode()
                    token = _http(TOKEN_URL, data=token_body, headers={"Content-Type": "application/x-www-form-urlencoded"})
                    if not token.get("refresh_token"):
                        old = read_json(state_path(), {}) or {}
                        token["refresh_token"] = old.get("refresh_token", "")
                    if not token.get("refresh_token"):
                        raise ValueError("Google did not return a refresh token. Revoke EasyRead access in Google Account and connect again.")
                    _save_state({"refresh_token": token["refresh_token"], "access_token": token.get("access_token", ""),
                                 "expires_at": time.time() + int(token.get("expires_in", 3600)),
                                 "folder_id": (read_json(state_path(), {}) or {}).get("folder_id", "")})
                    result["error"] = ""
                    body = b"EasyRead is connected to Google Drive. You can close this window."
                    self.send_response(200)
                except Exception as e:  # callback response remains readable in the browser
                    result["error"] = str(e)
                    body = str(e).encode("utf-8", "replace")
                    self.send_response(400)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                ready.set()

        server = http.server.HTTPServer(("127.0.0.1", 0), Callback)
        redirect = f"http://127.0.0.1:{server.server_address[1]}/"
        params = {"client_id": creds["client_id"], "redirect_uri": redirect, "response_type": "code",
                  "scope": SCOPE, "access_type": "offline", "prompt": "consent", "state": state}
        result["auth_url"] = AUTH_URL + "?" + urllib.parse.urlencode(params)
        _pending.clear()
        _pending.update(result)

        def serve():
            try:
                server.timeout = 1
                deadline = time.monotonic() + 600
                while not ready.is_set() and time.monotonic() < deadline:
                    server.handle_request()
                if not ready.is_set():
                    result["error"] = "Google Drive authorization timed out"
            finally:
                server.server_close()
                with _lock:
                    if _pending.get("state") == state:
                        _pending.update(active=False, error=result["error"])
        threading.Thread(target=serve, name="easyread-google-oauth", daemon=True).start()
        return {"auth_url": result["auth_url"]}


def files() -> dict[str, dict]:
    root = _root()
    if not root:
        return {}
    items = _list(f"'{root}' in parents and trashed=false")
    found = {}
    for f in items:
        if f.get("mimeType") == "application/vnd.google-apps.folder":
            continue
        found[_safe_relpath(f.get("appProperties", {}).get("easyreadPath", f.get("name", "")))] = f
    return found


def _download(file_id: str) -> bytes:
    token = _token()["access_token"]
    url = API + f"/files/{urllib.parse.quote(file_id)}?alt=media"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            return response.read()
    except urllib.error.HTTPError as e:
        raise ValueError(f"Google Drive download failed ({e.code})") from e


def _sha(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()  # Google Drive exposes md5Checksum for binary files.


def _safe_relpath(value: str) -> str:
    path = Path(value)
    if not value or value == "." or path.is_absolute() or ".." in path.parts or "\\" in value or "\x00" in value or ":" in value:
        raise ValueError(f"Unsafe path in Google Drive EasyRead folder: {value!r}")
    return path.as_posix()


def _upload(folder: str, rel: str, content: bytes, existing: dict | None):
    meta = {"name": rel, "parents": [folder], "appProperties": {"easyreadPath": rel}}
    if existing:
        return _api("PATCH", f"/files/{existing['id']}", query={"fields": "id,name,md5Checksum,appProperties"}, payload=meta, media=content)
    return _api("POST", "/files", query={"fields": "id,name,md5Checksum,appProperties"}, payload=meta, media=content)


def _read_stable(path: Path) -> bytes:
    if path.is_symlink():
        raise ValueError(f"Symbolic links are not synced: {path}")
    before = path.stat()
    data = path.read_bytes()
    after = path.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
        raise ValueError(f"File changed during Google Drive sync; retry: {path}")
    return data


def sync(library: str | Path) -> dict:
    if not _sync_lock.acquire(blocking=False):
        raise ValueError("A Google Drive sync is already running")
    try:
        return _sync(library)
    finally:
        _sync_lock.release()


def _sync(library: str | Path) -> dict:
    """Two-way sync with a per-file base snapshot; conflict versions are retained."""
    root_path = Path(library).resolve()
    folder = _root(create=True)
    remote = files()
    ignored = lambda p: not p or p.startswith(".easyread-") or p.startswith(("runner-task-", "runner-output-"))
    remote = {p: f for p, f in remote.items() if not ignored(p)}
    local: dict[str, Path] = {}
    for path in root_path.rglob("*"):
        if path.is_symlink():
            raise ValueError(f"Symbolic links are not synced: {path}")
        if path.is_file() and not path.name.startswith(".easyread-"):
            rel = path.relative_to(root_path).as_posix()
            local[rel] = path
    state = read_json(state_path(), {}) or {}
    base = state.get("sync_base", {})
    changed = {"uploaded": 0, "downloaded": 0, "deleted": 0, "conflicts": [], "unchanged": 0}
    paths = sorted(set(local) | set(remote))
    for rel in paths:
        lp, rf = local.get(rel), remote.get(rel)
        lbytes = _read_stable(lp) if lp else None
        lhash = _sha(lbytes) if lbytes is not None else ""
        rhash = (rf or {}).get("md5Checksum", "")
        prior = base.get(rel, {})
        prior_l, prior_r = prior.get("local", ""), prior.get("remote", "")
        if not lp and rf and prior_l and rhash == prior_r:
            _api("DELETE", f"/files/{rf['id']}")
            changed["deleted"] += 1
        elif not lp and rf and prior_l and rhash != prior_r:
            conflict = (root_path / rel).with_name(Path(rel).name + ".conflict-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
            conflict.parent.mkdir(parents=True, exist_ok=True)
            conflict.write_bytes(_download(rf["id"]))
            changed["conflicts"].append(rel)
        elif lp and not rf and prior_r and lhash == prior_l:
            lp.unlink(missing_ok=True)
            changed["deleted"] += 1
        elif lp and rf and lhash == rhash:
            changed["unchanged"] += 1
        elif lp and rf and lhash != rhash and (not prior_l or (lhash != prior_l and rhash != prior_r)):
            conflict = lp.with_name(lp.name + ".conflict-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
            conflict.write_bytes(_download(rf["id"]))
            changed["conflicts"].append(rel)
            _upload(folder, rel, lbytes, rf)
            changed["uploaded"] += 1
        elif rf and (not lp or (prior_r and rhash != prior_r and (not prior_l or lhash == prior_l))):
            data = _download(rf["id"])
            target = root_path / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(target.name + ".easyread-tmp")
            tmp.write_bytes(data)
            if lp and _sha(_read_stable(target)) != lhash:
                conflict = target.with_name(target.name + ".conflict-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
                conflict.write_bytes(data)
                tmp.unlink(missing_ok=True)
                changed["conflicts"].append(rel)
                continue
            os.replace(tmp, target)
            changed["downloaded"] += 1
        elif lp:
            _upload(folder, rel, lbytes, rf)
            changed["uploaded"] += 1
    current = {p: f for p, f in files().items() if not ignored(p)}
    base = {}
    for rel in sorted(set(local) | set(current)):
        lp, rf = root_path / rel, current.get(rel)
        if lp.is_file() if lp.exists() else False:
            lh = _sha(_read_stable(lp))
        else:
            lh = ""
        base[rel] = {"local": lh, "remote": (rf or {}).get("md5Checksum", "")}
    state.update({"folder_id": folder, "sync_base": base,
                  "last_sync": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                  "message": tr("已上传 {uploaded} 个文件，下载 {downloaded} 个，删除 {deleted} 个，冲突 {conflicts} 个。",
                                uploaded=changed["uploaded"], downloaded=changed["downloaded"], deleted=changed["deleted"],
                                conflicts=len(changed["conflicts"]))})
    _save_state(state)
    return {**changed, "last_sync": state["last_sync"], "message": state["message"]}


def create_task(task: dict) -> dict:
    folder = _root(create=True)
    task_id = str(task.get("id") or uuid.uuid4().hex)
    existing = _list(f"'{folder}' in parents and name='runner-task-{task_id}.json' and trashed=false")
    if existing:
        return {"id": task_id, "duplicate": True, "file_id": existing[0]["id"]}
    payload = json.dumps({**task, "id": task_id, "state": "queued"}, ensure_ascii=False).encode()
    item = _api("POST", "/files", query={"fields": "id,name"}, payload={
        "name": f"runner-task-{task_id}.json", "parents": [folder], "appProperties": {"easyreadTask": task_id},
    }, media=payload)
    return {"id": task_id, "duplicate": False, "file_id": item["id"]}


def pending_tasks() -> list[dict]:
    root = _root()
    if not root:
        return []
    state = read_json(state_path(), {}) or {}
    token = state.get("change_token")
    if not token:
        token = _api("GET", "/changes/startPageToken", query={"fields": "startPageToken"}).get("startPageToken")
        if token:
            state["change_token"] = token
            _save_state(state)
    else:
        page = token
        while page:
            changes = _api("GET", "/changes", query={"pageToken": page, "includeRemoved": "true",
                                                        "fields": "changes(fileId,removed,file(name)),newStartPageToken,nextPageToken"})
            if changes.get("newStartPageToken"):
                state["change_token"] = changes["newStartPageToken"]
                _save_state(state)
                page = None
            else:
                page = changes.get("nextPageToken")
    items = _list(f"'{root}' in parents and name contains 'runner-task-' and trashed=false")
    tasks = []
    for item in items:
        if item.get("name", "").startswith("runner-task-"):
            try:
                task = json.loads(_download(item["id"]))
                task["file_id"] = item["id"]
                tasks.append(task)
            except (ValueError, OSError, json.JSONDecodeError):
                continue
    return sorted((t for t in tasks if t.get("state") == "queued"), key=lambda t: t.get("created", ""))


def all_tasks() -> list[dict]:
    root = _root()
    if not root:
        return []
    items = _list(f"'{root}' in parents and name contains 'runner-task-' and trashed=false")
    tasks = []
    for item in items:
        if item.get("name", "").startswith("runner-task-"):
            try:
                task = json.loads(_download(item["id"]))
                task["file_id"] = item["id"]
                tasks.append(task)
            except (ValueError, OSError, json.JSONDecodeError):
                continue
    return sorted(tasks, key=lambda t: t.get("created", ""), reverse=True)


def upload_task_output(task_id: str, name: str, content: bytes) -> dict:
    folder = _root()
    safe = Path(name).as_posix().strip("/").replace("/", "__")
    filename = f"runner-output-{task_id}-{safe}"
    existing = _list(f"'{folder}' in parents and name='{filename}' and trashed=false")
    meta = {"name": filename, "parents": [folder], "appProperties": {"easyreadTask": task_id}}
    if existing:
        return _api("PATCH", f"/files/{existing[0]['id']}", query={"fields": "id,name"}, payload=meta, media=content)
    return _api("POST", "/files", query={"fields": "id,name"}, payload=meta, media=content)


def update_task(file_id: str, task: dict) -> None:
    data = json.dumps(task, ensure_ascii=False).encode()
    _api("PATCH", f"/files/{file_id}", query={"fields": "id"}, payload={}, media=data)
