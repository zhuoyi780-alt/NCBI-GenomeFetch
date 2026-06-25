"""
Main controller for accession-based genome downloads.

This module orchestrates the accession download workflow, including
batch creation, parallel processing, and result aggregation.
"""

import json
import os
import shutil
import signal
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import List, Generator

from . import __version__
from .accession_models import (
    AccessionConfig,
    BatchResult,
    get_filename_for_accession,
    get_filenames_for_accession,
)
from .file_type_utils import get_expected_file_extensions
from .accession_manifest import AccessionManifest, ManifestConflictError
from .accession_parser import load_accessions
from .accession_batch_processor import AccessionBatchProcessor
from .logging_config import get_logger, get_console
from .progress_manager import ProgressManager


class AccessionDownloader:
    """Main controller for accession-based genome downloads."""
    
    def __init__(self, config: AccessionConfig):
        """
        Initialize the accession downloader.
        
        Args:
            config: AccessionConfig with download parameters
        """
        self.config = config
        self.logger = get_logger("accession_downloader")
        self.console = get_console()
        self.interrupted = False
        self._interrupt_lock = threading.Lock()
        self.temp_root = None
        self.manifest = AccessionManifest(config.output_dir)
        self.manifest.load()
        self.manifest.import_existing_md5()
        
        # Initialize ProgressManager for resume functionality
        state_file = Path(config.output_dir) / ".accession_progress_state.json"
        self.progress_manager = ProgressManager(str(state_file))
        
        # Validation directory for file existence checks
        # Default is None (use output_dir), but can be set for auto-fix scenarios
        # where files are organized to a different directory
        self.validation_dir = None
        
        # Set up signal handlers for graceful interruption
        signal.signal(signal.SIGINT, self._signal_handler)
        try:
            signal.signal(signal.SIGTERM, self._signal_handler)
        except (OSError, ValueError) as e:
            # SIGTERM may not be available on all platforms (e.g., Windows)
            self.logger.debug(f"Could not register SIGTERM handler: {e}")
    
    def run(self) -> int:
        """
        Execute the accession download workflow.
        
        Steps:
        1. Validate configuration
        2. Load accessions from file
        3. Display API key warning if not configured
        4. Create batches
        5. Process batches in parallel
        6. Aggregate MD5 checksums
        7. Display summary
        
        Returns:
            Exit code (0 for success, non-zero for failure)
        """
        start_time = time.time()
        
        try:
            # Validate configuration
            errors = self.config.validate()
            if errors:
                for error in errors:
                    self.console.print_error(error)
                return 1
            
            # Display header
            self.console.print_header("NCBI Genome Downloader - Accession Mode")
            self.console.print_info(f"Output directory: {self.config.output_dir}")
            self.console.print_info(f"Batch size: {self.config.batch_size}")
            self.console.print_info(f"Max workers: {self.config.max_workers}")
            self.console.print_separator()
            
            # Display API key warning if not configured
            if not self.config.has_api_key():
                self.console.print_warning(
                    "No API key provided. NCBI rate limits are stricter without an API key."
                )
                self.console.print_warning(
                    "Consider using -k/--api-key for better performance."
                )
                self.console.print_separator()
            
            # Load accessions
            self.logger.info(f"Loading accessions from {self.config.accession_file}")
            accessions, duplicate_count = load_accessions(self.config.accession_file)
            self.console.print_info(f"Loaded {len(accessions)} unique accession(s)")
            
            # Display deduplication info if duplicates were found
            if duplicate_count > 0:
                self.console.print_info(f"Removed {duplicate_count} duplicate accession(s) from input")
            
            # Handle resume: filter out completed accessions
            remaining_accessions = self._handle_resume(accessions)
            
            # Check if all accessions are already completed
            if not remaining_accessions:
                self.console.print_success("All accessions already completed!")
                self.progress_manager.cleanup()
                return 0
            
            # Create temporary directory for batch processing
            temp_base_dir = Path(self.config.temp_dir) if self.config.temp_dir else Path(self.config.output_dir)
            self.temp_root = temp_base_dir / ".temp_batches"
            self.temp_root.mkdir(parents=True, exist_ok=True)
            self.logger.info(f"Created temporary directory: {self.temp_root}")
            
            # Create batches (only for remaining accessions)
            batches = list(self._create_batches(remaining_accessions))
            total_batches = len(batches)
            self.console.print_info(f"Created {total_batches} batch(es)")
            self.console.print_separator()
            
            # Process batches in parallel
            batch_results = self._process_batches_parallel(batches)
            
            # Check for interruption
            if self.interrupted:
                self.console.print_warning("Download interrupted by user")
                return 130  # Standard exit code for SIGINT
            
            # Aggregate results
            successful_batches = [r for r in batch_results if r.success]
            failed_batches = [r for r in batch_results if not r.success]
            
            total_files = sum(r.files_saved for r in successful_batches)
            
            # Calculate total completed accessions across all batches
            total_completed = sum(len(r.completed_accessions) for r in batch_results)
            total_expected = len(remaining_accessions)
            
            # Cleanup progress state file ONLY if ALL accessions completed successfully
            # This prevents premature cleanup when some accessions fail within successful batches
            if not failed_batches and total_completed == total_expected:
                self.progress_manager.cleanup()
                self.logger.info("All accessions completed successfully, cleaned up progress state file")
            elif total_completed < total_expected:
                missing_count = total_expected - total_completed
                self.logger.warning(
                    f"{missing_count} accession(s) did not complete successfully. "
                    f"Progress state file preserved for resume."
                )
                self.console.print_warning(
                    f"{missing_count} accession(s) failed to download. "
                    f"Run the same command again to retry failed accessions."
                )
            
            # Display summary
            elapsed_time = time.time() - start_time
            self.console.print_separator()
            self.console.print_info("Process Finished.")
            self.console.print_info(f"Total files saved: {total_files}")
            self.console.print_info(f"Successful batches: {len(successful_batches)}/{total_batches}")
            if failed_batches:
                self.console.print_warning(f"Failed batches: {len(failed_batches)}")
            coverage = self.manifest.coverage()
            self.console.print_info(
                "Manifest coverage: "
                f"{coverage['tracked_artifacts']} trusted artifacts / "
                f"{coverage['discovered_artifacts']} discovered output artifacts"
            )
            self.console.print_info(f"Total time: {elapsed_time / 60:.2f} minutes")
            self.console.print_separator()
            
            return 0 if not failed_batches else 1
        
        except Exception as e:
            self.logger.error(f"Fatal error in accession downloader: {e}", exc_info=True)
            self.console.print_error(f"Fatal error: {e}")
            return 1
        
        finally:
            # Guaranteed cleanup of temporary directory
            if self.temp_root and self.temp_root.exists():
                try:
                    shutil.rmtree(self.temp_root)
                    self.logger.info("Cleaned up temporary directory")
                except Exception as e:
                    self.logger.error(f"Failed to cleanup temporary directory: {e}")
    
    def _create_batches(self, accessions: List[str]) -> Generator[List[str], None, None]:
        """
        Create batches of accessions.
        
        Args:
            accessions: List of all accessions
            
        Yields:
            Lists of accessions (batches)
        """
        batch_size = self.config.batch_size
        
        for i in range(0, len(accessions), batch_size):
            yield accessions[i:i + batch_size]
    
    def _process_batches_parallel(self, batches: List[List[str]]) -> List[BatchResult]:
        """
        Process batches in parallel using ThreadPoolExecutor.
        
        Args:
            batches: List of accession batches
            
        Returns:
            List of BatchResult objects
        """
        results = []
        
        with ThreadPoolExecutor(max_workers=self.config.max_workers) as executor:
            # Submit all batch jobs
            future_to_batch = {}
            for batch_num, batch in enumerate(batches, start=1):
                processor = AccessionBatchProcessor(
                    datasets_exe=self.config.datasets_executable,
                    output_dir=self.config.output_dir,
                    temp_root=str(self.temp_root),
                    api_key=self.config.api_key,
                    max_retries=self.config.max_retries,
                    base_retry_delay=self.config.base_retry_delay,
                    include_params=self.config.include_params,
                    download_timeout=self.config.download_timeout,
                    rehydrate_timeout=self.config.rehydrate_timeout,
                    keep_failed_temp=self.config.keep_failed_temp,
                )
                future = executor.submit(processor.process_batch, batch_num, batch)
                future_to_batch[future] = batch_num
            
            # Process completed batches
            for future in as_completed(future_to_batch):
                if self.interrupted:
                    # Cancel remaining futures
                    cancelled_count = 0
                    for f in future_to_batch:
                        if f.cancel():
                            cancelled_count += 1
                    if cancelled_count > 0:
                        self.logger.info(f"Cancelled {cancelled_count} pending batch(es)")
                    break
                
                batch_num = future_to_batch[future]
                try:
                    result = future.result()
                    results.append(result)
                    
                    # Save trusted MD5 manifest before marking progress complete.
                    artifacts = getattr(result, "artifacts", [])
                    if not isinstance(artifacts, list):
                        artifacts = []
                    if result.success and artifacts:
                        try:
                            self.manifest.commit_artifacts(artifacts)
                        except ManifestConflictError as e:
                            self.logger.error(
                                f"Batch {batch_num}: manifest conflict: {e}", exc_info=True
                            )
                            self.console.print_error(f"[Batch {batch_num} !] {e}")
                            result.success = False
                            result.error_type = "checksum_conflict"
                            result.error_message = str(e)

                    # Save progress incrementally after each successful batch.
                    if result.success and result.completed_accessions:
                        for accession in result.completed_accessions:
                            self.progress_manager.save_completed_taxon(accession)
                            self.logger.debug(f"Marked accession '{accession}' as completed")
                        
                        # Log at INFO level ONCE per batch
                        self.logger.info(
                            f"Batch {batch_num}: Marked {len(result.completed_accessions)} "
                            f"accessions as completed"
                        )
                    
                    # Display progress
                    if result.success:
                        self.console.print_success(
                            f"[OK] Batch {batch_num} completed. Found {result.files_saved} FNA files."
                        )
                    else:
                        self.console.print_error(
                            f"[Batch {batch_num} !] {result.error_message}"
                        )
                
                except Exception as e:
                    self.logger.error(f"Batch {batch_num} raised exception: {e}", exc_info=True)
                    self.console.print_error(f"[Batch {batch_num} !] Unexpected error: {e}")
                    results.append(BatchResult(
                        batch_num=batch_num,
                        success=False,
                        files_saved=0,
                        md5_entries=[],
                        error_message=str(e)
                    ))
        
        return results
    
    def _write_consolidated_md5(self, successful_batches: List[BatchResult]) -> None:
        """
        Write consolidated md5sum.txt file from the persistent manifest.
        
        Args:
            successful_batches: List of successful BatchResult objects
        """
        md5_output_path = Path(self.config.output_dir) / "md5sum.txt"
        
        try:
            self.manifest.write_md5sum_atomic()
            self.logger.info(
                f"Wrote {len(self.manifest.artifacts)} MD5 entries to {md5_output_path}"
            )
            self.console.print_info(f"MD5 checksums written to: {md5_output_path}")
        
        except Exception as e:
            self.logger.error(f"Failed to write consolidated MD5 file: {e}", exc_info=True)
            self.console.print_error(f"Failed to write MD5 file: {e}")
    
    def _handle_resume(self, all_accessions: List[str]) -> List[str]:
        """
        Handle resume functionality and return remaining accessions.
        
        This method orchestrates the resume workflow by:
        1. Loading completed accessions from the progress state file
        2. Validating that files actually exist for completed accessions
        3. Filtering out completed accessions from the input list
        4. Displaying appropriate progress messages
        
        If no state file exists (e.g., after successful completion), this method
        will still check if files exist in the output/validation directory to avoid
        re-downloading files that are already present.
        
        Error handling ensures that corrupted or unreadable state files
        never crash the download process. On any error, the system logs
        a warning and continues with a fresh state.
        
        Args:
            all_accessions: Complete list of accessions to process
            
        Returns:
            List of accessions that still need to be processed
            
        Note:
            - Displays "Found existing progress" if completed accessions exist
            - Displays warning if previously completed files are missing
            - Displays "Resuming" message with completion counts
            - Handles corrupted JSON, read errors, and permission errors gracefully
            - Validates: Requirements 2.1, 2.2, 2.3, 2.5, 3.3, 5.1, 5.2, 5.3, 8.1, 8.2, 8.5
        """
        try:
            # 1. Load completed accessions from progress manager
            completed_accessions = self.progress_manager.load_state()
            
            if completed_accessions:
                # 2. Display "Found existing progress" message
                self.console.print_info(
                    f"Found existing progress: {len(completed_accessions)} accessions already completed"
                )
                
                if self.config.resume_validate_files:
                    try:
                        # 3. Call _validate_accession_files to verify files exist
                        valid_completed = self._validate_accession_files(completed_accessions)
                        
                        # 4. Display warning if files are missing
                        if len(valid_completed) < len(completed_accessions):
                            removed_count = len(completed_accessions) - len(valid_completed)
                            self.console.print_warning(
                                f"{removed_count} previously completed accessions have missing files "
                                f"and will be re-processed"
                            )
                    except Exception as e:
                        # File validation errors should not crash the process
                        self.logger.warning(
                            f"Error during file validation: {e}. Continuing with fresh state."
                        )
                        # Continue with fresh state (no completed accessions)
                        return all_accessions
                else:
                    self.console.print_warning(
                        "Skipping output file validation for resume; trusting progress JSON state"
                    )
            else:
                # No state file exists - check if files already exist in output/validation directory
                # This handles the case where auto-fix completed successfully and cleaned up the state file
                if not self.config.resume_validate_files:
                    self.logger.debug(
                        "No progress state found and output file validation is disabled; "
                        "processing all accessions"
                    )
                    return all_accessions
                
                self.logger.debug("No progress state found, checking for existing files...")
                
                try:
                    # Check if any of the requested files already exist
                    # Use silent validation to avoid noisy warnings for first-time downloads
                    existing_accessions = self._validate_accession_files_silent(set(all_accessions))
                    
                    if existing_accessions:
                        self.console.print_info(
                            f"Found {len(existing_accessions)} existing file(s) that will be skipped"
                        )
                        self.logger.info(
                            f"Skipping {len(existing_accessions)} accessions with existing files"
                        )
                        
                        # Update progress manager state with existing files
                        # This ensures get_remaining_taxa() will filter them out
                        if self.progress_manager.state is None:
                            self.progress_manager.load_state()
                        self.progress_manager.state.completed_taxa = existing_accessions
                except Exception as e:
                    # If validation fails, continue with all accessions
                    self.logger.warning(
                        f"Error checking for existing files: {e}. Will process all accessions."
                    )
            
            # 5. Get remaining accessions using progress manager
            remaining = self.progress_manager.get_remaining_taxa(all_accessions)
            
            # 6. Display "Resuming" message with counts
            if len(remaining) < len(all_accessions):
                completed_count = len(all_accessions) - len(remaining)
                self.console.print_info(
                    f"Resuming: {completed_count} accessions already completed, "
                    f"{len(remaining)} remaining"
                )
            
            # 7. Return list of remaining accessions
            return remaining
        
        except (json.JSONDecodeError, ValueError) as e:
            # Corrupted JSON or invalid state file format
            self.logger.warning(
                f"Progress state file is corrupted or invalid: {e}. Starting with fresh state."
            )
            return all_accessions
        
        except (OSError, IOError, PermissionError) as e:
            # File read errors, permission errors, or other I/O issues
            self.logger.warning(
                f"Failed to read progress state file: {e}. Starting with fresh state."
            )
            return all_accessions
        
        except Exception as e:
            # Catch-all for any other unexpected errors
            self.logger.warning(
                f"Unexpected error during resume: {e}. Starting with fresh state."
            )
            return all_accessions
    
    def _validate_accession_files(self, completed_accessions: set) -> set:
        """
        Validate that .fna files exist for completed accessions.
        
        This method checks the output directory to verify that files actually
        exist for accessions marked as completed. Missing files are removed
        from the completed set and will be re-downloaded.
        
        For auto-fix scenarios where validation_dir is set, files may be in
        subdirectories. This method will recursively search for files.
        
        Args:
            completed_accessions: Set of accession identifiers marked as completed
            
        Returns:
            Set of accessions with existing, valid files
            
        Note:
            - Uses get_filename_for_accession() for consistency with file saving
            - Displays progress message for large lists (> 1000 files)
            - Logs progress every 10,000 files for very large lists
            - Updates progress manager state to remove invalid accessions
            - Supports recursive search in subdirectories for auto-fix scenarios
            - Logs warnings for missing files (use _validate_accession_files_silent for silent mode)
        """
        return self._validate_accession_files_internal(completed_accessions, silent=False)
    
    def _validate_accession_files_silent(self, accessions: set) -> set:
        """
        Silently validate that .fna files exist for accessions (no warnings for missing files).
        
        This is used when checking for existing files in first-time downloads to avoid
        noisy warning messages for files that are expected to not exist yet.
        
        Args:
            accessions: Set of accession identifiers to check
            
        Returns:
            Set of accessions with existing files
        """
        return self._validate_accession_files_internal(accessions, silent=True)
    
    def _validate_accession_files_internal(self, accessions: set, silent: bool = False) -> set:
        """
        Internal method to validate that .fna files exist for accessions.
        
        This method checks the output directory to verify that files actually
        exist for accessions. Missing files are removed from the set.
        
        For auto-fix scenarios where validation_dir is set, files may be in
        subdirectories. This method will recursively search for files.
        
        Args:
            accessions: Set of accession identifiers to check
            silent: If True, don't log warnings for missing files
            
        Returns:
            Set of accessions with existing, valid files
            
        Note:
            - Uses get_filename_for_accession() for consistency with file saving
            - Displays progress message for large lists (> 1000 files)
            - Logs progress every 10,000 files for very large lists
            - Updates progress manager state to remove invalid accessions (if not silent)
            - Supports recursive search in subdirectories for auto-fix scenarios
        """
        # Use validation_dir if set (auto-fix scenario), otherwise use output_dir
        search_dir = self.validation_dir if self.validation_dir else Path(self.config.output_dir)
        
        valid_accessions = set()
        
        # Performance: Display progress message for large lists
        total = len(accessions)
        if total > 1000 and not silent:
            self.console.print_info(f"Verifying {total} existing files...")
        
        # Build file index for fast lookup
        # This is especially important for auto-fix scenarios with subdirectories
        file_index = self._build_file_index(search_dir)
        
        # Performance: Use set for O(1) lookup complexity
        for idx, accession in enumerate(accessions, 1):
            # Use include-aware centralized filename generation for consistency
            filenames = get_filenames_for_accession(accession, self.config.include_params)
            
            # Check if file exists in index
            if all(
                filename in file_index and self.manifest.has_trusted_artifact(filename)
                for filename in filenames
            ):
                valid_accessions.add(accession)
                self.logger.debug(f"Validated existing file for accession '{accession}'")
            else:
                # Only log warning if not in silent mode
                if not silent:
                    self.logger.warning(
                        f"Output file missing or untrusted for completed accession '{accession}', will re-process"
                    )
                else:
                    self.logger.debug(
                        f"File not found or untrusted for accession '{accession}' (expected for first-time download)"
                    )
            
            # Performance: Log progress every 10,000 files for very large lists
            if total > 10000 and idx % 10000 == 0 and not silent:
                self.logger.info(f"Validation progress: {idx}/{total} files checked")
        
        # Update progress manager state to remove invalid accessions (only if not silent)
        if not silent and len(valid_accessions) < len(accessions):
            invalid_accessions = accessions - valid_accessions
            self.logger.info(f"Removing {len(invalid_accessions)} invalid completed accessions from state")
            # Update the progress manager's state (ensure state is loaded)
            if self.progress_manager.state is None:
                self.progress_manager.load_state()
            self.progress_manager.state.completed_taxa = valid_accessions
            self.progress_manager._save_state()
        
        return valid_accessions
    
    def _build_file_index(self, search_dir: Path) -> dict:
        """
        Build an index of requested data files in the search directory.
        
        This method recursively searches for .fna files and creates a mapping
        from filename to full path. This is used for efficient file validation,
        especially in auto-fix scenarios where files may be in subdirectories.
        
        Args:
            search_dir: Directory to search (may contain subdirectories)
        
        Returns:
            Dictionary mapping filename to full path
        
        Note:
            - Recursively searches all subdirectories
            - Indexes files matching current include parameters
            - If duplicate filenames exist, uses the first found and logs warning
            - Handles permission errors gracefully
        
        Examples:
            >>> downloader = AccessionDownloader(config)
            >>> index = downloader._build_file_index(Path("/data"))
            >>> "GCA_000001.1.fna" in index
            True
        """
        file_index = {}
        
        try:
            # Recursively find all requested file extensions
            indexed_paths = []
            for extension in get_expected_file_extensions(self.config.include_params):
                indexed_paths.extend(search_dir.rglob(f"*{extension}"))

            for fna_file in sorted(set(indexed_paths), key=lambda path: path.as_posix()):
                try:
                    if fna_file.is_file():
                        filename = fna_file.name
                        if filename not in file_index:
                            file_index[filename] = fna_file
                            self.logger.debug(f"Indexed file: {filename} at {fna_file}")
                        else:
                            # Duplicate filename - log warning
                            self.logger.warning(
                                f"Duplicate filename found: {filename} "
                                f"(using {file_index[filename]}, ignoring {fna_file})"
                            )
                except (PermissionError, OSError) as e:
                    # Skip files we can't access
                    self.logger.debug(f"Cannot access file {fna_file}: {e}")
                    continue
        
        except (PermissionError, OSError) as e:
            self.logger.warning(f"Error building file index: {e}")
        
        self.logger.info(f"Built file index with {len(file_index)} files from {search_dir}")
        
        return file_index
    
    def _signal_handler(self, signum, frame):
        """
        Handle interrupt signals (Ctrl+C and SIGTERM).
        
        Args:
            signum: Signal number
            frame: Current stack frame
        """
        with self._interrupt_lock:
            if not self.interrupted:
                self.interrupted = True
                signal_name = "SIGINT" if signum == signal.SIGINT else f"Signal {signum}"
                self.console.print_warning(f"\n{signal_name} received. Cleaning up...")
                self.logger.warning(f"{signal_name} received, initiating graceful shutdown")
