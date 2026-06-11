"""
Rate limiting functionality for NCBI API requests.
"""

import time
import threading
from typing import Optional
from .logging_config import get_logger


class RateLimiter:
    """
    Thread-safe rate limiter for API requests.
    
    Uses token bucket algorithm to ensure requests don't exceed specified rate.
    """
    
    def __init__(self, requests_per_second: float):
        """
        Initialize rate limiter.
        
        Args:
            requests_per_second: Maximum requests per second allowed
        """
        self.requests_per_second = requests_per_second
        self.interval = 1.0 / requests_per_second if requests_per_second > 0 else 0
        self.last_request_time = 0.0
        self.lock = threading.Lock()
        self.logger = get_logger("rate_limiter")
        
        self.logger.debug(f"Rate limiter initialized: {requests_per_second} requests/second")
    
    def acquire(self, timeout: Optional[float] = None) -> bool:
        """
        Acquire permission to make a request.
        
        This method blocks until it's safe to make a request according to the rate limit.
        
        Args:
            timeout: Maximum time to wait for permission (None for no timeout)
            
        Returns:
            True if permission acquired, False if timeout exceeded
        """
        start_time = time.time()
        
        with self.lock:
            current_time = time.time()
            
            # Calculate time since last request
            time_since_last = current_time - self.last_request_time
            
            # If we need to wait, calculate wait time
            if time_since_last < self.interval:
                wait_time = self.interval - time_since_last
                
                # Check timeout
                if timeout is not None and wait_time > timeout:
                    self.logger.warning(f"Rate limit timeout exceeded: {wait_time:.2f}s > {timeout:.2f}s")
                    return False
                
                self.logger.debug(f"Rate limiting: waiting {wait_time:.2f}s")
                time.sleep(wait_time)
                current_time = time.time()
            
            # Update last request time
            self.last_request_time = current_time
            
            elapsed = current_time - start_time
            if elapsed > 0.1:  # Log if we waited more than 100ms
                self.logger.debug(f"Rate limit acquired after {elapsed:.2f}s")
            
            return True
    
    def update_rate(self, requests_per_second: float) -> None:
        """
        Update the rate limit.
        
        Args:
            requests_per_second: New rate limit
        """
        with self.lock:
            self.requests_per_second = requests_per_second
            self.interval = 1.0 / requests_per_second if requests_per_second > 0 else 0
            self.logger.info(f"Rate limit updated to {requests_per_second} requests/second")


class RetryHandler:
    """
    Handles retry logic with exponential backoff for transient errors.
    """
    
    def __init__(self, max_retries: int = 3, base_delay: float = 1.0, max_delay: float = 60.0):
        """
        Initialize retry handler.
        
        Args:
            max_retries: Maximum number of retry attempts
            base_delay: Base delay in seconds for exponential backoff
            max_delay: Maximum delay between retries
        """
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.logger = get_logger("retry_handler")
    
    def should_retry(self, exception: Exception, attempt: int) -> bool:
        """
        Determine if an error should be retried.
        
        Args:
            exception: The exception that occurred
            attempt: Current attempt number (0-based)
            
        Returns:
            True if the error should be retried
        """
        if attempt >= self.max_retries:
            return False
        
        # Check for retryable error types
        error_message = str(exception).lower()
        
        # Network-related errors
        if any(keyword in error_message for keyword in [
            'timeout', 'connection', 'network', 'unreachable',
            'temporary failure', 'service unavailable', 'rate limit'
        ]):
            return True
        
        # HTTP status codes that should be retried
        if any(code in error_message for code in ['429', '502', '503', '504']):
            return True
        
        return False
    
    def get_delay(self, attempt: int) -> float:
        """
        Calculate delay for exponential backoff.
        
        Args:
            attempt: Current attempt number (0-based)
            
        Returns:
            Delay in seconds
        """
        delay = self.base_delay * (2 ** attempt)
        return min(delay, self.max_delay)
    
    def execute_with_retry(self, func, *args, **kwargs):
        """
        Execute a function with retry logic.
        
        Args:
            func: Function to execute
            *args: Positional arguments for the function
            **kwargs: Keyword arguments for the function
            
        Returns:
            Function result
            
        Raises:
            Last exception if all retries failed
        """
        last_exception = None
        
        for attempt in range(self.max_retries + 1):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                last_exception = e
                
                if not self.should_retry(e, attempt):
                    self.logger.debug(f"Not retrying error on attempt {attempt + 1}: {e}")
                    break
                
                if attempt < self.max_retries:
                    delay = self.get_delay(attempt)
                    self.logger.warning(f"Attempt {attempt + 1} failed: {e}. Retrying in {delay:.1f}s...")
                    time.sleep(delay)
                else:
                    self.logger.error(f"All {self.max_retries + 1} attempts failed")
        
        # Re-raise the last exception
        raise last_exception