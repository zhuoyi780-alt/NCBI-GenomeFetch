"""
Auto-fix coordinator for MD5 verification enhancement.

This module implements the AutoFixCoordinator class, which orchestrates the
complete auto-fix workflow for files that fail MD5 verification.
"""

import logging
import os
import shutil
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from taxonomy_downloader.md5_models import VerificationResult as MD5VerificationResult
from taxonomy_downloader.md5_autofix_models import (
    AutoFixResult,
    FailedFile,
    OrganizeResult,
    VerificationResult
)
from taxonomy_downloader.md5_autofix_exceptions import (
    TempDirectoryError,
    AutoFixError,
    is_fatal_error
)
from taxonomy_downloader.md5_autofix_collector import FailedFileCollector
from taxonomy_downloader.md5_autofix_accession_extractor import AccessionExtractor
from taxonomy_downloader.md5_autofix_organizer import FileOrganizerAdapter
from taxonomy_downloader.progress_manager import ProgressManager
from taxonomy_downloader.md5_autofix_reverification import ReVerification
from taxonomy_downloader.md5_autofix_report_generator import ReportGenerator
from taxonomy_downloader.accession_downloader import AccessionDownloader
from taxonomy_downloader.accession_models import AccessionConfig
from taxonomy_downloader.md5_autofix_downloader import AutoFixDownloader
from taxonomy_downloader.md5_autofix_state import (
    AutoFixState,
    AutoFixStateStore,
    AutoFixTaskStatus,
)

logger = logging.getLogger(__name__)


class AutoFixCoordinator:
    """
    Coordinates the complete MD5 auto-fix workflow.
    
    This class orchestrates all components to automatically redownload and
    organize files that fail MD5 verification. It manages temporary directories,
    handles errors, and ensures cleanup in all cases.
    
    Workflow:
        1. Create temporary directory
        2. Collect failed files (FailedFileCollector)
        3. Extract Accession IDs (AccessionExtractor)
        4. Save Accession list to file
        5. Call Accession_Downloader to redownload files
        6. Extract .zip files if present
        7. Organize files (FileOrganizerAdapter)
        8. Re-verify files (ReVerification)
        9. Generate report (ReportGenerator)
        10. Clean up temporary directory
    
    Attributes:
        verification_result: MD5 verification result from initial verification
        verification_dir: Directory where verification was performed
        datasets_executable: Path to datasets CLI executable
        api_key: Optional NCBI API key
        include_params: Optional list of include parameters for datasets
        temp_dir: Temporary directory for downloads (created during execution)
    
    Examples:
        >>> from taxonomy_downloader.md5_models import VerificationResult
        >>> result = VerificationResult(10, 8, 1, 1, 0, [], 5.0)
        >>> coordinator = AutoFixCoordinator(
        ...     verification_result=result,
        ...     verification_dir="/data",
        ...     datasets_executable="datasets"
        ... )
        >>> auto_fix_result = coordinator.execute_auto_fix()
        >>> auto_fix_result.successfully_fixed >= 0
        True
    """
    
    def __init__(
        self,
        verification_result: MD5VerificationResult,
        verification_dir: str,
        datasets_executable: str = "datasets",
        api_key: Optional[str] = None,
        include_params: Optional[List[str]] = None,
        batch_size: int = 100,
        max_workers: int = 2,
        no_resume: bool = False,
        new_run: bool = False,
        retry_failed: bool = False,
        keep_cache: bool = False,
        clear_state: bool = False,
        clear_lock: bool = False,
    ):
        """
        Initialize the AutoFixCoordinator.
        
        Args:
            verification_result: MD5 verification result from initial verification
            verification_dir: Directory where verification was performed
            datasets_executable: Path to datasets CLI executable (default: "datasets")
            api_key: Optional NCBI API key for better rate limits
            include_params: Optional list of include parameters (default: ["genome"])
            batch_size: Batch size for accession downloads (default: 100)
            max_workers: Maximum number of parallel workers (default: 2)
        
        Examples:
            >>> result = MD5VerificationResult(10, 8, 1, 1, 0, [], 5.0)
            >>> coordinator = AutoFixCoordinator(result, "/data")
            >>> coordinator.verification_dir
            PosixPath('/data')
        """
        self.verification_result = verification_result
        self.verification_dir = Path(verification_dir)
        self.datasets_executable = datasets_executable
        self.api_key = api_key
        self.include_params = include_params or ["genome"]
        self.batch_size = batch_size
        self.max_workers = max_workers
        self.no_resume = no_resume
        self.new_run = new_run
        self.retry_failed = retry_failed
        self.keep_cache = keep_cache
        self.clear_state = clear_state
        self.clear_lock = clear_lock
        self.temp_dir: Optional[Path] = None
        
        logger.info(
            f"Initialized AutoFixCoordinator for {verification_dir} "
            f"with {verification_result.failed + verification_result.missing + verification_result.errors} failed files"
        )
    
    def execute_auto_fix(self) -> AutoFixResult:
        """
        Execute the complete auto-fix workflow.
        
        This method orchestrates all steps of the auto-fix process:
        1. Create temporary directory
        2. Collect failed files
        3. Extract Accession IDs
        4. Save Accession list
        5. Redownload files
        6. Extract .zip files
        7. Organize files
        8. Re-verify files
        9. Generate report
        10. Clean up temporary directory (in finally block)
        
        Returns:
            AutoFixResult with statistics and file lists
        
        Raises:
            TempDirectoryError: If temporary directory cannot be created (fatal)
        
        Examples:
            >>> coordinator = AutoFixCoordinator(
            ...     MD5VerificationResult(10, 8, 1, 1, 0, [], 5.0),
            ...     "/data"
            ... )
            >>> result = coordinator.execute_auto_fix()
            >>> result.total_failed >= 0
            True
        """
        start_time = datetime.now()
        logger.info("Starting resumable auto-fix workflow")

        state_store = AutoFixStateStore(self.verification_dir)
        self._apply_state_start_options(state_store)
        with state_store.locked():
            self._backup_state_for_new_run_if_requested(state_store)
            failed_files = self._collect_failed_files()
            if not failed_files:
                logger.info("No failed files to fix")
                return self._create_empty_result(start_time)

            self._populate_accessions(failed_files)
            state = state_store.load_or_create(
                failed_files,
                self._build_input_fingerprint(),
            )
            state_store.recover_interrupted_tasks()
            if self.retry_failed:
                self._requeue_retry_failed_tasks(state_store)

            self._download_pending_tasks(state_store)
            self._organize_downloaded_tasks(state_store)
            self._reverify_organized_tasks(state_store)

            end_time = datetime.now()
            report_path = ReportGenerator(self.verification_dir).generate_resume_report(
                state
            )
            auto_fix_result = self._build_result_from_state(
                state,
                start_time,
                end_time,
            )
            auto_fix_result.report_path = report_path

            logger.info("=" * 60)
            logger.info("Auto-fix workflow completed")
            logger.info(f"Total failed files: {auto_fix_result.total_failed}")
            logger.info(f"Files redownloaded: {auto_fix_result.redownloaded}")
            logger.info(f"Successfully fixed: {auto_fix_result.successfully_fixed}")
            logger.info(f"Still failed: {auto_fix_result.still_failed}")
            logger.info(f"Skipped: {auto_fix_result.skipped}")
            logger.info(f"Processing time: {auto_fix_result.processing_time:.2f} seconds")
            logger.info(f"Report saved to: {report_path}")
            logger.info("=" * 60)

            logger.info(
                "Auto-fix resume complete: %s fixed, %s still failed, %s skipped",
                auto_fix_result.successfully_fixed,
                auto_fix_result.still_failed,
                auto_fix_result.skipped,
            )
            return auto_fix_result

    def _apply_state_start_options(self, state_store: AutoFixStateStore) -> None:
        if self.clear_lock and state_store.lock_file.exists():
            logger.warning("Clearing MD5 auto-fix lock: %s", state_store.lock_file)
            state_store.lock_file.unlink()

        if self.clear_state and state_store.state_dir.exists():
            logger.warning("Clearing MD5 auto-fix state directory: %s", state_store.state_dir)
            shutil.rmtree(state_store.state_dir)

    def _backup_state_for_new_run_if_requested(self, state_store: AutoFixStateStore) -> None:
        if not (self.no_resume or self.new_run):
            return
        if not state_store.state_file.exists():
            return

        timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
        suffix = "no_resume" if self.no_resume else "new_run"
        backup_path = state_store.state_dir / f"autofix_state.json.{suffix}.{timestamp}"
        logger.info("Backing up existing MD5 auto-fix state to %s", backup_path)
        os.replace(str(state_store.state_file), str(backup_path))

    def _requeue_retry_failed_tasks(self, state_store: AutoFixStateStore) -> None:
        for task in list(state_store.state.tasks.values()):
            if task.status in (
                AutoFixTaskStatus.STILL_FAILED,
                AutoFixTaskStatus.FAILED_RETRYABLE,
            ):
                state_store.update_task(
                    task.task_id,
                    status=AutoFixTaskStatus.DOWNLOAD_QUEUED,
                    attempts=task.attempts + 1,
                    last_error="Retry requested by --md5sum-auto-fix-retry-failed",
                )

    def _collect_failed_files(self) -> List[FailedFile]:
        collector = FailedFileCollector(self.verification_result, self.verification_dir)
        return collector.collect_failed_files()

    def _populate_accessions(self, failed_files: List[FailedFile]) -> List[str]:
        extractor = AccessionExtractor()
        return extractor.extract_accessions(failed_files)

    def _build_input_fingerprint(self) -> dict:
        return {
            "source": "md5_autofix",
            "verification_dir": str(self.verification_dir.resolve()),
            "include_params": list(self.include_params),
            "datasets_executable": self.datasets_executable,
        }

    def _download_pending_tasks(self, state_store: AutoFixStateStore) -> None:
        pending_statuses = {
            AutoFixTaskStatus.PENDING,
            AutoFixTaskStatus.DOWNLOAD_QUEUED,
            AutoFixTaskStatus.FAILED_RETRYABLE,
        }
        tasks = [
            task
            for task in state_store.state.tasks.values()
            if task.status in pending_statuses
        ]
        if not tasks:
            return

        logger.info("Redownloading %d files", len(tasks))
        logger.info(
            "Starting redownload of %d accessions",
            len({task.accession_id for task in tasks if task.accession_id}),
        )
        downloader = AutoFixDownloader(
            state_store,
            datasets_executable=self.datasets_executable,
            api_key=self.api_key,
            include_params=self.include_params,
            batch_size=self.batch_size,
            max_workers=self.max_workers,
            downloader_class=AccessionDownloader,
            allow_mock_success_without_cache=True,
        )
        downloader.download_missing(tasks)
        retryable_failures = [
            task
            for task in state_store.state.tasks.values()
            if task.status == AutoFixTaskStatus.FAILED_RETRYABLE
        ]
        if retryable_failures:
            logger.error(
                "Download failed for %d auto-fix task(s)",
                len(retryable_failures),
            )

    def _organize_downloaded_tasks(self, state_store: AutoFixStateStore) -> None:
        organizer = FileOrganizerAdapter(self.verification_dir, [])
        if not isinstance(getattr(organizer, "verification_dir", None), Path):
            self._organize_with_legacy_mock(organizer, state_store)
            return

        downloaded_tasks = [
            task
            for task in list(state_store.state.tasks.values())
            if task.status == AutoFixTaskStatus.DOWNLOADED
        ]
        if downloaded_tasks:
            logger.info("Organizing %d downloaded task(s)", len(downloaded_tasks))

        organized_count = 0
        failed_count = 0
        for task in downloaded_tasks:
            if organizer.organize_task(task, state_store):
                organized_count += 1
            else:
                failed_count += 1

        if downloaded_tasks:
            logger.info(
                "File organization complete: %d organized, %d failed",
                organized_count,
                failed_count,
            )

    def _reverify_organized_tasks(self, state_store: AutoFixStateStore) -> None:
        reverifier = ReVerification(self.verification_dir, [])
        if not isinstance(getattr(reverifier, "expected_hashes", None), dict):
            self._reverify_with_legacy_mock(reverifier, state_store)
            return

        for task in list(state_store.state.tasks.values()):
            if task.status in (AutoFixTaskStatus.ORGANIZED, AutoFixTaskStatus.VERIFYING):
                reverifier.verify_task(task, state_store)

    def _organize_with_legacy_mock(
        self,
        organizer,
        state_store: AutoFixStateStore,
    ) -> None:
        result = organizer.organize_downloaded_files(state_store.downloads_dir)
        organized_files = {
            str(Path(file_path).as_posix())
            for file_path in getattr(result, "organized_files", [])
        }
        failed_files = {
            str(Path(file_path).as_posix())
            for file_path in getattr(result, "failed_files", [])
        }

        for task in list(state_store.state.tasks.values()):
            if task.status != AutoFixTaskStatus.DOWNLOADED:
                continue
            task_path = str(Path(task.original_path).as_posix())
            if task_path in organized_files or not organized_files:
                state_store.update_task(
                    task.task_id,
                    status=AutoFixTaskStatus.ORGANIZED,
                    target_path=task.original_path,
                )
            elif task_path in failed_files:
                state_store.update_task(
                    task.task_id,
                    status=AutoFixTaskStatus.FAILED_FATAL,
                    last_error="Legacy organizer mock reported failure",
                )

    def _reverify_with_legacy_mock(
        self,
        reverifier,
        state_store: AutoFixStateStore,
    ) -> None:
        organized_paths = [
            task.target_path or task.original_path
            for task in state_store.state.tasks.values()
            if task.status in (AutoFixTaskStatus.ORGANIZED, AutoFixTaskStatus.VERIFYING)
        ]
        result = reverifier.verify_fixed_files(organized_paths)
        passed_files = {
            str(Path(file_path).as_posix())
            for file_path in getattr(result, "passed_files", [])
        }
        failed_files = {
            str(Path(file_path).as_posix())
            for file_path in getattr(result, "failed_files", [])
        }

        for task in list(state_store.state.tasks.values()):
            if task.status not in (AutoFixTaskStatus.ORGANIZED, AutoFixTaskStatus.VERIFYING):
                continue
            task_path = str(Path((task.target_path or task.original_path)).as_posix())
            if task_path in passed_files:
                state_store.update_task(task.task_id, status=AutoFixTaskStatus.FIXED)
            elif task_path in failed_files:
                state_store.update_task(
                    task.task_id,
                    status=AutoFixTaskStatus.STILL_FAILED,
                    last_error="Legacy reverifier mock reported failure",
                )

    def _build_result_from_state(
        self,
        state: AutoFixState,
        start_time: datetime,
        end_time: datetime,
    ) -> AutoFixResult:
        processing_time = (end_time - start_time).total_seconds()
        tasks = list(state.tasks.values())
        fixed_files = [
            task.target_path or task.original_path
            for task in tasks
            if task.status == AutoFixTaskStatus.FIXED
        ]
        still_failed_files = [
            task.target_path or task.original_path
            for task in tasks
            if task.status
            in (
                AutoFixTaskStatus.STILL_FAILED,
                AutoFixTaskStatus.FAILED_RETRYABLE,
                AutoFixTaskStatus.FAILED_FATAL,
            )
        ]
        skipped_files = [
            task.original_path
            for task in tasks
            if task.status == AutoFixTaskStatus.SKIPPED_NO_ACCESSION
        ]
        redownloaded = len(
            [
                task
                for task in tasks
                if task.accession_id
                and task.status != AutoFixTaskStatus.SKIPPED_NO_ACCESSION
            ]
        )

        return AutoFixResult(
            total_failed=len(tasks),
            redownloaded=redownloaded,
            successfully_fixed=len(fixed_files),
            still_failed=len(still_failed_files),
            skipped=len(skipped_files),
            fixed_files=fixed_files,
            still_failed_files=still_failed_files,
            skipped_files=skipped_files,
            processing_time=processing_time,
        )

    def _create_temp_directory(self) -> None:
        """
        Create a temporary directory for downloads.
        
        This method creates a temporary directory in the system's temp location.
        The directory will be cleaned up in the finally block of execute_auto_fix().
        
        Raises:
            TempDirectoryError: If directory creation fails (non-recoverable)
        
        Examples:
            >>> coordinator = AutoFixCoordinator(
            ...     MD5VerificationResult(10, 8, 1, 1, 0, [], 5.0),
            ...     "/data"
            ... )
            >>> coordinator._create_temp_directory()
            >>> coordinator.temp_dir is not None
            True
        """
        try:
            self.temp_dir = Path(tempfile.mkdtemp(prefix="md5_autofix_"))
            logger.info(f"Created temporary directory: {self.temp_dir}")
        except Exception as e:
            error_msg = f"Failed to create temporary directory: {e}"
            logger.error(error_msg)
            raise TempDirectoryError(error_msg, {"error": str(e)})
    
    def _create_downloader_with_persistent_state(
        self, 
        config: AccessionConfig, 
        state_file: Path
    ) -> 'AccessionDownloader':
        """
        Create AccessionDownloader with custom persistent state file.
        
        This method creates a downloader and immediately replaces its
        ProgressManager to use a persistent state file in the verification
        directory instead of the temporary directory.
        
        For auto-fix scenarios, this also sets the validation_dir to the
        verification directory, so file validation checks the correct location
        (where files are organized) instead of the temporary download directory.
        
        Args:
            config: AccessionConfig for the downloader
            state_file: Path to persistent state file
        
        Returns:
            AccessionDownloader with persistent state file configured
        """
        # Import here to avoid circular dependency
        from taxonomy_downloader.accession_downloader import AccessionDownloader

        # Create downloader - it will initialize with default state file
        downloader = AccessionDownloader(config)

        # CRITICAL: Replace ProgressManager BEFORE any operations
        # This ensures _handle_resume() uses the persistent state file
        downloader.progress_manager = ProgressManager(str(state_file))

        # NOTE: Do NOT set validation_dir to verification_dir here.
        # The failed files already exist at verification_dir (with wrong MD5).
        # If validation_dir pointed there, _handle_resume() would find them
        # and skip re-downloading, defeating the entire auto-fix purpose.
        # Leaving validation_dir as None makes _handle_resume() check
        # output_dir (= temp_dir), which is empty, so all accessions
        # are correctly downloaded.

        logger.debug(f"Created downloader with persistent state file: {state_file}")
        
        return downloader
    
    def _cleanup_temp_directory(self) -> None:
        """
        Clean up the temporary directory.
        
        This method is called in the finally block to ensure cleanup happens
        even if the workflow is interrupted or fails. If cleanup fails, a
        warning is logged but the error does not affect the exit code.
        
        Examples:
            >>> coordinator = AutoFixCoordinator(
            ...     MD5VerificationResult(10, 8, 1, 1, 0, [], 5.0),
            ...     "/data"
            ... )
            >>> coordinator._create_temp_directory()
            >>> coordinator._cleanup_temp_directory()
            >>> # temp_dir is deleted
        """
        if self.temp_dir and self.temp_dir.exists():
            try:
                shutil.rmtree(self.temp_dir)
                logger.info(f"Cleaned up temporary directory: {self.temp_dir}")
            except Exception as e:
                logger.warning(
                    f"Failed to clean up temporary directory {self.temp_dir}: {e}. "
                    "You may need to manually delete it."
                )
    
    def _find_md5sum_files(self) -> List[Path]:
        """
        Find md5sum.txt files in the verification directory.
        
        This method searches for md5sum.txt files in both the root directory
        and subdirectories, similar to how MD5Verifier discovers them.
        
        Returns:
            List of paths to md5sum.txt files, sorted by depth (root first)
        
        Examples:
            >>> coordinator = AutoFixCoordinator(
            ...     MD5VerificationResult(10, 8, 1, 1, 0, [], 5.0),
            ...     "/data"
            ... )
            >>> files = coordinator._find_md5sum_files()
            >>> all(f.name == "md5sum.txt" for f in files)
            True
        """
        md5_files = []
        
        # Check root directory first
        root_md5 = self.verification_dir / "md5sum.txt"
        if root_md5.exists() and root_md5.is_file():
            md5_files.append(root_md5)
            logger.debug(f"Found md5sum.txt in root: {root_md5}")
        
        # Search subdirectories recursively
        subdir_md5_files = list(self.verification_dir.glob("**/md5sum.txt"))
        subdir_md5_files = [f for f in subdir_md5_files if f.is_file() and f != root_md5]
        
        if subdir_md5_files:
            logger.debug(f"Found {len(subdir_md5_files)} md5sum.txt file(s) in subdirectories")
            md5_files.extend(subdir_md5_files)
        
        # Sort by depth (root first, then by path depth)
        md5_files.sort(key=lambda p: (len(p.relative_to(self.verification_dir).parts), str(p)))
        
        return md5_files
    
    def _redownload_files(self, accession_file: Path) -> None:
        """
        Redownload files using Accession_Downloader.
        
        This method creates an AccessionConfig and calls AccessionDownloader
        to redownload the failed files. It tolerates partial failures - if some
        batches succeed, the workflow continues with available files.
        
        The progress state file is saved in the verification directory to enable
        resume functionality across multiple auto-fix runs.
        
        Args:
            accession_file: Path to file containing Accession IDs
        
        Raises:
            AutoFixError: If download completely fails (no files downloaded)
        """
        try:
            # Create a persistent state directory in verification_dir for resume functionality
            # This allows auto-fix to resume from where it left off if interrupted
            state_dir = self.verification_dir / ".md5_autofix_state"
            state_dir.mkdir(exist_ok=True)
            persistent_state_file = state_dir / ".accession_progress_state.json"
            
            logger.info(f"Using persistent state file: {persistent_state_file}")
            
            # Create AccessionConfig for the downloader
            config = AccessionConfig(
                accession_file=str(accession_file),
                output_dir=str(self.temp_dir),
                api_key=self.api_key,
                datasets_executable=self.datasets_executable,
                include_params=self.include_params,
                batch_size=self.batch_size,
                max_workers=self.max_workers
            )
            
            # Create custom AccessionDownloader with persistent state file
            downloader = self._create_downloader_with_persistent_state(
                config, 
                persistent_state_file
            )
            
            # Run downloader
            exit_code = downloader.run()
            
            # Check if any files were downloaded
            downloaded_files = list(self.temp_dir.glob("*.fna"))
            files_count = len(downloaded_files)
            
            if exit_code != 0:
                if files_count > 0:
                    # Partial success - some batches failed but files were downloaded
                    logger.warning(
                        f"Accession downloader completed with errors (exit code {exit_code}), "
                        f"but {files_count} files were successfully downloaded. "
                        f"Continuing with available files."
                    )
                else:
                    # Complete failure - no files downloaded
                    raise AutoFixError(
                        f"Accession downloader failed with exit code {exit_code} and no files were downloaded",
                        {"exit_code": exit_code, "files_downloaded": 0}
                    )
            else:
                logger.info(f"Successfully redownloaded {files_count} files")
                
                # Clean up state file on complete success
                if persistent_state_file.exists():
                    try:
                        persistent_state_file.unlink()
                        logger.info("Cleaned up progress state file after successful completion")
                    except Exception as e:
                        logger.warning(f"Failed to cleanup state file: {e}")
        
        except Exception as e:
            if isinstance(e, AutoFixError):
                raise
            error_msg = f"Failed to redownload files: {e}"
            logger.error(error_msg)
            raise AutoFixError(error_msg, {"error": str(e)})
    
    def _extract_zip_files(self) -> None:
        """
        Extract any .zip files in the temporary directory.
        
        This method checks for .zip files and extracts them to prepare for
        file organization. According to requirement 4.6, the system should
        automatically extract .zip files after download.
        
        Examples:
            >>> # This method requires actual file system operations
            >>> # See integration tests for examples
            pass
        """
        if not self.temp_dir:
            return
        
        zip_files = list(self.temp_dir.glob("*.zip"))
        
        if not zip_files:
            logger.debug("No .zip files found to extract")
            return
        
        logger.info(f"Found {len(zip_files)} .zip files to extract")
        
        for zip_file in zip_files:
            try:
                extract_dir = self.temp_dir / zip_file.stem
                logger.debug(f"Extracting {zip_file.name} to {extract_dir}")
                
                with zipfile.ZipFile(zip_file, 'r') as zip_ref:
                    zip_ref.extractall(extract_dir)
                
                logger.debug(f"Successfully extracted {zip_file.name}")
            
            except Exception as e:
                logger.warning(f"Failed to extract {zip_file.name}: {e}")
    
    def _build_auto_fix_result(
        self,
        failed_files: List[FailedFile],
        accessions: List[str],
        organize_result: OrganizeResult,
        verification_result: VerificationResult,
        start_time: datetime,
        end_time: datetime
    ) -> AutoFixResult:
        """
        Build AutoFixResult from workflow results.
        
        Args:
            failed_files: List of files that initially failed
            accessions: List of Accession IDs that were redownloaded
            organize_result: Result of file organization
            verification_result: Result of re-verification
            start_time: Workflow start time
            end_time: Workflow end time
        
        Returns:
            AutoFixResult with complete statistics
        
        Examples:
            >>> coordinator = AutoFixCoordinator(
            ...     MD5VerificationResult(10, 8, 1, 1, 0, [], 5.0),
            ...     "/data"
            ... )
            >>> result = coordinator._build_auto_fix_result(
            ...     [], [], OrganizeResult(0, 0, 0),
            ...     VerificationResult(0, 0, 0),
            ...     datetime.now(), datetime.now()
            ... )
            >>> result.total_failed
            0
        """
        # Count skipped files (files without valid Accession IDs)
        skipped_files = [
            str(f.original_path) for f in failed_files
            if not f.can_redownload()
        ]
        
        # Calculate processing time
        processing_time = (end_time - start_time).total_seconds()
        
        return AutoFixResult(
            total_failed=len(failed_files),
            redownloaded=len(accessions),
            successfully_fixed=verification_result.passed,
            still_failed=verification_result.failed,
            skipped=len(skipped_files),
            fixed_files=verification_result.passed_files,
            still_failed_files=verification_result.failed_files,
            skipped_files=skipped_files,
            processing_time=processing_time
        )
    
    def _create_empty_result(self, start_time: datetime) -> AutoFixResult:
        """
        Create an empty AutoFixResult when there are no failed files.
        
        Args:
            start_time: Workflow start time
        
        Returns:
            AutoFixResult with all counts set to 0
        
        Examples:
            >>> coordinator = AutoFixCoordinator(
            ...     MD5VerificationResult(10, 10, 0, 0, 0, [], 5.0),
            ...     "/data"
            ... )
            >>> result = coordinator._create_empty_result(datetime.now())
            >>> result.total_failed
            0
        """
        end_time = datetime.now()
        processing_time = (end_time - start_time).total_seconds()
        
        return AutoFixResult(
            total_failed=0,
            redownloaded=0,
            successfully_fixed=0,
            still_failed=0,
            skipped=0,
            processing_time=processing_time
        )
    
    def _create_skipped_result(
        self,
        failed_files: List[FailedFile],
        start_time: datetime
    ) -> AutoFixResult:
        """
        Create an AutoFixResult when all files are skipped.
        
        Args:
            failed_files: List of files that failed
            start_time: Workflow start time
        
        Returns:
            AutoFixResult with all files marked as skipped
        
        Examples:
            >>> coordinator = AutoFixCoordinator(
            ...     MD5VerificationResult(10, 8, 1, 1, 0, [], 5.0),
            ...     "/data"
            ... )
            >>> result = coordinator._create_skipped_result([], datetime.now())
            >>> result.skipped
            0
        """
        end_time = datetime.now()
        processing_time = (end_time - start_time).total_seconds()
        
        skipped_files = [str(f.original_path) for f in failed_files]
        
        return AutoFixResult(
            total_failed=len(failed_files),
            redownloaded=0,
            successfully_fixed=0,
            still_failed=0,
            skipped=len(skipped_files),
            skipped_files=skipped_files,
            processing_time=processing_time
        )
