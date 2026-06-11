"""
Auto-fix coordinator for MD5 verification enhancement.

This module implements the AutoFixCoordinator class, which orchestrates the
complete auto-fix workflow for files that fail MD5 verification.
"""

import logging
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
        max_workers: int = 2
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
        
        logger.info("Starting auto-fix workflow")
        
        # Initialize result tracking variables
        # These will be populated as we progress through the workflow
        failed_files: List[FailedFile] = []
        accessions: List[str] = []
        organize_result = OrganizeResult(0, 0, 0)
        verification_result = VerificationResult(0, 0, 0)
        
        try:
            # Step 1: Create temporary directory for downloads
            # This is a fatal error if it fails - we can't proceed without a temp directory
            self._create_temp_directory()
            
            # Step 2: Collect failed files from verification results
            # Identifies all files with FAIL, MISSING, or ERROR status
            logger.info("Step 1/8: Collecting failed files")
            collector = FailedFileCollector(
                self.verification_result,
                self.verification_dir
            )
            failed_files = collector.collect_failed_files()
            
            # Early exit if no files need fixing
            if not failed_files:
                logger.info("No failed files to fix")
                return self._create_empty_result(start_time)
            
            # Step 3: Extract Accession IDs from file paths
            # Uses regex pattern matching to find GCA_/GCF_ identifiers
            logger.info("Step 2/8: Extracting Accession IDs")
            extractor = AccessionExtractor()
            accessions = extractor.extract_accessions(failed_files)
            
            # Early exit if no valid Accession IDs found
            if not accessions:
                logger.warning("No valid Accession IDs found, all files will be skipped")
                return self._create_skipped_result(failed_files, start_time)
            
            # Step 4: Save Accession list to file for the downloader
            # Creates failed_accessions.txt with one ID per line
            logger.info("Step 3/8: Saving Accession list")
            accession_file = self.temp_dir / "failed_accessions.txt"
            extractor.save_to_file(accessions, accession_file)
            
            # Step 5: Redownload files using Accession_Downloader
            # Requirement 12.1: Display number of files to redownload before starting
            logger.info(f"Step 4/8: Redownloading {len(accessions)} files...")
            logger.info(f"Starting redownload of {len(accessions)} accessions")
            self._redownload_files(accession_file)
            
            # Step 6: Extract .zip files if present
            # The datasets CLI may download files as .zip archives
            logger.info("Step 5/8: Extracting downloaded files")
            self._extract_zip_files()
            
            # Step 7: Organize files to their original locations
            # Matches downloaded files to failed file records and moves them
            logger.info("Step 6/8: Organizing downloaded files")
            organizer = FileOrganizerAdapter(self.verification_dir, failed_files)
            organize_result = organizer.organize_downloaded_files(self.temp_dir)
            
            # Requirement 12.3: Display file organization progress
            logger.info(
                f"File organization complete: {organize_result.organized} organized, "
                f"{organize_result.failed} failed"
            )
            
            # Step 8: Re-verify files to ensure they now pass MD5 checks
            # Calculates MD5 hashes and compares with expected values
            logger.info("Step 7/8: Re-verifying organized files")
            
            # Find all md5sum.txt file(s) - may be in root or subdirectories
            md5sum_files = self._find_md5sum_files()
            
            if not md5sum_files:
                logger.warning(
                    f"No md5sum.txt files found in {self.verification_dir}. "
                    f"Skipping re-verification."
                )
                verification_result = VerificationResult(0, 0, 0)
            else:
                logger.info(f"Found {len(md5sum_files)} md5sum.txt file(s) for verification")
                
                # Use multi-file verification to match each file with its corresponding md5sum.txt
                reverifier = ReVerification(self.verification_dir, md5sum_files)
                verification_result = reverifier.verify_fixed_files(
                    organize_result.organized_files
                )
            
            # Step 9: Generate comprehensive report
            # Creates redownload_report.txt with detailed statistics
            logger.info("Step 8/8: Generating report")
            end_time = datetime.now()
            report_generator = ReportGenerator(self.verification_dir)
            
            # Build AutoFixResult with all collected statistics
            auto_fix_result = self._build_auto_fix_result(
                failed_files,
                accessions,
                organize_result,
                verification_result,
                start_time,
                end_time
            )
            
            # Generate and save the report
            report_path = report_generator.generate_report(
                failed_files=failed_files,
                auto_fix_result=auto_fix_result,
                organize_result=organize_result,
                verification_result=verification_result,
                start_time=start_time,
                end_time=end_time
            )
            
            auto_fix_result.report_path = report_path
            
            # Requirement 12.4: Display final statistics after completion
            logger.info("=" * 60)
            logger.info("Auto-fix workflow completed successfully")
            logger.info(f"Total failed files: {auto_fix_result.total_failed}")
            logger.info(f"Files redownloaded: {auto_fix_result.redownloaded}")
            logger.info(f"Successfully fixed: {auto_fix_result.successfully_fixed}")
            logger.info(f"Still failed: {auto_fix_result.still_failed}")
            logger.info(f"Skipped: {auto_fix_result.skipped}")
            logger.info(f"Processing time: {auto_fix_result.processing_time:.2f} seconds")
            logger.info(f"Report saved to: {report_path}")
            logger.info("=" * 60)
            
            logger.info(
                f"Auto-fix complete: {auto_fix_result.successfully_fixed} fixed, "
                f"{auto_fix_result.still_failed} still failed, "
                f"{auto_fix_result.skipped} skipped"
            )
            
            return auto_fix_result
        
        except Exception as e:
            # Error handling: distinguish between fatal and recoverable errors
            if is_fatal_error(e):
                # Fatal errors (like TempDirectoryError) terminate the workflow
                logger.error(f"Fatal error during auto-fix: {e}")
                raise
            else:
                # Recoverable errors are logged but allow partial results to be returned
                logger.error(f"Error during auto-fix: {e}", exc_info=True)
                end_time = datetime.now()
                return self._build_auto_fix_result(
                    failed_files,
                    accessions,
                    organize_result,
                    verification_result,
                    start_time,
                    end_time
                )
        
        finally:
            # Step 10: Clean up temporary directory
            # This always runs, even if the workflow fails or is interrupted
            self._cleanup_temp_directory()

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
