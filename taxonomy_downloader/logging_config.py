"""
Logging configuration for the taxonomy downloader.
"""

import logging
import logging.handlers
import sys
from pathlib import Path
from typing import Optional

# Import version from package
try:
    from . import __version__
except ImportError:
    __version__ = "1.1.3"  # Fallback version


class ConsoleOutputManager:
    """
    Manages console output separately from logging.
    Provides clean, user-friendly output to terminal while detailed logs go to files.
    """
    
    _instance: Optional['ConsoleOutputManager'] = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        self._initialized = True
        self._verbose = False
    
    def set_verbose(self, verbose: bool) -> None:
        """Set verbose mode for additional console output."""
        self._verbose = verbose
    
    def print_header(self, title: str, version: str = None) -> None:
        """Print application header."""
        if version is None:
            version = __version__
        print(f"{title} v{version}")
        print("=" * 50)
    
    def print_config(self, **kwargs) -> None:
        """Print configuration parameters."""
        for key, value in kwargs.items():
            print(f"  {key}: {value}")
    
    def print_separator(self) -> None:
        """Print a separator line."""
        print("=" * 50)
    
    def print_info(self, message: str) -> None:
        """Print an info message."""
        print(message)
    
    def print_progress(self, completed: int, failed: int, total: int, 
                       elapsed: str, eta: str = "", disk_status: str = "") -> None:
        """Print progress update."""
        progress_percent = (completed + failed) / total * 100 if total > 0 else 0
        eta_str = f", ETA: {eta}" if eta else ""
        print(f"Progress: {completed + failed}/{total} ({progress_percent:.1f}%) - "
              f"Success: {completed}, Failed: {failed}, "
              f"Elapsed: {elapsed}{eta_str}{disk_status}")
    
    def print_success(self, message: str) -> None:
        """Print a success message."""
        print(message)
    
    def print_warning(self, message: str) -> None:
        """Print a warning message."""
        print(f"Warning: {message}")
    
    def print_error(self, message: str) -> None:
        """Print an error message."""
        print(f"Error: {message}")
    
    def print_summary(self, results: dict) -> None:
        """Print final summary."""
        print("\n" + "=" * 60)
        print("DOWNLOAD SUMMARY")
        print("=" * 60)
        for key, value in results.items():
            print(f"{key}: {value}")
        print("=" * 60)


def get_console() -> ConsoleOutputManager:
    """Get the singleton console output manager."""
    return ConsoleOutputManager()


def setup_logging(
    output_dir: str,
    log_level: str = "INFO",
    max_log_size: int = 10 * 1024 * 1024,  # 10MB
    backup_count: int = 5,
    console_quiet: bool = False
) -> logging.Logger:
    """
    Set up comprehensive logging for the application.
    
    Console output is now handled separately by ConsoleOutputManager.
    This function only configures file-based logging.
    
    Args:
        output_dir: Directory where log files will be created
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR)
        max_log_size: Maximum size of log files before rotation
        backup_count: Number of backup log files to keep
        console_quiet: If True, reduce console output to minimum (passed to ConsoleOutputManager)
    
    Returns:
        Configured logger instance
    """
    # Create logs directory
    log_dir = Path(output_dir) / "logs"
    log_dir.mkdir(exist_ok=True)
    
    # Configure root logger
    logger = logging.getLogger("taxonomy_downloader")
    logger.setLevel(getattr(logging, log_level.upper()))
    
    # Clear any existing handlers
    logger.handlers.clear()
    
    # Allow propagation so test harnesses and embedding applications can capture logs.
    # Console output is managed separately, so the package itself does not attach
    # console handlers here.
    logger.propagate = True
    
    # Create formatters
    detailed_formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s'
    )
    
    # Main log file handler with rotation
    main_log_file = log_dir / "download.log"
    main_handler = logging.handlers.RotatingFileHandler(
        main_log_file,
        maxBytes=max_log_size,
        backupCount=backup_count
    )
    main_handler.setLevel(logging.DEBUG)
    main_handler.setFormatter(detailed_formatter)
    logger.addHandler(main_handler)
    
    # Error log file handler
    error_log_file = log_dir / "error.log"
    error_handler = logging.handlers.RotatingFileHandler(
        error_log_file,
        maxBytes=max_log_size,
        backupCount=backup_count
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.setFormatter(detailed_formatter)
    logger.addHandler(error_handler)
    
    # Log startup information (only to files)
    logger.info("Logging system initialized")
    logger.info(f"Log files location: {log_dir}")
    logger.info(f"Log level: {log_level}")
    
    return logger


def get_logger(name: Optional[str] = None) -> logging.Logger:
    """Get a logger instance for a specific module."""
    if name:
        return logging.getLogger(f"taxonomy_downloader.{name}")
    return logging.getLogger("taxonomy_downloader")
