"""
Download orchestration with multi-threading and thread-safe rate limiting.
"""

import signal
import threading
import time
from concurrent.futures import ThreadPoolExecutor, Future, as_completed
from typing import List, Dict, Any, Optional, Callable
from pathlib import Path

from .models import DownloadConfig, TaxonResult, DownloadResults, ErrorType, SpaceLevel, SpaceStatus
from .taxon_processor import TaxonProcessor
from .progress_manager import ProgressManager
from .error_handler import ErrorHandler
from .rate_limiter import RateLimiter
from .logging_config import get_logger
from .disk_space_monitor import DiskSpaceMonitor
from .backoff_controller import BackoffController


# Type alias for extended progress callback that includes disk space status
# Signature: (completed, failed, total, space_status_or_none)
ExtendedProgressCallback = Callable[[int, int, int, Optional['SpaceStatus']], None]


class DownloadOrchestrator:
    """
    Orchestrates multi-threaded genome downloads with thread-safe rate limiting.
    
    Manages thread pool execution, progress monitoring, result aggregation,
    and graceful shutdown handling for concurrent taxon processing.
    """
    
    def __init__(self, config: DownloadConfig, progress_manager: ProgressManager, 
                 error_handler: ErrorHandler):
        """
        Initialize the download orchestrator.
        
        Args:
            config: Download configuration
            progress_manager: Progress tracking manager
            error_handler: Error handling and logging
        """
        self.config = config
        self.progress_manager = progress_manager
        self.error_handler = error_handler
        self.logger = get_logger("download_orchestrator")
        
        # Thread-safe rate limiter (singleton pattern for global rate limiting)
        self._rate_limiter = RateLimiter(config.rate_limit_per_second)
        
        # Thread pool and execution state
        self._executor: Optional[ThreadPoolExecutor] = None
        self._futures: List[Future] = []
        self._shutdown_requested = False
        self._shutdown_lock = threading.Lock()
        
        # Progress monitoring
        self._progress_callback: Optional[Callable[[int, int, int], None]] = None
        self._completed_count = 0
        self._failed_count = 0
        self._total_count = 0
        self._progress_lock = threading.Lock()
        
        # Results aggregation
        self._results: List[TaxonResult] = []
        self._results_lock = threading.Lock()
        
        # Disk space backoff components (initialized lazily in process_taxa)
        self._disk_monitor: Optional[DiskSpaceMonitor] = None
        self._backoff_controller: Optional[BackoffController] = None
        
        # Setup signal handlers for graceful shutdown
        self._setup_signal_handlers()
        
        self.logger.info(f"DownloadOrchestrator initialized with {config.max_workers} workers, "
                        f"rate limit: {config.rate_limit_per_second} requests/second"
                        f"{', disk backoff enabled' if config.enable_disk_backoff else ''}")
    
    def _setup_signal_handlers(self) -> None:
        """Setup signal handlers for graceful shutdown."""
        def signal_handler(signum, frame):
            self.logger.info(f"Received signal {signum}, initiating graceful shutdown...")
            self.request_shutdown()
        
        # Register handlers for common termination signals
        try:
            signal.signal(signal.SIGINT, signal_handler)   # Ctrl+C
            signal.signal(signal.SIGTERM, signal_handler)  # Termination request
        except (OSError, ValueError) as e:
            # Signal handling may not be available in all environments (e.g., Windows threads)
            self.logger.debug(f"Could not register signal handlers: {e}")
    
    def set_progress_callback(self, callback: Callable[[int, int, int], None]) -> None:
        """
        Set callback function for progress updates.
        
        The callback can accept either 3 or 4 arguments:
        - 3 args: (completed, failed, total) - basic progress
        - 4 args: (completed, failed, total, space_status) - extended with disk space
        
        Args:
            callback: Function called with progress counts and optionally space status
        """
        self._progress_callback = callback
    
    def request_shutdown(self) -> None:
        """Request graceful shutdown of all processing."""
        with self._shutdown_lock:
            if not self._shutdown_requested:
                self._shutdown_requested = True
                self.logger.info("Shutdown requested - will complete current tasks and stop")
    
    def is_shutdown_requested(self) -> bool:
        """Check if shutdown has been requested."""
        with self._shutdown_lock:
            return self._shutdown_requested
    
    def process_taxa(self, taxa: List[str], estimated_size_per_taxon: int = 0) -> DownloadResults:
        """
        Process multiple taxa using thread pool with rate limiting.
        
        Args:
            taxa: List of taxon names to process
            estimated_size_per_taxon: Optional estimated size per taxon in bytes for disk space check
            
        Returns:
            DownloadResults with aggregated processing results
        """
        start_time = time.time()
        self._total_count = len(taxa)
        self._completed_count = 0
        self._failed_count = 0
        self._results.clear()
        
        self.logger.info(f"Starting processing of {len(taxa)} taxa with {self.config.max_workers} workers")
        
        # Perform disk space check if estimated size is provided
        if estimated_size_per_taxon > 0:
            self._check_disk_space_before_start(taxa, estimated_size_per_taxon)
        
        # Initialize disk space backoff components if enabled
        if self.config.enable_disk_backoff:
            self._initialize_disk_backoff()
        
        try:
            # Start disk space monitoring if enabled
            if self._disk_monitor is not None:
                self._disk_monitor.start()
                self.logger.info("Disk space monitoring started")
            
            # Create thread pool
            self._executor = self._create_worker_pool()
            
            # Submit all tasks
            self._futures = []
            for taxon in taxa:
                if self.is_shutdown_requested():
                    self.logger.info("Shutdown requested before submitting all tasks")
                    break
                
                future = self._executor.submit(self._process_single_taxon, taxon)
                self._futures.append(future)
            
            # Monitor progress and collect results
            results = self._monitor_progress_and_collect_results()
            
            # Calculate final statistics
            processing_time = time.time() - start_time
            successful_results = [r for r in results if r.success]
            failed_results = [r for r in results if not r.success]
            total_files = sum(r.files_found for r in successful_results)
            
            download_results = DownloadResults(
                total_taxa=len(taxa),
                successful=len(successful_results),
                failed=len(failed_results),
                total_files=total_files,
                failed_taxa=failed_results,
                processing_time=processing_time,
                start_time=start_time
            )
            
            self.logger.info(f"Processing completed: {download_results.successful}/{download_results.total_taxa} "
                           f"successful in {processing_time:.2f}s")
            
            return download_results
            
        except Exception as e:
            self.error_handler.log_error(
                taxon="orchestrator",
                error_type=ErrorType.PROCESSING,
                message=f"Orchestrator failed: {str(e)}",
                exception=e
            )
            raise
        
        finally:
            # Stop disk space monitor to prevent zombie threads
            if self._disk_monitor is not None:
                self._disk_monitor.stop()
                self.logger.debug("Disk space monitor stopped")
            
            self._cleanup_executor()
    
    def _create_worker_pool(self) -> ThreadPoolExecutor:
        """
        Create thread pool executor with proper configuration.
        
        Returns:
            Configured ThreadPoolExecutor
        """
        # Use daemon threads to ensure clean shutdown
        executor = ThreadPoolExecutor(
            max_workers=self.config.max_workers,
            thread_name_prefix="taxon_worker"
        )
        
        self.logger.debug(f"Created thread pool with {self.config.max_workers} workers")
        return executor
    
    def _initialize_disk_backoff(self) -> None:
        """
        Initialize disk space monitoring and backoff controller.
        
        Creates DiskSpaceMonitor and BackoffController with configuration
        from DownloadConfig. The monitor's callback is connected to the
        controller's on_space_update method.
        """
        # Determine directories to monitor
        temp_dir = Path(self.config.temp_dir) if self.config.temp_dir else Path(self.config.output_dir)
        output_dir = Path(self.config.output_dir)
        
        # Create backoff controller
        self._backoff_controller = BackoffController(
            max_workers=self.config.max_workers,
            warning_threshold=self.config.disk_warning_threshold,
            critical_threshold=self.config.disk_critical_threshold,
            minimum_threshold=self.config.disk_minimum_threshold,
            warning_min_bytes=self.config.disk_warning_min_bytes,
            critical_min_bytes=self.config.disk_critical_min_bytes,
            minimum_bytes=self.config.disk_minimum_bytes,
            hysteresis_margin=self.config.disk_hysteresis_margin,
        )
        
        # Create disk space monitor
        self._disk_monitor = DiskSpaceMonitor(
            temp_dir=temp_dir,
            output_dir=output_dir,
            check_interval=self.config.disk_check_interval,
            critical_interval=self.config.disk_critical_interval,
            warning_threshold=self.config.disk_warning_threshold,
            critical_threshold=self.config.disk_critical_threshold,
            minimum_threshold=self.config.disk_minimum_threshold,
            warning_min_bytes=self.config.disk_warning_min_bytes,
            critical_min_bytes=self.config.disk_critical_min_bytes,
            minimum_bytes=self.config.disk_minimum_bytes,
        )
        
        # Connect monitor callback to controller
        self._disk_monitor.set_callback(self._backoff_controller.on_space_update)
        
        self.logger.info(
            f"Disk backoff initialized: warning={self.config.disk_warning_threshold:.0%}, "
            f"critical={self.config.disk_critical_threshold:.0%}, "
            f"minimum={self.config.disk_minimum_threshold:.0%}"
        )
    
    def _process_single_taxon(self, taxon: str) -> TaxonResult:
        """
        Process a single taxon with rate limiting and error handling.
        
        This method runs in worker threads and must be thread-safe.
        Uses backoff controller for disk space-aware concurrency control.
        
        Args:
            taxon: Taxon name to process
            
        Returns:
            TaxonResult with processing outcome
        """
        # Check for shutdown before starting
        if self.is_shutdown_requested():
            return TaxonResult(
                taxon=taxon,
                success=False,
                error_message="Processing cancelled due to shutdown request",
                error_type=ErrorType.PROCESSING
            )
        
        # Acquire execution slot if backoff controller is enabled
        slot_acquired = False
        if self._backoff_controller is not None:
            # Use a reasonable timeout to allow for disk space recovery
            slot_acquired = self._backoff_controller.acquire_slot(timeout=300.0)  # 5 minute timeout
            if not slot_acquired:
                # Log user-friendly message about disk space pause
                self.logger.warning(
                    f"Disk space extremely low, pausing new tasks... "
                    f"Task '{taxon}' waiting for disk space to recover."
                )
                return TaxonResult(
                    taxon=taxon,
                    success=False,
                    error_message="Disk space too low, task paused and timed out waiting for recovery",
                    error_type=ErrorType.FILESYSTEM
                )
        
        try:
            # Apply rate limiting (thread-safe)
            if not self._rate_limiter.acquire(timeout=30.0):
                return TaxonResult(
                    taxon=taxon,
                    success=False,
                    error_message="Rate limiting timeout exceeded",
                    error_type=ErrorType.TRANSIENT
                )
            
            # Create processor for this thread (not shared between threads)
            processor = TaxonProcessor(self.config, self.error_handler)
            
            # Process the taxon
            result = processor.process_taxon(taxon)
            
            # Update progress and save state
            self._update_progress(result)
            
            return result
            
        except Exception as e:
            # Handle unexpected errors in worker thread
            self.error_handler.log_error(
                taxon=taxon,
                error_type=ErrorType.PROCESSING,
                message=f"Unexpected error in worker thread: {str(e)}",
                exception=e
            )
            
            result = TaxonResult(
                taxon=taxon,
                success=False,
                error_message=f"Worker thread error: {str(e)}",
                error_type=ErrorType.PROCESSING
            )
            
            self._update_progress(result)
            return result
        
        finally:
            # Always release the slot if it was acquired
            if self._backoff_controller is not None and slot_acquired:
                self._backoff_controller.release_slot()
    
    def _update_progress(self, result: TaxonResult) -> None:
        """
        Update progress counters and save state (thread-safe).
        
        Args:
            result: Result of taxon processing
        """
        with self._progress_lock:
            if result.success:
                self._completed_count += 1
                self.progress_manager.save_completed_taxon(result.taxon)
            else:
                self._failed_count += 1
                self.progress_manager.save_failed_taxon(result.taxon)
            
            # Store result for final aggregation
            with self._results_lock:
                self._results.append(result)
            
            # Call progress callback if set
            if self._progress_callback:
                try:
                    # Get current disk space status if monitor is running
                    space_status = None
                    if self._disk_monitor is not None and self._disk_monitor.is_running():
                        space_status = self._disk_monitor.get_current_status()
                    
                    # Try to call with extended signature (4 args) first
                    # Fall back to basic signature (3 args) for backward compatibility
                    import inspect
                    sig = inspect.signature(self._progress_callback)
                    num_params = len(sig.parameters)
                    
                    if num_params >= 4:
                        # Extended callback with disk space status
                        self._progress_callback(
                            self._completed_count, 
                            self._failed_count, 
                            self._total_count,
                            space_status
                        )
                    else:
                        # Basic callback without disk space status
                        self._progress_callback(
                            self._completed_count, 
                            self._failed_count, 
                            self._total_count
                        )
                except Exception as e:
                    self.logger.warning(f"Progress callback failed: {e}")
    
    def _monitor_progress_and_collect_results(self) -> List[TaxonResult]:
        """
        Monitor task progress and collect results as they complete.
        
        Returns:
            List of all TaxonResult objects
        """
        completed_futures = 0
        last_progress_log = 0
        
        try:
            # Process completed futures as they finish
            for future in as_completed(self._futures):
                completed_futures += 1
                
                try:
                    # Get result (this will re-raise any exception from the worker)
                    result = future.result()
                    
                    # Log progress periodically
                    if completed_futures - last_progress_log >= 10 or completed_futures == len(self._futures):
                        self.logger.info(f"Progress: {completed_futures}/{len(self._futures)} tasks completed "
                                       f"({self._completed_count} successful, {self._failed_count} failed)")
                        last_progress_log = completed_futures
                    
                    # Check for shutdown request
                    if self.is_shutdown_requested():
                        self.logger.info(f"Shutdown requested, cancelling remaining {len(self._futures) - completed_futures} tasks")
                        self._cancel_remaining_futures(completed_futures)
                        break
                        
                except Exception as e:
                    # This should not happen as exceptions are handled in _process_single_taxon
                    self.logger.error(f"Unexpected error collecting future result: {e}")
            
            # Return collected results
            with self._results_lock:
                return self._results.copy()
                
        except KeyboardInterrupt:
            self.logger.info("Keyboard interrupt received, cancelling remaining tasks")
            self.request_shutdown()
            self._cancel_remaining_futures(completed_futures)
            
            with self._results_lock:
                return self._results.copy()
    
    def _cancel_remaining_futures(self, completed_count: int) -> None:
        """
        Cancel remaining futures that haven't completed yet.
        
        Args:
            completed_count: Number of futures already completed
        """
        cancelled_count = 0
        
        for i, future in enumerate(self._futures[completed_count:], completed_count):
            if future.cancel():
                cancelled_count += 1
            else:
                # Future is already running, let it complete
                pass
        
        self.logger.info(f"Cancelled {cancelled_count} pending tasks")
    
    def _cleanup_executor(self) -> None:
        """Clean up thread pool executor."""
        if self._executor:
            try:
                # Shutdown executor gracefully
                self._executor.shutdown(wait=True)
                self.logger.debug("Thread pool executor shut down successfully")
            except Exception as e:
                self.logger.warning(f"Error shutting down executor: {e}")
            finally:
                self._executor = None
                self._futures.clear()
    
    def get_rate_limiter_stats(self) -> Dict[str, Any]:
        """
        Get current rate limiter statistics.
        
        Returns:
            Dictionary with rate limiter information
        """
        return {
            "requests_per_second": self._rate_limiter.requests_per_second,
            "interval": self._rate_limiter.interval,
            "last_request_time": self._rate_limiter.last_request_time,
            "current_time": time.time()
        }
    
    def get_orchestrator_stats(self) -> Dict[str, Any]:
        """
        Get current orchestrator statistics.
        
        Returns:
            Dictionary with orchestrator information
        """
        with self._progress_lock:
            stats = {
                "max_workers": self.config.max_workers,
                "total_count": self._total_count,
                "completed_count": self._completed_count,
                "failed_count": self._failed_count,
                "shutdown_requested": self.is_shutdown_requested(),
                "active_futures": len(self._futures),
                "rate_limiter": self.get_rate_limiter_stats()
            }
            
            # Add disk backoff stats if enabled
            if self._backoff_controller is not None:
                stats["disk_backoff"] = {
                    "enabled": True,
                    "current_level": self._backoff_controller.get_current_level().value,
                    "active_workers": self._backoff_controller.get_active_workers(),
                    "target_workers": self._backoff_controller.get_target_workers(),
                    "is_paused": self._backoff_controller.is_paused(),
                    "held_tokens": self._backoff_controller.get_held_tokens(),
                }
            else:
                stats["disk_backoff"] = {"enabled": False}
            
            # Add disk space status if monitor is running
            if self._disk_monitor is not None and self._disk_monitor.is_running():
                space_status = self._disk_monitor.get_current_status()
                stats["disk_space"] = {
                    "temp_dir_free_bytes": space_status.temp_dir_free_bytes,
                    "temp_dir_free_percent": space_status.temp_dir_free_percent,
                    "output_dir_free_bytes": space_status.output_dir_free_bytes,
                    "output_dir_free_percent": space_status.output_dir_free_percent,
                    "level": space_status.level.value,
                }
            
            return stats
    
    def update_rate_limit(self, requests_per_second: float) -> None:
        """
        Update the rate limit for all workers.
        
        Args:
            requests_per_second: New rate limit
        """
        self._rate_limiter.update_rate(requests_per_second)
        self.logger.info(f"Updated global rate limit to {requests_per_second} requests/second")
    
    def get_disk_space_status(self) -> Optional[SpaceStatus]:
        """
        Get the current disk space status.
        
        Returns:
            SpaceStatus object if disk monitoring is enabled and running, None otherwise
        """
        if self._disk_monitor is not None and self._disk_monitor.is_running():
            return self._disk_monitor.get_current_status()
        return None
    
    def _check_disk_space_before_start(self, taxa: List[str], estimated_size_per_taxon: int) -> None:
        """
        Check disk space before starting processing and log warnings.
        
        Args:
            taxa: List of taxa to process
            estimated_size_per_taxon: Estimated size per taxon in bytes
        """
        try:
            # Create a temporary processor to use its disk space check
            processor = TaxonProcessor(self.config, self.error_handler)
            
            # Estimate total size (use max concurrent workers for peak calculation)
            # Peak occurs when max_workers are all processing simultaneously
            total_estimated = estimated_size_per_taxon * len(taxa)
            
            # Check disk space with concurrent worker count
            space_check = processor.check_disk_space(
                estimated_size_bytes=total_estimated,
                num_concurrent=self.config.max_workers
            )
            
            # Log warnings if any
            for warning in space_check.get('warnings', []):
                self.logger.warning(f"Disk space warning: {warning}")
            
            if not space_check.get('overall_sufficient', True):
                self.logger.warning(
                    f"Disk space may be insufficient for processing {len(taxa)} taxa. "
                    f"Estimated peak usage: {processor._format_bytes(space_check['required_with_margin'])}. "
                    f"Consider reducing --workers or using --temp-dir on a different disk."
                )
            else:
                self.logger.info(
                    f"Disk space check passed. "
                    f"Estimated peak usage: {processor._format_bytes(space_check['required_with_margin'])}"
                )
                
        except Exception as e:
            self.logger.debug(f"Could not perform disk space check: {e}")