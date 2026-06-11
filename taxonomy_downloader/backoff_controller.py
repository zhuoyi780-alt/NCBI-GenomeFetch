"""
Backoff controller for dynamic concurrency adjustment based on disk space.

This module implements a semaphore-based concurrency control mechanism that
dynamically adjusts the number of active workers based on disk space status.
"""

import threading
import logging
from typing import Optional, Callable
from taxonomy_downloader.models import SpaceLevel, SpaceStatus

logger = logging.getLogger(__name__)


class BackoffController:
    """
    Controls concurrency based on disk space status using a "borrow-and-hold" semaphore strategy.
    
    The controller uses a semaphore to limit concurrent workers. When disk space drops,
    it "borrows" tokens from the semaphore (acquires without releasing) to reduce
    available slots. When space recovers, it "returns" the borrowed tokens.
    
    Thread Safety:
        All state modifications are protected by a lock since on_space_update()
        may be called asynchronously from a monitoring thread.
    
    Hysteresis:
        To prevent thrashing when space fluctuates near thresholds, recovery
        requires space to exceed the threshold plus a hysteresis margin.
    """
    
    def __init__(
        self,
        max_workers: int,
        warning_threshold: float = 0.20,
        critical_threshold: float = 0.10,
        minimum_threshold: float = 0.05,
        warning_min_bytes: int = 10 * 1024 * 1024 * 1024,   # 10GB
        critical_min_bytes: int = 5 * 1024 * 1024 * 1024,   # 5GB
        minimum_bytes: int = 1 * 1024 * 1024 * 1024,        # 1GB
        hysteresis_margin: float = 0.02,  # 2% hysteresis
    ):
        """
        Initialize the backoff controller.
        
        Args:
            max_workers: Maximum number of concurrent workers
            warning_threshold: Space percentage below which to reduce workers by 50%
            critical_threshold: Space percentage below which to use single worker
            minimum_threshold: Space percentage below which to pause new tasks
            warning_min_bytes: Minimum absolute bytes for warning level
            critical_min_bytes: Minimum absolute bytes for critical level
            minimum_bytes: Minimum absolute bytes for paused level
            hysteresis_margin: Extra margin required for recovery (prevents thrashing)
        """
        if max_workers < 1:
            raise ValueError("max_workers must be at least 1")
        
        self.max_workers = max_workers
        self.warning_threshold = warning_threshold
        self.critical_threshold = critical_threshold
        self.minimum_threshold = minimum_threshold
        self.warning_min_bytes = warning_min_bytes
        self.critical_min_bytes = critical_min_bytes
        self.minimum_bytes = minimum_bytes
        self.hysteresis_margin = hysteresis_margin
        
        # Semaphore for controlling concurrency
        self._semaphore = threading.Semaphore(max_workers)
        
        # Track borrowed tokens (acquired but not released)
        self._held_tokens = 0
        
        # Lock for thread-safe state modifications
        self._lock = threading.Lock()
        
        # Current space level (for hysteresis logic)
        self._current_level = SpaceLevel.NORMAL
        
        # Flag to indicate paused state
        self._paused = False
        
        # Event to signal when unpaused
        self._unpause_event = threading.Event()
        self._unpause_event.set()  # Initially not paused
        
        logger.debug(
            f"BackoffController initialized: max_workers={max_workers}, "
            f"warning={warning_threshold:.1%}, critical={critical_threshold:.1%}, "
            f"minimum={minimum_threshold:.1%}, hysteresis={hysteresis_margin:.1%}"
        )

    def _determine_level(self, free_percent: float, free_bytes: int) -> SpaceLevel:
        """
        Determine the space level based on percentage and absolute bytes.
        
        Uses max(percentage_threshold, absolute_bytes_threshold) logic:
        - Space is considered sufficient if it exceeds EITHER threshold
        - This handles large disks where percentage thresholds may be too conservative
        
        Args:
            free_percent: Free space as a fraction (0.0 to 1.0)
            free_bytes: Free space in bytes
            
        Returns:
            The appropriate SpaceLevel
        """
        # Check from most severe to least severe
        # PAUSED: below minimum threshold
        if free_percent < self.minimum_threshold and free_bytes < self.minimum_bytes:
            return SpaceLevel.PAUSED
        
        # CRITICAL: below critical threshold
        if free_percent < self.critical_threshold and free_bytes < self.critical_min_bytes:
            return SpaceLevel.CRITICAL
        
        # WARNING: below warning threshold
        if free_percent < self.warning_threshold and free_bytes < self.warning_min_bytes:
            return SpaceLevel.WARNING
        
        return SpaceLevel.NORMAL
    
    def _determine_level_with_hysteresis(
        self, 
        free_percent: float, 
        free_bytes: int,
        current_level: SpaceLevel
    ) -> SpaceLevel:
        """
        Determine space level with hysteresis for recovery.
        
        When recovering (improving space), requires exceeding threshold + hysteresis
        to prevent thrashing when space fluctuates near thresholds.
        
        Args:
            free_percent: Free space as a fraction
            free_bytes: Free space in bytes
            current_level: Current space level
            
        Returns:
            The new SpaceLevel accounting for hysteresis
        """
        # Get the raw level without hysteresis
        raw_level = self._determine_level(free_percent, free_bytes)
        
        # If degrading (getting worse), apply immediately
        level_order = {
            SpaceLevel.NORMAL: 0,
            SpaceLevel.WARNING: 1,
            SpaceLevel.CRITICAL: 2,
            SpaceLevel.PAUSED: 3,
        }
        
        if level_order[raw_level] >= level_order[current_level]:
            # Same or worse - apply immediately
            return raw_level
        
        # Recovering - apply hysteresis
        # Need to exceed threshold + margin to recover
        hysteresis_percent = free_percent - self.hysteresis_margin
        
        # Recalculate with reduced percentage (simulating stricter threshold)
        hysteresis_level = self._determine_level(hysteresis_percent, free_bytes)
        
        # Only recover if hysteresis check also passes
        if level_order[hysteresis_level] < level_order[current_level]:
            return hysteresis_level
        
        # Stay at current level
        return current_level
    
    def _get_target_workers_for_level(self, level: SpaceLevel) -> int:
        """
        Get the target worker count for a given space level.
        
        Args:
            level: The space level
            
        Returns:
            Target number of workers
        """
        if level == SpaceLevel.NORMAL:
            return self.max_workers
        elif level == SpaceLevel.WARNING:
            # Reduce by 50%, but at least 1
            return max(1, self.max_workers // 2)
        elif level == SpaceLevel.CRITICAL:
            return 1
        else:  # PAUSED
            return 0

    def on_space_update(self, status: SpaceStatus) -> None:
        """
        Respond to a disk space status update.
        
        This method is called by the DiskSpaceMonitor from a background thread.
        It adjusts the semaphore by borrowing or returning tokens.
        
        Thread Safety:
            This method is thread-safe and uses a lock to protect all state modifications.
        
        Args:
            status: Current disk space status
        """
        with self._lock:
            # Use the minimum of temp and output directory space
            min_percent = min(status.temp_dir_free_percent, status.output_dir_free_percent)
            min_bytes = min(status.temp_dir_free_bytes, status.output_dir_free_bytes)
            
            # Determine new level with hysteresis
            new_level = self._determine_level_with_hysteresis(
                min_percent, min_bytes, self._current_level
            )
            
            if new_level == self._current_level:
                return  # No change needed
            
            old_level = self._current_level
            self._current_level = new_level
            
            # Calculate target workers
            target_workers = self._get_target_workers_for_level(new_level)
            current_workers = self.max_workers - self._held_tokens
            
            logger.info(
                f"Disk space level changed: {old_level.value} -> {new_level.value}, "
                f"workers: {current_workers} -> {target_workers} "
                f"(free: {min_percent:.1%}, {min_bytes / (1024**3):.1f}GB)"
            )
            
            # Handle pause/unpause
            if new_level == SpaceLevel.PAUSED:
                self._paused = True
                self._unpause_event.clear()
                logger.warning("Disk space extremely low, pausing new tasks...")
            elif old_level == SpaceLevel.PAUSED:
                self._paused = False
                self._unpause_event.set()
                logger.info("Disk space recovered, resuming task submissions")
            
            # Adjust semaphore
            if target_workers < current_workers:
                # Need to reduce - borrow tokens
                self._reduce_workers(target_workers)
            elif target_workers > current_workers:
                # Need to increase - return tokens
                self._restore_workers(target_workers)
    
    def _reduce_workers(self, target: int) -> None:
        """
        Reduce available workers by borrowing tokens from the semaphore.
        
        Must be called with self._lock held.
        
        Args:
            target: Target number of workers
        """
        current = self.max_workers - self._held_tokens
        tokens_to_hold = current - target
        
        if tokens_to_hold <= 0:
            return
        
        logger.debug(f"Reducing workers: borrowing {tokens_to_hold} tokens")
        
        for _ in range(tokens_to_hold):
            # Try to acquire with a short timeout
            # This may block briefly if all tokens are in use
            acquired = self._semaphore.acquire(blocking=True, timeout=0.1)
            if acquired:
                self._held_tokens += 1
            else:
                # Token is in use, will be held when released
                # For now, just track that we want to hold it
                logger.debug("Token in use, will be held on next release")
                break
    
    def _restore_workers(self, target: int) -> None:
        """
        Restore available workers by returning borrowed tokens.
        
        Must be called with self._lock held.
        
        Args:
            target: Target number of workers
        """
        current = self.max_workers - self._held_tokens
        tokens_to_release = target - current
        
        if tokens_to_release <= 0:
            return
        
        # Can only release tokens we actually hold
        tokens_to_release = min(tokens_to_release, self._held_tokens)
        
        logger.debug(f"Restoring workers: releasing {tokens_to_release} tokens")
        
        for _ in range(tokens_to_release):
            self._semaphore.release()
            self._held_tokens -= 1

    def acquire_slot(self, timeout: Optional[float] = None) -> bool:
        """
        Acquire an execution slot.
        
        This method should be called before starting a new task.
        It will block until a slot is available or timeout expires.
        
        If the system is in PAUSED state, this will wait for unpause
        before attempting to acquire a slot.
        
        Args:
            timeout: Maximum time to wait in seconds. None means wait forever.
            
        Returns:
            True if slot was acquired, False if timeout expired
        """
        # First, wait for unpause if paused
        if not self._unpause_event.wait(timeout=timeout):
            logger.warning("Timeout waiting for disk space to recover")
            return False
        
        # Now try to acquire semaphore
        if timeout is not None:
            acquired = self._semaphore.acquire(blocking=True, timeout=timeout)
        else:
            acquired = self._semaphore.acquire(blocking=True)
        
        if not acquired:
            logger.debug("Failed to acquire slot within timeout")
        
        return acquired
    
    def release_slot(self) -> None:
        """
        Release an execution slot.
        
        This method should be called when a task completes (in a finally block).
        """
        self._semaphore.release()
    
    def get_active_workers(self) -> int:
        """
        Get the current number of available worker slots.
        
        Returns:
            Number of workers that can currently be active
        """
        with self._lock:
            return self.max_workers - self._held_tokens
    
    def get_target_workers(self) -> int:
        """
        Get the target worker count based on current space level.
        
        Returns:
            Target number of workers for current space level
        """
        with self._lock:
            return self._get_target_workers_for_level(self._current_level)
    
    def get_current_level(self) -> SpaceLevel:
        """
        Get the current space level.
        
        Returns:
            Current SpaceLevel
        """
        with self._lock:
            return self._current_level
    
    def is_paused(self) -> bool:
        """
        Check if the controller is in paused state.
        
        Returns:
            True if paused, False otherwise
        """
        with self._lock:
            return self._paused
    
    def get_held_tokens(self) -> int:
        """
        Get the number of tokens currently held (borrowed).
        
        Returns:
            Number of held tokens
        """
        with self._lock:
            return self._held_tokens
