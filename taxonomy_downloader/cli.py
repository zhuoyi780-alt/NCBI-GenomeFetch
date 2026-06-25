"""
Command-line interface for the taxonomy downloader.
"""

import sys
import signal
import time
import argparse
from pathlib import Path
from typing import Optional
from .config import ConfigManager, ConfigurationError
from .logging_config import setup_logging, get_logger, get_console
from .models import DownloadConfig, DownloadResults
from .progress_manager import ProgressManager
from .error_handler import ErrorHandler
from .download_orchestrator import DownloadOrchestrator
from . import __version__


class TaxonomyDownloader:
    """Main CLI interface for the taxonomy downloader."""
    
    def __init__(self, args: Optional[list] = None):
        self.config: Optional[DownloadConfig] = None
        self.logger = None
        self.console = get_console()
        self.interrupted = False
        self.progress_manager: Optional[ProgressManager] = None
        self.error_handler: Optional[ErrorHandler] = None
        self.orchestrator: Optional[DownloadOrchestrator] = None
        
        # Progress tracking for display
        self._last_progress_display = 0
        self._start_time = time.time()
        
        # Set up signal handlers for graceful shutdown
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        
        try:
            # Parse configuration
            config_manager = ConfigManager()
            self.config = config_manager.parse_args(args)
            
            # Check if split mode is requested
            if hasattr(self.config, '_split_mode') and self.config._split_mode:
                # Run split mode directly without setting up logging
                exit_code = self._run_split_mode()
                sys.exit(exit_code)
            
            # Check if MD5 verification mode is requested
            if hasattr(self.config, '_md5_verification_mode') and self.config._md5_verification_mode:
                # Run MD5 verification mode directly without setting up full logging
                exit_code = self._run_md5_verification_mode()
                sys.exit(exit_code)

            if hasattr(self.config, '_rebuild_md5_mode') and self.config._rebuild_md5_mode:
                exit_code = self._run_rebuild_md5_mode()
                sys.exit(exit_code)
            
            # Check if standalone auto-fix mode is requested
            if hasattr(self.config, '_standalone_autofix_mode') and self.config._standalone_autofix_mode:
                # Run standalone auto-fix mode
                exit_code = self._run_standalone_autofix_mode()
                sys.exit(exit_code)
            
            # Check if accession mode is requested
            if hasattr(self.config, '_accession_mode') and self.config._accession_mode:
                # Run accession mode directly without setting up full logging
                exit_code = self._run_accession_mode()
                sys.exit(exit_code)
            
            # Set up logging (file-only, console handled separately)
            self.logger = setup_logging(self.config.output_dir, console_quiet=True)
            self.logger.info("Taxonomy Downloader starting up")
            
            # Validate inputs
            self._validate_inputs()
            
        except ConfigurationError as e:
            self.console.print_error(f"Configuration Error: {e}")
            sys.exit(1)
        except Exception as e:
            self.console.print_error(f"Initialization Error: {e}")
            sys.exit(1)
    
    def run(self) -> int:
        """
        Run the main download process.
        
        Returns:
            Exit code (0 for success, non-zero for failure)
        """
        if not self.config or not self.logger:
            return 1
        
        try:
            # Log detailed info to files only
            self.logger.info("Starting taxonomy genome download process")
            self.logger.info(f"Configuration: {self.config}")
            
            # Display startup information to console
            self._display_startup_info()
            
            # Load taxonomy names
            config_manager = ConfigManager()
            taxa = config_manager.load_taxa_from_file(self.config.input_file)
            self.logger.info(f"Loaded {len(taxa)} taxonomy names from {self.config.input_file}")
            
            if not taxa:
                self.console.print_error("No taxa found in input file. Exiting.")
                return 1
            
            # Initialize components
            self._initialize_components()
            
            # Check for resume capability
            remaining_taxa = self._handle_resume(taxa)
            
            if not remaining_taxa:
                self.console.print_success("All taxa have already been processed successfully!")
                self._display_final_summary(DownloadResults(
                    total_taxa=len(taxa),
                    successful=len(taxa),
                    failed=0,
                    total_files=0,
                    failed_taxa=[],
                    processing_time=0.0
                ))
                return 0
            
            # Start processing - console output
            self.console.print_info(f"Starting download of {len(remaining_taxa)} taxa using {self.config.max_workers} workers...")
            self.console.print_info(f"Rate limit: {self.config.rate_limit_per_second} requests/second")
            self.console.print_info("Press Ctrl+C to gracefully stop processing")
            print()  # Empty line for spacing
            
            # Set up progress callback
            self.orchestrator.set_progress_callback(self._progress_callback)
            
            # Process taxa
            results = self.orchestrator.process_taxa(remaining_taxa)
            
            # Display final summary
            self._display_final_summary(results)
            
            # Clean up progress state if fully successful
            if results.failed == 0:
                self.progress_manager.cleanup()
            
            self.logger.info("Download process completed successfully")
            return 0 if results.failed == 0 else 1
            
        except KeyboardInterrupt:
            self.logger.warning("Process interrupted by user")
            self.console.print_info("Process interrupted by user. Progress has been saved.")
            return 130
        except Exception as e:
            self.logger.error(f"Unexpected error during processing: {e}")
            self.console.print_error(f"Unexpected error: {e}")
            return 1
    
    def _display_startup_info(self) -> None:
        """Display startup information to user."""
        self.console.print_header("Taxonomy Genome Downloader")
        self.console.print_config(
            **{
                "Output directory": self.config.output_dir,
                "Temporary directory": self.config.temp_dir or "system default",
                "Max workers": self.config.max_workers,
                "Include types": ", ".join(self.config.include_params),
            }
        )
        if self.config.assembly_source:
            print(f"  Assembly source: {self.config.assembly_source}")
        
        # API key status
        if self.config.api_key:
            print("  API key: configured (enhanced rate limits)")
        else:
            print("  API key: not configured (standard rate limits)")
        
        # Disk space backoff configuration
        if self.config.enable_disk_backoff:
            print(f"  Disk backoff: enabled")
            print(f"    Warning threshold: {self.config.disk_warning_threshold:.0%} or {self._format_bytes(self.config.disk_warning_min_bytes)}")
            print(f"    Critical threshold: {self.config.disk_critical_threshold:.0%} or {self._format_bytes(self.config.disk_critical_min_bytes)}")
            print(f"    Minimum threshold: {self.config.disk_minimum_threshold:.0%} or {self._format_bytes(self.config.disk_minimum_bytes)}")
            print(f"    Check interval: {self.config.disk_check_interval}s (normal), {self.config.disk_critical_interval}s (critical)")
        else:
            print("  Disk backoff: disabled")
        
        self.console.print_separator()
    
    def _initialize_components(self) -> None:
        """Initialize all required components."""
        # Progress manager
        state_file = Path(self.config.output_dir) / ".progress_state.json"
        self.progress_manager = ProgressManager(str(state_file))
        
        # Error handler
        error_log_file = Path(self.config.output_dir) / "error.log"
        self.error_handler = ErrorHandler(str(error_log_file))
        
        # Download orchestrator
        self.orchestrator = DownloadOrchestrator(
            self.config, 
            self.progress_manager, 
            self.error_handler
        )
        
        self.logger.info("All components initialized successfully")
    
    def _handle_resume(self, all_taxa: list) -> list:
        """
        Handle resume functionality and return remaining taxa.
        
        Args:
            all_taxa: Complete list of taxa to process
            
        Returns:
            List of taxa that still need processing
        """
        completed_taxa = self.progress_manager.load_state()
        
        if completed_taxa:
            self.console.print_info(f"Found existing progress: {len(completed_taxa)} taxa already completed")
            
            if self.config.resume_validate_files:
                # Validate existing files
                valid_completed = self.progress_manager.validate_existing_files(
                    self.config.output_dir,
                    completed_taxa,
                    include_params=self.config.include_params,
                )
                
                if len(valid_completed) < len(completed_taxa):
                    removed_count = len(completed_taxa) - len(valid_completed)
                    self.console.print_warning(f"{removed_count} previously completed taxa have missing files and will be re-processed")
            else:
                self.console.print_warning(
                    "Skipping output file validation for resume; trusting progress JSON state"
                )
        
        remaining_taxa = self.progress_manager.get_remaining_taxa(all_taxa)
        
        if len(remaining_taxa) < len(all_taxa):
            completed_count = len(all_taxa) - len(remaining_taxa)
            self.console.print_info(f"Resuming: {completed_count} taxa already completed, {len(remaining_taxa)} remaining")
        
        return remaining_taxa
    
    def _progress_callback(self, completed: int, failed: int, total: int, space_status=None) -> None:
        """
        Callback for progress updates during processing.
        
        Args:
            completed: Number of successfully completed taxa
            failed: Number of failed taxa
            total: Total number of taxa being processed
            space_status: Optional SpaceStatus object with disk space information
        """
        current_time = time.time()
        
        # Update display every 5 seconds or on completion
        if (current_time - self._last_progress_display >= 5.0 or 
            completed + failed == total):
            
            elapsed = current_time - self._start_time
            processed = completed + failed
            
            if processed > 0:
                rate = processed / elapsed
                eta_seconds = (total - processed) / rate if rate > 0 else 0
                eta_str = self._format_duration(eta_seconds) if eta_seconds > 0 else ""
            else:
                eta_str = ""
            
            # Build disk space status string if available
            disk_status_str = ""
            if space_status is not None:
                level = space_status.level.value.upper()
                # Use the lower of the two free percentages for display
                min_free_percent = min(space_status.temp_dir_free_percent, space_status.output_dir_free_percent)
                min_free_bytes = min(space_status.temp_dir_free_bytes, space_status.output_dir_free_bytes)
                disk_status_str = f" | Disk: {level} ({min_free_percent:.0%}, {self._format_bytes(min_free_bytes)} free)"
            
            self.console.print_progress(
                completed, failed, total,
                self._format_duration(elapsed),
                eta_str,
                disk_status_str
            )
            
            self._last_progress_display = current_time
    
    def _display_final_summary(self, results: DownloadResults) -> None:
        """
        Display comprehensive summary of processing results.
        
        Args:
            results: Final processing results
        """
        # Calculate rate
        rate_str = ""
        if results.processing_time > 0:
            taxa_per_minute = (results.total_taxa / results.processing_time) * 60
            rate_str = f"{taxa_per_minute:.1f} taxa/minute"
        
        summary_data = {
            "Total taxa processed": results.total_taxa,
            "Successful downloads": results.successful,
            "Failed downloads": results.failed,
            "Success rate": f"{(results.successful/results.total_taxa*100):.1f}%",
            "Total files downloaded": results.total_files,
            "Processing time": self._format_duration(results.processing_time),
        }
        
        if rate_str:
            summary_data["Average rate"] = rate_str
        
        self.console.print_summary(summary_data)
        
        # Failed taxa details
        if results.failed_taxa:
            print(f"\nFailed taxa ({len(results.failed_taxa)}):")
            print("-" * 40)
            
            # Group failures by error type
            error_groups = {}
            for failure in results.failed_taxa:
                error_type = failure.error_type.value if failure.error_type else "unknown"
                if error_type not in error_groups:
                    error_groups[error_type] = []
                error_groups[error_type].append(failure)
            
            for error_type, failures in error_groups.items():
                print(f"\n{error_type.upper()} errors ({len(failures)}):")
                for failure in failures[:10]:
                    print(f"  - {failure.taxon}: {failure.error_message}")
                
                if len(failures) > 10:
                    print(f"  ... and {len(failures) - 10} more")
        
        # Output location
        print(f"\nOutput directory: {self.config.output_dir}")
        print(f"Detailed log: {Path(self.config.output_dir) / 'logs' / 'download.log'}")
        
        if results.failed > 0:
            print(f"Error log: {Path(self.config.output_dir) / 'logs' / 'error.log'}")
            print(f"\nTo resume failed downloads, run the same command again.")
        
        print("=" * 60)
    
    def _format_duration(self, seconds: float) -> str:
        """
        Format duration in human-readable format.
        
        Args:
            seconds: Duration in seconds
            
        Returns:
            Formatted duration string
        """
        if seconds < 60:
            return f"{seconds:.1f}s"
        elif seconds < 3600:
            minutes = int(seconds // 60)
            secs = int(seconds % 60)
            return f"{minutes}m {secs}s"
        else:
            hours = int(seconds // 3600)
            minutes = int((seconds % 3600) // 60)
            return f"{hours}h {minutes}m"
    
    def _format_bytes(self, bytes_value: int) -> str:
        """
        Format bytes in human-readable format.
        
        Args:
            bytes_value: Size in bytes
            
        Returns:
            Formatted size string (e.g., "10.5 GB")
        """
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if abs(bytes_value) < 1024.0:
                return f"{bytes_value:.1f} {unit}"
            bytes_value /= 1024.0
        return f"{bytes_value:.1f} PB"
    
    def _run_split_mode(self) -> int:
        """
        Run split mode workflow.
        
        Returns:
            Exit code (0 for success, non-zero for failure)
        """
        from .split_workflow import SplitWorkflow
        
        # Get taxon and output directory from config
        taxon = self.config._split_taxon
        output_dir = Path(self.config.output_dir) if self.config.output_dir else None
        
        # Create and run workflow
        workflow = SplitWorkflow(taxon, output_dir)
        return workflow.run()
    
    def _run_accession_mode(self) -> int:
        """
        Run accession mode workflow.
        
        Returns:
            Exit code (0 for success, non-zero for failure)
        """
        from .accession_models import AccessionConfig
        from .accession_downloader import AccessionDownloader
        
        # Validate required parameters
        if not self.config._accession_file:
            self.console.print_error("Error: Accession file is required")
            return 1
        
        if not self.config.output_dir:
            self.console.print_error("Error: Output directory is required (-o/--output)")
            return 1
        
        # Create AccessionConfig from parsed arguments
        accession_config = AccessionConfig(
            accession_file=self.config._accession_file,
            output_dir=self.config.output_dir,
            api_key=self.config._api_key,
            batch_size=self.config._batch_size,
            max_workers=self.config.max_workers,
            datasets_executable=self.config.datasets_executable,
            include_params=self.config.include_params,
            temp_dir=self.config.temp_dir,
            resume_validate_files=getattr(self.config, "_resume_validate_files", True),
            download_timeout=getattr(self.config, "_download_timeout", 1800),
            rehydrate_timeout=getattr(self.config, "_rehydrate_timeout", 7200),
            keep_failed_temp=getattr(self.config, "_keep_failed_temp", False),
        )
        
        # Create and run AccessionDownloader
        downloader = AccessionDownloader(accession_config)
        return downloader.run()

    def _run_rebuild_md5_mode(self) -> int:
        """Run accession MD5 rebuild workflow from a dehydrated package."""
        from .accession_md5_rebuilder import AccessionMD5Rebuilder
        from .accession_manifest import ManifestConflictError

        self.console.print_header("Accession MD5 Rebuild")
        print(f"Accession file: {self.config._accession_file}")
        print(f"Output directory: {self.config.output_dir}")
        print(f"Dehydrated package: {self.config._dehy_package}")
        print()

        try:
            rebuilder = AccessionMD5Rebuilder(
                accession_file=self.config._accession_file,
                output_dir=self.config.output_dir,
                dehydrated_package=self.config._dehy_package,
                include_params=self.config.include_params,
            )
            result = rebuilder.rebuild()
        except ManifestConflictError as e:
            self.console.print_error(f"MD5 conflict: {e}")
            return 1
        except Exception as e:
            self.console.print_error(f"Rebuild failed: {e}")
            return 1

        self.console.print_success(
            f"Committed {result.committed_artifacts} trusted artifact(s)"
        )
        if result.missing_outputs:
            self.console.print_warning(
                f"Missing output files: {len(result.missing_outputs)}"
            )
        if result.skipped_unrequested:
            self.console.print_info(
                f"Skipped unrequested package entries: {result.skipped_unrequested}"
            )
        if result.skipped_unrecognized:
            self.console.print_info(
                f"Skipped unrecognized package entries: {result.skipped_unrecognized}"
            )
        return 0
    
    def _run_md5_verification_mode(self) -> int:
        """
        Run MD5 verification workflow.
        
        Returns:
            Exit code (0 for success, non-zero for failure)
        """
        from .md5_verifier import MD5Verifier
        from .md5_report import MD5ReportGenerator
        
        # Get verification directory and auto-fix flag from config
        verification_dir = self.config._md5_verification_dir
        auto_fix_enabled = bool(self.config._md5_auto_fix)
        failed_file_path = self.config._md5_failed_file if hasattr(self.config, '_md5_failed_file') else None
        
        # Display header
        self.console.print_header("MD5 Checksum Verification")
        print(f"Directory: {verification_dir}")
        if auto_fix_enabled:
            print("Auto-fix: enabled")
        print()
        
        try:
            # Create and run verifier
            verifier = MD5Verifier(verification_dir)
            result = verifier.verify()
            
            # Generate console report
            generator = MD5ReportGenerator()
            generator.generate_console_report(result)
            
            # Generate file report
            report_path = Path(verification_dir) / "md5_verification_report.txt"
            try:
                generator.generate_file_report(result, report_path)
                print(f"\nReport saved to: {report_path}")
            except IOError as e:
                print(f"\nWarning: Could not save report file: {e}")
            
            # Check if there are failed files
            has_failed_files = (result.failed > 0 or result.missing > 0 or result.errors > 0)
            
            # Save failed files list if there are failures
            if has_failed_files:
                # Determine output path for failed files list
                if failed_file_path:
                    # User specified a custom path with --md5sum-auto-fix
                    failed_list_path = Path(failed_file_path)
                else:
                    # Default: save to current working directory
                    failed_list_path = Path.cwd() / "md5_failed_files.txt"
                
                try:
                    self._save_failed_files_list(result, failed_list_path)
                    print(f"\nFailed files list saved to: {failed_list_path}")
                except IOError as e:
                    print(f"\nWarning: Could not save failed files list: {e}")
            
            # Check if auto-fix is enabled
            if auto_fix_enabled and has_failed_files:
                print("\n" + "=" * 60)
                print("Starting auto-fix process...")
                print("=" * 60 + "\n")
                
                # Import AutoFixCoordinator
                from .md5_autofix_coordinator import AutoFixCoordinator
                
                # Create coordinator with appropriate parameters
                coordinator = AutoFixCoordinator(
                    verification_result=result,
                    verification_dir=verification_dir,
                    datasets_executable=self.config.datasets_executable,
                    api_key=self.config.api_key,
                    include_params=self.config.include_params,
                    batch_size=getattr(self.config, '_batch_size', 100),
                    max_workers=self.config.max_workers,
                    no_resume=getattr(self.config, '_md5_auto_fix_no_resume', False),
                    new_run=getattr(self.config, '_md5_auto_fix_new_run', False),
                    retry_failed=getattr(self.config, '_md5_auto_fix_retry_failed', False),
                    keep_cache=getattr(self.config, '_md5_auto_fix_keep_cache', False),
                    clear_state=getattr(self.config, '_md5_auto_fix_clear_state', False),
                    clear_lock=getattr(self.config, '_md5_auto_fix_clear_lock', False)
                )
                
                # Execute auto-fix
                auto_fix_result = coordinator.execute_auto_fix()
                
                # Display auto-fix results
                print("\n" + "=" * 60)
                print("Auto-fix completed")
                print("=" * 60)
                print(f"Total failed files: {auto_fix_result.total_failed}")
                print(f"Redownloaded: {auto_fix_result.redownloaded}")
                print(f"Successfully fixed: {auto_fix_result.successfully_fixed}")
                print(f"Still failed: {auto_fix_result.still_failed}")
                print(f"Skipped: {auto_fix_result.skipped}")
                print(f"Processing time: {self._format_duration(auto_fix_result.processing_time)}")
                
                if auto_fix_result.report_path:
                    print(f"\nDetailed report saved to: {auto_fix_result.report_path}")
                
                # Return exit code based on auto-fix results
                # Exit code 0 if all files were fixed, non-zero otherwise
                if auto_fix_result.still_failed > 0 or auto_fix_result.skipped > 0:
                    return 1
                return 0
            
            # Return exit code based on verification results
            # Exit code 0 if all files passed, non-zero otherwise
            if has_failed_files:
                return 1
            return 0
            
        except ValueError as e:
            self.console.print_error(f"Verification Error: {e}")
            return 1
        except FileNotFoundError as e:
            self.console.print_error(f"File Not Found: {e}")
            return 1
        except Exception as e:
            self.console.print_error(f"Unexpected Error: {e}")
            return 1
    
    def _signal_handler(self, signum, frame):
        """Handle interrupt signals for graceful shutdown."""
        if not self.interrupted:
            self.interrupted = True
            if self.logger:
                self.logger.warning(f"Received signal {signum}, initiating graceful shutdown...")
            else:
                print(f"\nReceived signal {signum}, shutting down gracefully...", file=sys.stderr)
            
            # Request shutdown from orchestrator if available
            if self.orchestrator:
                self.orchestrator.request_shutdown()
        else:
            # Second interrupt, force exit
            if self.logger:
                self.logger.error("Second interrupt received, forcing exit")
            print("\nForcing immediate exit...", file=sys.stderr)
            sys.exit(1)
    
    def _save_failed_files_list(self, result, output_path: Path) -> None:
        """
        Save list of failed files to a text file.
        
        Args:
            result: MD5 verification result
            output_path: Path to save the failed files list
        """
        from .md5_models import VerificationStatus
        
        failed_results = [
            fr for fr in result.file_results
            if fr.status in [VerificationStatus.FAIL, VerificationStatus.MISSING, VerificationStatus.ERROR]
        ]
        
        # Get verification directory for reference
        verification_dir = Path(self.config._md5_verification_dir)
        
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write("# MD5 Verification Failed Files\n")
            f.write(f"# Generated: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"# Verification Directory: {verification_dir}\n")
            f.write(f"# Total failed files: {len(failed_results)}\n")
            f.write("#\n")
            f.write("# Format: STATUS | FILE_PATH | EXPECTED_HASH | COMPUTED_HASH | ERROR_MESSAGE\n")
            f.write("# FILE_PATH is relative to the verification directory\n")
            f.write("#" + "=" * 80 + "\n\n")
            
            for fr in failed_results:
                status = fr.status.value.upper()
                file_path = fr.file_path  # Already contains full relative path
                expected = fr.expected_hash or "N/A"
                computed = fr.computed_hash or "N/A"
                error = fr.error_message or ""
                
                f.write(f"{status} | {file_path} | {expected} | {computed} | {error}\n")
    
    def _run_standalone_autofix_mode(self) -> int:
        """
        Run standalone auto-fix workflow.
        
        This mode reads a failed files list and performs auto-fix without
        running MD5 verification first.
        
        Returns:
            Exit code (0 for success, non-zero for failure)
        """
        from .md5_autofix_models import FailedFile
        from .md5_autofix_coordinator import AutoFixCoordinator
        from .md5_models import VerificationResult as MD5VerificationResult, FileVerificationResult, VerificationStatus
        
        failed_file_path = Path(self.config._md5_failed_file)
        
        # Display header
        self.console.print_header("MD5 Auto-Fix (Standalone Mode)")
        print(f"Failed files list: {failed_file_path}")
        print()
        
        try:
            # Extract verification directory from the failed files list
            verification_dir = self._extract_verification_dir_from_list(failed_file_path)
            
            # Parse the failed files list
            print("Reading failed files list...")
            failed_file_results = self._parse_failed_files_list(failed_file_path)
            
            if not failed_file_results:
                print("No failed files found in the list.")
                return 0
            
            print(f"Found {len(failed_file_results)} failed files")
            print(f"Verification directory: {verification_dir}")
            print()
            
            # Create a mock MD5VerificationResult for the coordinator
            mock_result = MD5VerificationResult(
                total_files=len(failed_file_results),
                passed=0,
                failed=len([f for f in failed_file_results if f.status == VerificationStatus.FAIL]),
                missing=len([f for f in failed_file_results if f.status == VerificationStatus.MISSING]),
                errors=len([f for f in failed_file_results if f.status == VerificationStatus.ERROR]),
                file_results=failed_file_results,
                processing_time=0.0
            )
            
            # Start auto-fix process
            print("=" * 60)
            print("Starting auto-fix process...")
            print("=" * 60 + "\n")
            
            # Create coordinator
            coordinator = AutoFixCoordinator(
                verification_result=mock_result,
                verification_dir=str(verification_dir),
                datasets_executable=self.config.datasets_executable,
                api_key=self.config.api_key,
                include_params=self.config.include_params,
                batch_size=getattr(self.config, '_batch_size', 100),
                max_workers=self.config.max_workers,
                no_resume=getattr(self.config, '_md5_auto_fix_no_resume', False),
                new_run=getattr(self.config, '_md5_auto_fix_new_run', False),
                retry_failed=getattr(self.config, '_md5_auto_fix_retry_failed', False),
                keep_cache=getattr(self.config, '_md5_auto_fix_keep_cache', False),
                clear_state=getattr(self.config, '_md5_auto_fix_clear_state', False),
                clear_lock=getattr(self.config, '_md5_auto_fix_clear_lock', False)
            )
            
            # Execute auto-fix
            auto_fix_result = coordinator.execute_auto_fix()
            
            # Display results
            print("\n" + "=" * 60)
            print("Auto-fix completed")
            print("=" * 60)
            print(f"Total failed files: {auto_fix_result.total_failed}")
            print(f"Redownloaded: {auto_fix_result.redownloaded}")
            print(f"Successfully fixed: {auto_fix_result.successfully_fixed}")
            print(f"Still failed: {auto_fix_result.still_failed}")
            print(f"Skipped: {auto_fix_result.skipped}")
            print(f"Processing time: {self._format_duration(auto_fix_result.processing_time)}")
            
            if auto_fix_result.report_path:
                print(f"\nDetailed report saved to: {auto_fix_result.report_path}")
            
            # Return exit code based on results
            if auto_fix_result.still_failed > 0 or auto_fix_result.skipped > 0:
                return 1
            return 0
            
        except FileNotFoundError as e:
            self.console.print_error(f"File Not Found: {e}")
            return 1
        except Exception as e:
            self.console.print_error(f"Unexpected Error: {e}")
            import traceback
            traceback.print_exc()
            return 1
    
    def _extract_verification_dir_from_list(self, file_path: Path) -> Path:
        """
        Extract verification directory from the failed files list header.
        
        Args:
            file_path: Path to the failed files list
            
        Returns:
            Path to the verification directory
            
        Raises:
            ValueError: If verification directory cannot be found in the file
        """
        with open(file_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                
                # Look for the verification directory comment
                if line.startswith('# Verification Directory:'):
                    # Extract the directory path
                    dir_str = line.split(':', 1)[1].strip()
                    verification_dir = Path(dir_str)
                    
                    # Validate that the directory exists
                    if not verification_dir.exists():
                        raise ValueError(
                            f"Verification directory does not exist: {verification_dir}\n"
                            f"The directory may have been moved or deleted."
                        )
                    
                    if not verification_dir.is_dir():
                        raise ValueError(
                            f"Verification directory path is not a directory: {verification_dir}"
                        )
                    
                    return verification_dir
        
        # If we reach here, the verification directory was not found in the file
        raise ValueError(
            f"Could not find 'Verification Directory' in {file_path}\n"
            f"The file may be corrupted or in an old format.\n"
            f"Please regenerate the failed files list using --md5sum."
        )
    
    def _parse_failed_files_list(self, file_path: Path):
        """
        Parse the failed files list file.
        
        Args:
            file_path: Path to the failed files list
            
        Returns:
            List of FileVerificationResult objects
        """
        from .md5_models import FileVerificationResult, VerificationStatus
        
        results = []
        
        with open(file_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                
                # Skip comments and empty lines
                if not line or line.startswith('#'):
                    continue
                
                # Parse line: STATUS | FILE_PATH | EXPECTED_HASH | COMPUTED_HASH | ERROR_MESSAGE
                parts = [p.strip() for p in line.split('|')]
                
                if len(parts) < 3:
                    continue
                
                status_str = parts[0]
                file_path_str = parts[1]
                expected_hash = parts[2] if len(parts) > 2 and parts[2] != "N/A" else None
                computed_hash = parts[3] if len(parts) > 3 and parts[3] != "N/A" else None
                error_message = parts[4] if len(parts) > 4 else None
                
                # Map status string to enum
                status_map = {
                    'FAIL': VerificationStatus.FAIL,
                    'MISSING': VerificationStatus.MISSING,
                    'ERROR': VerificationStatus.ERROR
                }
                status = status_map.get(status_str, VerificationStatus.ERROR)
                
                # Create FileVerificationResult with the complete file_path
                # The file_path should already be in the correct format from the verification
                result = FileVerificationResult(
                    file_path=file_path_str,  # Keep the complete relative path
                    expected_hash=expected_hash,
                    computed_hash=computed_hash,
                    status=status,
                    error_message=error_message
                )
                
                results.append(result)
        
        return results
    
    def _validate_inputs(self) -> None:
        """Validate input parameters and environment."""
        if not self.config:
            raise RuntimeError("Configuration not initialized")
        
        # Validate configuration parameters
        errors = self.config.validate_parameters()
        if errors:
            error_msg = "Configuration validation failed:\n" + "\n".join(f"  - {error}" for error in errors)
            raise ConfigurationError(error_msg)
        
        # Validate input file exists
        if not Path(self.config.input_file).exists():
            raise ConfigurationError(f"Input file not found: {self.config.input_file}")
        
        # Validate output directory can be created
        try:
            Path(self.config.output_dir).mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise ConfigurationError(f"Cannot create output directory {self.config.output_dir}: {e}")
        
        # Validate temporary directory if specified
        if self.config.temp_dir:
            try:
                Path(self.config.temp_dir).mkdir(parents=True, exist_ok=True)
            except OSError as e:
                raise ConfigurationError(f"Cannot create temporary directory {self.config.temp_dir}: {e}")
        
        self.logger.debug("Input validation completed")


def main():
    """Entry point for the command-line interface."""
    downloader = TaxonomyDownloader()
    exit_code = downloader.run()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
