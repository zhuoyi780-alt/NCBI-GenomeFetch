"""Persistent state for resumable MD5 auto-fix workflows."""

import hashlib
import json
import os
import socket
import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional

from taxonomy_downloader.md5_autofix_models import FailedFile


SCHEMA_VERSION = 1


class AutoFixStateError(Exception):
    """Raised when auto-fix state cannot be read or written safely."""


class AutoFixLockError(AutoFixStateError):
    """Raised when another auto-fix process already holds the state lock."""


class AutoFixTaskStatus(str, Enum):
    """Per-file auto-fix task states."""

    PENDING = "PENDING"
    SKIPPED_NO_ACCESSION = "SKIPPED_NO_ACCESSION"
    DOWNLOAD_QUEUED = "DOWNLOAD_QUEUED"
    DOWNLOADING = "DOWNLOADING"
    DOWNLOADED = "DOWNLOADED"
    ORGANIZING = "ORGANIZING"
    ORGANIZED = "ORGANIZED"
    VERIFYING = "VERIFYING"
    FIXED = "FIXED"
    STILL_FAILED = "STILL_FAILED"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_FATAL = "FAILED_FATAL"


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _path_to_posix(path: Path) -> str:
    return path.as_posix()


def _safe_relative_path(path: Path, root: Path) -> str:
    try:
        return _path_to_posix(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return _path_to_posix(path)


def generate_task_id(verification_dir: Path, failed_file: FailedFile) -> str:
    """Generate a stable task id from the failed file context."""

    normalized_verification_dir = str(Path(verification_dir).resolve())
    md5sum_dir = _path_to_posix(Path(failed_file.md5sum_dir))
    raw = "\0".join(
        [
            normalized_verification_dir,
            md5sum_dir,
            failed_file.md5_file_path,
            failed_file.expected_hash or "",
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass
class AutoFixTask:
    """Persistent state for one failed MD5 file."""

    task_id: str
    status: AutoFixTaskStatus
    md5_file_path: str
    md5sum_dir: str
    original_path: str
    expected_hash: str
    initial_status: str
    accession_id: Optional[str] = None
    target_path: Optional[str] = None
    cached_file: Optional[str] = None
    backup_path: Optional[str] = None
    attempts: int = 0
    last_error: Optional[str] = None
    computed_hash: Optional[str] = None
    created_at: str = field(default_factory=_utc_now)
    updated_at: str = field(default_factory=_utc_now)

    @classmethod
    def from_failed_file(
        cls, verification_dir: Path, failed_file: FailedFile
    ) -> "AutoFixTask":
        task_id = generate_task_id(verification_dir, failed_file)
        original_path = _path_to_posix(Path(failed_file.original_path))
        md5sum_dir = _path_to_posix(Path(failed_file.md5sum_dir))
        return cls(
            task_id=task_id,
            status=AutoFixTaskStatus.PENDING,
            accession_id=failed_file.accession_id,
            md5_file_path=failed_file.md5_file_path,
            md5sum_dir=md5sum_dir,
            original_path=original_path,
            target_path=original_path,
            expected_hash=failed_file.expected_hash or "",
            initial_status=failed_file.status.value,
            last_error=failed_file.error_message,
        )

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AutoFixTask":
        values = dict(data)
        values["status"] = AutoFixTaskStatus(values["status"])
        return cls(**values)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "status": self.status.value,
            "accession_id": self.accession_id,
            "md5_file_path": self.md5_file_path,
            "md5sum_dir": self.md5sum_dir,
            "original_path": self.original_path,
            "target_path": self.target_path,
            "expected_hash": self.expected_hash,
            "initial_status": self.initial_status,
            "cached_file": self.cached_file,
            "backup_path": self.backup_path,
            "attempts": self.attempts,
            "last_error": self.last_error,
            "computed_hash": self.computed_hash,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class AutoFixState:
    """Top-level persistent auto-fix state."""

    run_id: str
    verification_dir: str
    input_fingerprint: Dict[str, Any]
    created_at: str = field(default_factory=_utc_now)
    updated_at: str = field(default_factory=_utc_now)
    schema_version: int = SCHEMA_VERSION
    tool: str = "ncbi-genomefetch"
    mode: str = "md5_autofix"
    summary: Dict[str, int] = field(default_factory=dict)
    tasks: Dict[str, AutoFixTask] = field(default_factory=dict)

    @classmethod
    def create(
        cls, verification_dir: Path, input_fingerprint: Dict[str, Any]
    ) -> "AutoFixState":
        timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        seed = hashlib.sha256(
            (str(Path(verification_dir).resolve()) + timestamp).encode("utf-8")
        ).hexdigest()[:8]
        return cls(
            run_id=f"{timestamp}-{seed}",
            verification_dir=str(Path(verification_dir).resolve()),
            input_fingerprint=dict(input_fingerprint),
        )

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AutoFixState":
        if data.get("schema_version") != SCHEMA_VERSION:
            raise AutoFixStateError(
                "Unsupported auto-fix state schema version: "
                f"{data.get('schema_version')}"
            )
        tasks = {
            task_id: AutoFixTask.from_dict(task_data)
            for task_id, task_data in data.get("tasks", {}).items()
        }
        return cls(
            schema_version=data["schema_version"],
            tool=data.get("tool", "ncbi-genomefetch"),
            mode=data.get("mode", "md5_autofix"),
            run_id=data["run_id"],
            verification_dir=data["verification_dir"],
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            input_fingerprint=data.get("input_fingerprint", {}),
            summary=data.get("summary", {}),
            tasks=tasks,
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "tool": self.tool,
            "mode": self.mode,
            "run_id": self.run_id,
            "verification_dir": self.verification_dir,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "input_fingerprint": self.input_fingerprint,
            "summary": self.summary,
            "tasks": {
                task_id: task.to_dict() for task_id, task in self.tasks.items()
            },
        }


class AutoFixStateStore:
    """Read, write, and recover `.md5_autofix_state` contents."""

    def __init__(self, verification_dir: Path):
        self.verification_dir = Path(verification_dir)
        self.state_dir = self.verification_dir / ".md5_autofix_state"
        self.state_file = self.state_dir / "autofix_state.json"
        self.tmp_state_file = self.state_dir / "autofix_state.json.tmp"
        self.lock_file = self.state_dir / "lock"
        self.downloads_dir = self.state_dir / "downloads"
        self.staging_dir = self.state_dir / "staging"
        self.backups_dir = self.state_dir / "backups"
        self.reports_dir = self.state_dir / "reports"
        self.state: Optional[AutoFixState] = None

    def acquire_lock(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "pid": os.getpid(),
            "hostname": socket.gethostname(),
            "created_at": _utc_now(),
            "command": " ".join(sys.argv),
        }
        try:
            fd = os.open(str(self.lock_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                lock_info = self.lock_file.read_text(encoding="utf-8")
            except OSError:
                lock_info = "<unreadable>"
            raise AutoFixLockError(
                f"Existing MD5 auto-fix lock found at {self.lock_file}:\n{lock_info}"
            )
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)

    def release_lock(self) -> None:
        try:
            self.lock_file.unlink()
        except FileNotFoundError:
            return

    @contextmanager
    def locked(self) -> Iterator[None]:
        self.acquire_lock()
        try:
            yield
        finally:
            self.release_lock()

    def load_or_create(
        self, failed_files: List[FailedFile], input_fingerprint: Dict[str, Any]
    ) -> AutoFixState:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.downloads_dir.mkdir(parents=True, exist_ok=True)
        self.staging_dir.mkdir(parents=True, exist_ok=True)
        self.backups_dir.mkdir(parents=True, exist_ok=True)
        self.reports_dir.mkdir(parents=True, exist_ok=True)

        state = self._load_existing_state()
        if state is None:
            state = AutoFixState.create(self.verification_dir, input_fingerprint)

        for failed_file in failed_files:
            task = AutoFixTask.from_failed_file(self.verification_dir, failed_file)
            if task.task_id not in state.tasks:
                state.tasks[task.task_id] = task
            else:
                existing = state.tasks[task.task_id]
                if failed_file.accession_id and not existing.accession_id:
                    existing.accession_id = failed_file.accession_id
                    existing.updated_at = _utc_now()

        self.state = state
        self.save()
        return state

    def _load_existing_state(self) -> Optional[AutoFixState]:
        if not self.state_file.exists():
            return None
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
            return AutoFixState.from_dict(data)
        except json.JSONDecodeError:
            corrupt_path = self._corrupt_state_path()
            os.replace(str(self.state_file), str(corrupt_path))
            return None

    def _corrupt_state_path(self) -> Path:
        timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        return self.state_dir / f"autofix_state.json.corrupt.{timestamp}"

    def save(self) -> None:
        if self.state is None:
            raise AutoFixStateError("No auto-fix state loaded")
        self.state.updated_at = _utc_now()
        self.state.summary = self.recompute_summary()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        with open(self.tmp_state_file, "w", encoding="utf-8") as fh:
            json.dump(self.state.to_dict(), fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(str(self.tmp_state_file), str(self.state_file))

    def update_task(self, task_id: str, **changes: Any) -> None:
        if self.state is None:
            raise AutoFixStateError("No auto-fix state loaded")
        task = self.state.tasks[task_id]
        for key, value in changes.items():
            if key == "status":
                value = (
                    value
                    if isinstance(value, AutoFixTaskStatus)
                    else AutoFixTaskStatus(value)
                )
            setattr(task, key, value)
        task.updated_at = _utc_now()
        self.save()

    def tasks_by_status(
        self, *statuses: AutoFixTaskStatus
    ) -> List[AutoFixTask]:
        if self.state is None:
            return []
        normalized = {
            status if isinstance(status, AutoFixTaskStatus) else AutoFixTaskStatus(status)
            for status in statuses
        }
        return [task for task in self.state.tasks.values() if task.status in normalized]

    def recover_interrupted_tasks(self) -> None:
        if self.state is None:
            raise AutoFixStateError("No auto-fix state loaded")

        for task in list(self.state.tasks.values()):
            if not task.accession_id:
                if task.status not in (
                    AutoFixTaskStatus.FIXED,
                    AutoFixTaskStatus.SKIPPED_NO_ACCESSION,
                ):
                    self._set_task_without_saving(
                        task,
                        AutoFixTaskStatus.SKIPPED_NO_ACCESSION,
                        "No accession id found for failed file",
                    )
                continue

            if task.status == AutoFixTaskStatus.FIXED:
                if not self.validate_fixed_task(task):
                    self._set_task_without_saving(
                        task,
                        AutoFixTaskStatus.DOWNLOAD_QUEUED,
                        "Previously fixed target no longer matches expected MD5",
                    )
                continue

            if task.status in (
                AutoFixTaskStatus.PENDING,
                AutoFixTaskStatus.DOWNLOADING,
                AutoFixTaskStatus.FAILED_RETRYABLE,
            ):
                self._set_task_without_saving(task, AutoFixTaskStatus.DOWNLOAD_QUEUED)
            elif task.status == AutoFixTaskStatus.ORGANIZING:
                self._set_task_without_saving(task, AutoFixTaskStatus.DOWNLOADED)
            elif task.status == AutoFixTaskStatus.VERIFYING:
                self._set_task_without_saving(task, AutoFixTaskStatus.ORGANIZED)

        self.save()

    def validate_fixed_task(self, task: AutoFixTask) -> bool:
        target = self.resolve_task_path(task.target_path or task.original_path)
        if not target.exists() or not target.is_file():
            return False
        return self._calculate_md5(target) == task.expected_hash

    def resolve_task_path(self, path: str) -> Path:
        candidate = Path(path)
        if candidate.is_absolute():
            return candidate
        return self.verification_dir / candidate

    def relative_to_verification_dir(self, path: Path) -> str:
        return _safe_relative_path(path, self.verification_dir)

    def recompute_summary(self) -> Dict[str, int]:
        if self.state is None:
            return {}
        statuses = [task.status for task in self.state.tasks.values()]
        return {
            "total": len(statuses),
            "fixed": statuses.count(AutoFixTaskStatus.FIXED),
            "still_failed": statuses.count(AutoFixTaskStatus.STILL_FAILED),
            "skipped": statuses.count(AutoFixTaskStatus.SKIPPED_NO_ACCESSION),
            "retryable_failed": statuses.count(AutoFixTaskStatus.FAILED_RETRYABLE),
            "fatal_failed": statuses.count(AutoFixTaskStatus.FAILED_FATAL),
        }

    def _set_task_without_saving(
        self,
        task: AutoFixTask,
        status: AutoFixTaskStatus,
        last_error: Optional[str] = None,
    ) -> None:
        task.status = status
        if last_error is not None:
            task.last_error = last_error
        task.updated_at = _utc_now()

    def _calculate_md5(self, file_path: Path) -> str:
        md5_hash = hashlib.md5()
        with open(file_path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                md5_hash.update(chunk)
        return md5_hash.hexdigest()


def task_status_counts(tasks: Iterable[AutoFixTask]) -> Dict[str, int]:
    """Return status counts keyed by status value for reports."""

    counts: Dict[str, int] = {}
    for task in tasks:
        counts[task.status.value] = counts.get(task.status.value, 0) + 1
    return counts
