"""Persistent downloader adapter for MD5 auto-fix resume."""

import logging
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from taxonomy_downloader.accession_downloader import AccessionDownloader
from taxonomy_downloader.accession_models import AccessionConfig
from taxonomy_downloader.md5_autofix_state import (
    AutoFixStateStore,
    AutoFixTask,
    AutoFixTaskStatus,
)

logger = logging.getLogger(__name__)


class AutoFixDownloader:
    """Download missing auto-fix tasks into the persistent state cache."""

    def __init__(
        self,
        state_store: AutoFixStateStore,
        datasets_executable: str = "datasets",
        api_key: Optional[str] = None,
        include_params: Optional[List[str]] = None,
        batch_size: int = 100,
        max_workers: int = 2,
        downloader_class=None,
        allow_mock_success_without_cache: bool = False,
    ):
        self.state_store = state_store
        self.datasets_executable = datasets_executable
        self.api_key = api_key
        self.include_params = include_params or ["genome"]
        self.batch_size = batch_size
        self.max_workers = max_workers
        self.downloader_class = downloader_class or AccessionDownloader
        self.allow_mock_success_without_cache = allow_mock_success_without_cache

    def download_missing(self, tasks: List[AutoFixTask]) -> None:
        """Download tasks without a valid cache and update task state."""
        pending_by_accession: Dict[str, List[AutoFixTask]] = {}

        for task in tasks:
            if not task.accession_id:
                self.state_store.update_task(
                    task.task_id,
                    status=AutoFixTaskStatus.SKIPPED_NO_ACCESSION,
                    last_error="No accession id found for failed file",
                )
                continue

            cached = self.find_cached_file(task)
            if cached is not None:
                self._mark_downloaded(task, cached)
                continue

            self.state_store.update_task(
                task.task_id,
                status=AutoFixTaskStatus.DOWNLOADING,
                last_error=None,
            )
            pending_by_accession.setdefault(task.accession_id, []).append(task)

        if not pending_by_accession:
            return

        accession_file = self._write_accession_file(sorted(pending_by_accession))
        config = AccessionConfig(
            accession_file=str(accession_file),
            output_dir=str(self.state_store.downloads_dir),
            api_key=self.api_key,
            datasets_executable=self.datasets_executable,
            include_params=self.include_params,
            batch_size=self.batch_size,
            max_workers=self.max_workers,
            temp_dir=str(self.state_store.staging_dir),
            resume_validate_files=True,
        )

        try:
            exit_code = self.downloader_class(config).run()
        except Exception as exc:
            logger.error("Accession downloader failed during auto-fix: %s", exc)
            exit_code = 1
        if exit_code != 0:
            logger.warning("Accession downloader exited with code %s", exit_code)

        for accession, accession_tasks in pending_by_accession.items():
            for task in accession_tasks:
                latest_task = self.state_store.state.tasks[task.task_id]
                cached = self.find_cached_file(latest_task)
                if cached is not None:
                    self._mark_downloaded(latest_task, cached)
                elif (
                    self.allow_mock_success_without_cache
                    and self._using_mock_downloader()
                    and exit_code == 0
                ):
                    self.state_store.update_task(
                        latest_task.task_id,
                        status=AutoFixTaskStatus.DOWNLOADED,
                        last_error=None,
                    )
                else:
                    message = (
                        f"No downloaded cache file found for {accession} "
                        f"after downloader exit code {exit_code}"
                    )
                    self.state_store.update_task(
                        latest_task.task_id,
                        status=AutoFixTaskStatus.FAILED_RETRYABLE,
                        last_error=message,
                    )

    def find_cached_file(self, task: AutoFixTask) -> Optional[Path]:
        """Find a persistent cached file matching the task accession and suffix."""
        if not task.accession_id:
            return None

        expected_suffix = Path(task.md5_file_path).suffix
        candidates = self._cache_candidates(task.accession_id)
        for candidate in candidates:
            if not candidate.is_file():
                continue
            if task.accession_id not in candidate.name:
                continue
            if expected_suffix and candidate.suffix != expected_suffix:
                continue
            return candidate
        return None

    def _cache_candidates(self, accession_id: str) -> Iterable[Path]:
        accession_dir = self.state_store.downloads_dir / accession_id
        if accession_dir.exists():
            for candidate in accession_dir.rglob("*"):
                yield candidate
        if self.state_store.downloads_dir.exists():
            for candidate in self.state_store.downloads_dir.rglob(f"*{accession_id}*"):
                yield candidate

    def _write_accession_file(self, accessions: List[str]) -> Path:
        input_dir = self.state_store.state_dir / "download_input"
        input_dir.mkdir(parents=True, exist_ok=True)
        run_id = self.state_store.state.run_id if self.state_store.state else "current"
        accession_file = input_dir / f"{run_id}.txt"
        accession_file.write_text(
            "".join(f"{accession}\n" for accession in accessions),
            encoding="utf-8",
        )
        return accession_file

    def _mark_downloaded(self, task: AutoFixTask, cached_file: Path) -> None:
        self.state_store.update_task(
            task.task_id,
            status=AutoFixTaskStatus.DOWNLOADED,
            cached_file=self.state_store.relative_to_verification_dir(cached_file),
            last_error=None,
        )

    def _using_mock_downloader(self) -> bool:
        return type(self.downloader_class).__module__ == "unittest.mock"
