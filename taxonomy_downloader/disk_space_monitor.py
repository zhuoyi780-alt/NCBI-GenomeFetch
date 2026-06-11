"""
Disk space monitor for periodic monitoring of available disk space.

This module implements a background monitoring thread that periodically checks
disk space and notifies a callback (typically BackoffController) when space
status changes.
"""

import os
import shutil
import threading
import logging
import time
from pathlib import Path
from typing import Optional, Callable

from taxonomy_downloader.models import SpaceLevel, SpaceStatus

logger = logging.getLogger(__name__)


class DiskSpaceMonitor:
    """
    Periodically monitors disk space in background thread.
    
    The monitor checks both temporary and output directories at configurable
    intervals. When space level enters CRITICAL, the check interval is
    automatically reduced to respond faster to rapid space consumption.
    
    Thread Safety:
        The monitor runs in a background daemon thread. The callback is
        invoked from this thread, so the callback must be thread-safe.
    
    Usage:
        monitor = DiskSpaceMonitor(temp_dir, output_dir)
        monitor.set_callback(backoff_controller.on_space_update)
        monitor.start()
        try:
            # ... do work ...
        finally:
            monitor.stop()
    """
    
    def __init__(
        self,
        temp_dir: Path,
        output_dir: Path,
        check_interval: float = 30.0,
        critical_interval: float = 5.0,
        warning_threshold: float = 0.20,
        critical_threshold: float = 0.10,
        minimum_threshold: float = 0.05,
        warning_min_bytes: int = 10 * 1024 * 1024 * 1024,   # 10GB
        critical_min_bytes: int = 5 * 1024 * 1024 * 1024,   # 5GB
        minimum_bytes: int = 1 * 1024 * 1024 * 1024,        # 1GB
    ):
        """
        Initialize the disk space monitor.
        
        Args:
            temp_dir: Temporary directory to monitor
            output_dir: Output directory to monitor
            check_interval: Normal state check interval in seconds
            critical_interval: CRITICAL state check interval in seconds
            warning_threshold: Space percentage for WARNING level
            critical_threshold: Space percentage for CRITICAL level
            minimum_threshold: Space percentage for PAUSED level
            warning_min_bytes: Minimum absolute bytes for WARNING level
            critical_min_bytes: Minimum absolute bytes for CRITICAL level
            minimum_bytes: Minimum absolute bytes for PAUSED level
        """
        self.temp_dir = Path(temp_dir)
        self.output_dir = Path(output_dir)
        self.check_interval = check_interval
        self.critical_interval = critical_interval
        
        # Thresholds for level determination
        self.warning_threshold = warning_threshold
        self.critical_threshold = critical_threshold
        self.minimum_threshold = minimum_threshold
        self.warning_min_bytes = warning_min_bytes
        self.critical_min_bytes = critical_min_bytes
        self.minimum_bytes = minimum_bytes
        
        # Callback for space updates
        self._callback: Optional[Callable[[SpaceStatus], None]] = None
        
        # Thread control
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        
        # Current status cache
        self._current_status: Optional[SpaceStatus] = None
        self._status_lock = threading.Lock()
        
        logger.debug(
            f"DiskSpaceMonitor initialized: temp_dir={temp_dir}, output_dir={output_dir}, "
            f"check_interval={check_interval}s, critical_interval={critical_interval}s"
        )

    def set_callback(self, callback: Callable[[SpaceStatus], None]) -> None:
        """
        Set the callback to be invoked when space status is updated.
        
        The callback will be called from the monitoring thread, so it
        must be thread-safe.
        
        Args:
            callback: Function to call with SpaceStatus updates
        """
        self._callback = callback
    
    def start(self) -> None:
        """
        Start the background monitoring thread.
        
        The thread is started as a daemon so it won't prevent program exit.
        However, stop() should still be called for clean shutdown.
        """
        if self._thread is not None and self._thread.is_alive():
            logger.warning("Monitor already running")
            return
        
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._monitor_loop,
            name="DiskSpaceMonitor",
            daemon=True
        )
        self._thread.start()
        logger.info("Disk space monitor started")
    
    def stop(self) -> None:
        """
        Stop the background monitoring thread.
        
        This should be called in a finally block to ensure clean shutdown
        and prevent zombie threads.
        """
        if self._thread is None:
            return
        
        self._stop_event.set()
        
        # Wait for thread to finish with timeout
        self._thread.join(timeout=5.0)
        
        if self._thread.is_alive():
            logger.warning("Monitor thread did not stop cleanly")
        else:
            logger.info("Disk space monitor stopped")
        
        self._thread = None
    
    def _monitor_loop(self) -> None:
        """
        Main monitoring loop running in background thread.
        
        Periodically checks disk space and invokes callback on changes.
        Dynamically adjusts check interval based on space level.
        """
        logger.debug("Monitor loop started")
        
        while not self._stop_event.is_set():
            try:
                # Get current status
                status = self._check_disk_space()
                
                # Update cached status
                with self._status_lock:
                    self._current_status = status
                
                # Invoke callback if set
                if self._callback is not None:
                    try:
                        self._callback(status)
                    except Exception as e:
                        logger.error(f"Error in space update callback: {e}")
                
                # Determine sleep interval based on current level
                if status.level in (SpaceLevel.CRITICAL, SpaceLevel.PAUSED):
                    interval = self.critical_interval
                else:
                    interval = self.check_interval
                
            except Exception as e:
                logger.error(f"Error checking disk space: {e}")
                interval = self.check_interval
            
            # Wait for next check or stop signal
            self._stop_event.wait(timeout=interval)
        
        logger.debug("Monitor loop exited")
    
    def _check_disk_space(self) -> SpaceStatus:
        """
        Check disk space for both directories and return status.
        
        Returns:
            SpaceStatus with current disk space information
        """
        temp_free_bytes, temp_free_percent = self._get_disk_space(self.temp_dir)
        output_free_bytes, output_free_percent = self._get_disk_space(self.output_dir)
        
        # Determine level based on minimum of both directories
        min_percent = min(temp_free_percent, output_free_percent)
        min_bytes = min(temp_free_bytes, output_free_bytes)
        level = self._determine_level(min_percent, min_bytes)
        
        return SpaceStatus(
            temp_dir_free_bytes=temp_free_bytes,
            temp_dir_free_percent=temp_free_percent,
            output_dir_free_bytes=output_free_bytes,
            output_dir_free_percent=output_free_percent,
            level=level,
            timestamp=time.time()
        )
    
    def _get_disk_space(self, path: Path) -> tuple[int, float]:
        """
        Get free disk space for a path.
        
        Args:
            path: Path to check
            
        Returns:
            Tuple of (free_bytes, free_percent)
        """
        try:
            # Ensure path exists for disk_usage to work
            if not path.exists():
                # Try to get space from parent directory
                check_path = path
                while not check_path.exists() and check_path.parent != check_path:
                    check_path = check_path.parent
                
                if not check_path.exists():
                    logger.warning(f"Cannot determine disk space for {path}, using defaults")
                    return (100 * 1024 * 1024 * 1024, 1.0)  # Assume plenty of space
                
                path = check_path
            
            usage = shutil.disk_usage(path)
            free_bytes = usage.free
            free_percent = usage.free / usage.total if usage.total > 0 else 1.0
            
            return (free_bytes, free_percent)
            
        except Exception as e:
            logger.warning(f"Error getting disk space for {path}: {e}")
            # Return "plenty of space" to avoid false positives
            return (100 * 1024 * 1024 * 1024, 1.0)
    
    def _determine_level(self, free_percent: float, free_bytes: int) -> SpaceLevel:
        """
        Determine the space level based on percentage and absolute bytes.
        
        Uses max(percentage_threshold, absolute_bytes_threshold) logic:
        - Space is considered sufficient if it exceeds EITHER threshold
        
        Args:
            free_percent: Free space as a fraction (0.0 to 1.0)
            free_bytes: Free space in bytes
            
        Returns:
            The appropriate SpaceLevel
        """
        # Check from most severe to least severe
        # PAUSED: below minimum threshold (both percentage AND bytes)
        if free_percent < self.minimum_threshold and free_bytes < self.minimum_bytes:
            return SpaceLevel.PAUSED
        
        # CRITICAL: below critical threshold (both percentage AND bytes)
        if free_percent < self.critical_threshold and free_bytes < self.critical_min_bytes:
            return SpaceLevel.CRITICAL
        
        # WARNING: below warning threshold (both percentage AND bytes)
        if free_percent < self.warning_threshold and free_bytes < self.warning_min_bytes:
            return SpaceLevel.WARNING
        
        return SpaceLevel.NORMAL
    
    def get_current_status(self) -> SpaceStatus:
        """
        Get the current disk space status.
        
        This returns the cached status from the last check, or performs
        an immediate check if no cached status is available.
        
        Returns:
            Current SpaceStatus
        """
        with self._status_lock:
            if self._current_status is not None:
                return self._current_status
        
        # No cached status, perform immediate check
        return self._check_disk_space()
    
    def is_running(self) -> bool:
        """
        Check if the monitor is currently running.
        
        Returns:
            True if monitor thread is running
        """
        return self._thread is not None and self._thread.is_alive()
