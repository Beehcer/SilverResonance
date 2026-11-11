"""Private, bounded workspace snapshots. Never writes to or restores the source.

Only the production launcher starts this worker. It observes the store's actual
atomic JSON file, without taking its edit lock or importing a market SDK.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
import logging
import math
import os
from pathlib import Path
import re
import secrets
import stat
import tarfile
import tempfile
import threading
import time


LOGGER = logging.getLogger("silver.backup")
FORMAT = "silver-workspace-backup-v1"
POLL_SECONDS = 5
CHANGE_SECONDS = 30
SCHEDULE_SECONDS = 30 * 60
RETENTION = {"change": 40, "scheduled": 10}
MAX_SOURCE_BYTES = 64 * 1024 * 1024
NAME = re.compile(r"workspace-(\d{8}T\d{12}Z)-(change|scheduled)-([0-9a-f]{12})\.tar\.gz\Z")
_VALIDATOR = None
_VALIDATOR_LOCK = threading.Lock()


def _signature(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _regular(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
        raise ValueError("not a regular private file")
    return info


def _json(data):
    def invalid(_):
        raise ValueError("invalid JSON constant")
    return json.loads(data, parse_constant=invalid)


def _semantic_hash(state):
    # Session refresh and idempotency receipts are not edits. Still archive all
    # original bytes, including them, when a real change or timer is due.
    meaningful = {key: value for key, value in state.items() if key not in ("sessions", "mutations", "members")}
    meaningful["members"] = {identity: {key: value for key, value in member.items() if key != "last_seen"}
                             for identity, member in state["members"].items()}
    digest = hashlib.sha256()
    for chunk in json.JSONEncoder(ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).iterencode(meaningful):
        digest.update(chunk.encode("utf-8"))
    return digest.hexdigest()


def _validate_workspace(state):
    global _VALIDATOR
    if _VALIDATOR is None:
        with _VALIDATOR_LOCK:
            if _VALIDATOR is None:
                from workspace_store import WorkspaceStore
                _VALIDATOR = WorkspaceStore()
    _VALIDATOR._validate_state(state)


def _sync_directory(directory):
    if os.name != "nt":
        descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def verify_archive(path):
    """Verify privately, without extracting paths or returning user content."""
    _regular(Path(path))
    manifest = None
    payload = None
    with tarfile.open(path, mode="r|gz") as archive:
        names = []
        for member in archive:
            if (not member.isfile() or member.size < 0 or member.name not in ("manifest.json", "workspace.json")
                    or member.name in names or member.size > (8192 if member.name == "manifest.json" else MAX_SOURCE_BYTES)):
                raise ValueError("invalid backup members")
            names.append(member.name)
            with archive.extractfile(member) as stream:
                value = stream.read(member.size + 1)
            if len(value) != member.size:
                raise ValueError("incomplete backup member")
            if member.name == "manifest.json":
                manifest = _json(value)
            else:
                payload = value
    if set(names) != {"manifest.json", "workspace.json"} or not isinstance(manifest, dict):
        raise ValueError("incomplete backup")
    if (manifest.get("format") != FORMAT or manifest.get("kind") not in RETENTION
            or type(manifest.get("created")) not in (int, float) or not math.isfinite(manifest["created"])
            or not isinstance(manifest.get("semantic_sha256"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", manifest["semantic_sha256"])
            or manifest.get("source_size") != len(payload)
            or manifest.get("sha256") != hashlib.sha256(payload).hexdigest()):
        raise ValueError("backup verification failed")
    state = _json(payload)
    _validate_workspace(state)
    if not isinstance(state, dict) or state.get("schema_version") != 2 or _semantic_hash(state) != manifest["semantic_sha256"]:
        raise ValueError("backup content mismatch")
    return manifest


class WorkspaceBackup:
    def __init__(self, source, directory=None, *, clock=time.time):
        self.source = Path(source).absolute()
        self.directory = Path(directory).absolute() if directory else self.source.parent.parent / "backups" / "workspace"
        self.clock = clock
        self.stop_event = threading.Event()
        self.thread = None
        self._mutex = threading.Lock()
        self._seen_signature = None
        self._initialized = False
        self._retention_pending = False
        self._verified = {}
        self._invalid = []
        self._last_attempt = -math.inf
        self._last_snapshot = -math.inf
        self._last_scheduled = -math.inf
        self._semantic = None
        self.status = {"state": "waiting", "last_success": None, "last_error": None}

    def _ensure_directory(self):
        for path in reversed((self.directory, *self.directory.parents)):
            if path.exists() or path.is_symlink():
                info = path.lstat()
                if not stat.S_ISDIR(info.st_mode) or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
                    raise ValueError("backup directory must not be a link")
        self.directory.mkdir(parents=True, mode=0o700, exist_ok=True)
        for path in (self.directory, self.directory.parent):
            info = path.lstat()
            if not stat.S_ISDIR(info.st_mode) or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
                raise ValueError("backup directory must not be a link")
        os.chmod(self.directory, 0o700)

    @contextmanager
    def _publisher_lock(self):
        path = self.directory / ".publisher.lock"
        if path.exists():
            _regular(path)
        descriptor = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(descriptor, "r+b") as handle:
            acquired = False
            try:
                if os.name == "nt":
                    import msvcrt
                    if os.fstat(handle.fileno()).st_size == 0:
                        handle.write(b"0")
                        handle.flush()
                    handle.seek(0)
                    try:
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                        acquired = True
                    except OSError:
                        pass
                else:
                    import fcntl
                    try:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        acquired = True
                    except BlockingIOError:
                        pass
                yield acquired
            finally:
                if acquired:
                    if os.name == "nt":
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _records(self):
        records = []
        existing = set()
        self._invalid = []
        for path in self.directory.iterdir():
            match = NAME.fullmatch(path.name)
            if not match:
                continue
            signature = _signature(_regular(path))
            existing.add(path)
            created = datetime.strptime(match[1], "%Y%m%dT%H%M%S%fZ").replace(tzinfo=timezone.utc).timestamp()
            cached = self._verified.get(path)
            if cached is None or cached[0] != signature:
                try:
                    manifest = verify_archive(path)
                    if manifest["created"] != created or manifest["kind"] != match[2] or _signature(_regular(path)) != signature:
                        raise ValueError("archive changed or filename mismatch")
                except (OSError, ValueError, KeyError, TypeError, tarfile.TarError, EOFError):
                    manifest = None
                self._verified[path] = signature, manifest
            else:
                manifest = cached[1]
            if manifest is None:
                self._invalid.append((path, signature))
                continue
            records.append({"path": path, "created": created, "kind": match[2], "manifest": manifest})
        self._verified = {path: value for path, value in self._verified.items() if path in existing}
        return sorted(records, key=lambda item: (item["created"], item["path"].name))

    def _refresh_baseline(self, records):
        # Read only a newly published latest archive, not all fifty every poll.
        if not records:
            self._last_snapshot = self._last_scheduled = -math.inf
            self._semantic = None
            return
        if records[-1]["created"] <= self._last_snapshot:
            return
        manifest = records[-1]["manifest"]
        self._last_snapshot = manifest["created"]
        self._semantic = manifest["semantic_sha256"]
        self.status["last_success"] = self._last_snapshot
        self._last_scheduled = max((item["created"] for item in records if item["kind"] == "scheduled"), default=-math.inf)

    def _capture(self):
        expected = _regular(self.source)
        if expected.st_size > MAX_SOURCE_BYTES:
            raise ValueError("workspace exceeds backup size limit")
        with self.source.open("rb") as stream:
            before = os.fstat(stream.fileno())
            if _signature(before)[:4] != _signature(expected)[:4]:
                raise ValueError("workspace changed before capture")
            raw = stream.read(MAX_SOURCE_BYTES + 1)
            after = os.fstat(stream.fileno())
        if len(raw) > MAX_SOURCE_BYTES or _signature(before) != _signature(after):
            raise ValueError("workspace changed during capture")
        state = _json(raw)
        # Use the same full schema/invariant checks as the authoritative store.
        # Validation happens on this background thread, without the edit lock.
        _validate_workspace(state)
        return raw, _semantic_hash(state), _signature(expected)

    def _publish(self, raw, semantic, kind, now):
        # Round to filename precision so restart comparisons remain exact.
        now = round(now, 6)
        label = datetime.fromtimestamp(now, timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        final = self.directory / ("workspace-" + label + "-" + kind + "-" + secrets.token_hex(6) + ".tar.gz")
        manifest = {"format": FORMAT, "created": now, "kind": kind,
                    "source_size": len(raw), "sha256": hashlib.sha256(raw).hexdigest(), "semantic_sha256": semantic}
        descriptor, temporary = tempfile.mkstemp(prefix=".workspace-backup-", suffix=".tmp", dir=self.directory)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                with gzip.GzipFile(fileobj=stream, mode="wb", filename="", compresslevel=1, mtime=0) as compressed:
                    with tarfile.open(fileobj=compressed, mode="w|") as archive:
                        for name, content in (("manifest.json", json.dumps(manifest, separators=(",", ":")).encode()), ("workspace.json", raw)):
                            info = tarfile.TarInfo(name)
                            info.size, info.mode, info.mtime = len(content), 0o600, int(now)
                            archive.addfile(info, io.BytesIO(content))
                stream.flush()
                os.fsync(stream.fileno())
            if verify_archive(temporary) != manifest:
                raise ValueError("new backup failed verification")
            os.replace(temporary, final)
            _sync_directory(self.directory)
            self._verified[final] = _signature(_regular(final)), manifest
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return final, manifest

    def _prune(self, records):
        for kind, maximum in RETENTION.items():
            group = [item for item in records if item["kind"] == kind]
            for item in group[:-maximum]:
                # Only direct, regular managed archives in this private folder.
                _regular(item["path"])
                item["path"].unlink()
        # Invalid archives never take valid retention slots. Remove only known
        # corrupt managed files after a verified recovery point is available.
        if records:
            for path, signature in self._invalid:
                if _signature(_regular(path)) == signature:
                    path.unlink()
        _sync_directory(self.directory)

    def _healthy_status(self, records, now):
        self.status = {"state": "ok", "last_success": self._last_snapshot, "last_attempt": now,
                       "last_error": None, "counts": {kind: sum(item["kind"] == kind for item in records) for kind in RETENTION}}
        self._save_status()

    def _save_status(self):
        descriptor, temporary = tempfile.mkstemp(prefix=".workspace-status-", suffix=".tmp", dir=self.directory)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(self.status, stream, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.directory / "status.json")
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def tick(self):
        """Best-effort check. All failures stay outside edit/market execution."""
        with self._mutex:
            now = self.clock()
            if now - max(self._last_attempt, self._last_snapshot) < CHANGE_SECONDS:
                return False
            try:
                if self._initialized and not self._retention_pending and now - self._last_scheduled < SCHEDULE_SECONDS:
                    try:
                        if _signature(_regular(self.source)) == self._seen_signature:
                            return False
                    except FileNotFoundError:
                        if self._seen_signature is not None or math.isfinite(self._last_snapshot):
                            raise
                        return False
                self._ensure_directory()
                with self._publisher_lock() as acquired:
                    if not acquired:
                        return False
                    records = self._records()
                    self._refresh_baseline(records)
                    self._initialized = True
                    self._retention_pending = self._retention_pending or bool(self._invalid) or any(sum(item["kind"] == kind for item in records) > limit for kind, limit in RETENTION.items())
                    if self._retention_pending and records:
                        self._prune(records)
                        records = self._records()
                        self._healthy_status(records, now)
                        self._retention_pending = False
                    if now - self._last_snapshot < CHANGE_SECONDS:
                        return False
                    scheduled = now - self._last_scheduled >= SCHEDULE_SECONDS
                    try:
                        signature = _signature(_regular(self.source))
                    except FileNotFoundError:
                        if self._seen_signature is not None or records:
                            raise
                        return False
                    if not scheduled and signature == self._seen_signature:
                        return False
                    raw, semantic, signature = self._capture()
                    if not scheduled and semantic == self._semantic:
                        self._seen_signature = signature
                        return False
                    self._last_attempt = now
                    final, manifest = self._publish(raw, semantic, "scheduled" if scheduled else "change", now)
                    self._last_snapshot, self._semantic = manifest["created"], semantic
                    self._seen_signature = signature
                    self.status["last_success"] = manifest["created"]
                    if scheduled:
                        self._last_scheduled = manifest["created"]
                    self._retention_pending = True
                    self._prune(self._records())
                    records = self._records()
                    self._healthy_status(records, now)
                    self._retention_pending = False
                    return True
            except Exception as error:
                self._last_attempt = now
                self.status.update(state="failed", last_attempt=now, last_error=type(error).__name__)
                LOGGER.warning("工作区自动备份暂未完成（%s），编辑与行情继续运行。", type(error).__name__)
                try:
                    self._save_status()
                except Exception:
                    pass
                return False

    def _run(self):
        while not self.stop_event.is_set():
            self.tick()
            self.stop_event.wait(POLL_SECONDS)

    def start(self):
        if self.thread is None:
            self.thread = threading.Thread(target=self._run, name="workspace-backup", daemon=True)
            self.thread.start()
        return self

    def stop(self, timeout=2):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout)


def start_workspace_backup(source):
    """Launcher boundary: backup setup failure must not prevent service startup."""
    try:
        return WorkspaceBackup(source).start() if source else None
    except Exception as error:
        LOGGER.warning("工作区自动备份未启动（%s），编辑与行情继续运行。", type(error).__name__)
        return None
