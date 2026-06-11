"""
Error handling and classification for the taxonomy downloader.
"""

import json
import logging
import logging.handlers
import os
import time
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import asdict

from .models import ErrorType, TaxonResult, DownloadResults
from .logging_config import get_logger


class ErrorHandler:
    """
    Centralized error handling with classification and structured logging.
    
    Provides error classification, structured logging with context,
    log rotation, and comprehensive reporting capabilities.
    """
    
    def __init__(self, output_dir: str, max_log_size: int = 10 * 1024 * 1024, backup_count: int = 5):
        """
        Initialize the error handler.
        
        Args:
            output_dir: Directory where log files will be created
            max_log_size: Maximum size of log files before rotation (default: 10MB)
            backup_count: Number of backup log files to keep (default: 5)
        """
        self.output_dir = Path(output_dir)
        self.max_log_size = max_log_size
        self.backup_count = backup_count
        
        # Create logs directory
        self.log_dir = self.output_dir / "logs"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        
        # Initialize logger
        self.logger = get_logger("error_handler")
        
        # Set up structured error log
        self.error_log_file = self.log_dir / "structured_errors.jsonl"
        self._setup_structured_logging()
        
        # Track error statistics
        self.error_counts: Dict[ErrorType, int] = {error_type: 0 for error_type in ErrorType}
        self.total_errors = 0
        
        self.logger.info(f"ErrorHandler initialized with log directory: {self.log_dir}")
    
    def _setup_structured_logging(self) -> None:
        """Set up structured JSON logging for errors."""
        # Create a separate logger for structured errors
        self.structured_logger = logging.getLogger("taxonomy_downloader.structured_errors")
        self.structured_logger.setLevel(logging.ERROR)
        
        # Clear any existing handlers
        self.structured_logger.handlers.clear()
        
        # Create rotating file handler for structured errors
        structured_handler = logging.handlers.RotatingFileHandler(
            self.error_log_file,
            maxBytes=self.max_log_size,
            backupCount=self.backup_count
        )
        
        # Use a simple formatter since we'll format JSON ourselves
        structured_handler.setFormatter(logging.Formatter('%(message)s'))
        self.structured_logger.addHandler(structured_handler)
        
        # Prevent propagation to avoid duplicate logging
        self.structured_logger.propagate = False
    
    def log_error(self, taxon: str, error_type: ErrorType, message: str, 
                  exception: Optional[Exception] = None, context: Optional[Dict[str, Any]] = None) -> None:
        """
        Log an error with classification and structured data.
        
        Args:
            taxon: The taxon being processed when the error occurred
            error_type: Classification of the error type
            message: Human-readable error message
            exception: The original exception (if any)
            context: Additional context information
        """
        # Update error statistics
        self.error_counts[error_type] += 1
        self.total_errors += 1
        
        # Create structured error record
        error_record = {
            "timestamp": time.time(),
            "iso_timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
            "taxon": taxon,
            "error_type": error_type.value,
            "message": message,
            "context": context or {},
            "is_fatal": self.is_fatal_error_type(error_type),
            "should_retry": self.should_retry_error_type(error_type)
        }
        
        # Add exception details if provided
        if exception:
            error_record["exception"] = {
                "type": type(exception).__name__,
                "message": str(exception),
                "module": getattr(exception, '__module__', None)
            }
        
        # Log structured error
        self.structured_logger.error(json.dumps(error_record))
        
        # Log human-readable error
        log_message = f"[{error_type.value.upper()}] Taxon '{taxon}': {message}"
        if exception:
            log_message += f" (Exception: {type(exception).__name__}: {exception})"
        
        if self.is_fatal_error_type(error_type):
            self.logger.critical(log_message)
        else:
            self.logger.error(log_message)
        
        # Log context if provided
        if context:
            self.logger.debug(f"Error context for '{taxon}': {context}")
    
    def log_warning(self, message: str, taxon: Optional[str] = None, 
                   context: Optional[Dict[str, Any]] = None) -> None:
        """
        Log a warning message with optional context.
        
        Args:
            message: Warning message
            taxon: Optional taxon name for context
            context: Additional context information
        """
        log_message = message
        if taxon:
            log_message = f"Taxon '{taxon}': {message}"
        
        self.logger.warning(log_message)
        
        if context:
            self.logger.debug(f"Warning context: {context}")
    
    def is_fatal_error(self, error: Exception) -> bool:
        """
        Determine if an exception represents a fatal error.
        
        Args:
            error: The exception to classify
            
        Returns:
            True if the error is fatal (should stop all processing)
        """
        error_type = self.classify_exception(error)
        return self.is_fatal_error_type(error_type)
    
    def is_fatal_error_type(self, error_type: ErrorType) -> bool:
        """
        Determine if an error type is fatal.
        
        Args:
            error_type: The error type to check
            
        Returns:
            True if the error type is fatal
        """
        return error_type in {ErrorType.ENVIRONMENT, ErrorType.FILESYSTEM}
    
    def should_retry(self, error: Exception) -> bool:
        """
        Determine if an exception should trigger a retry.
        
        Args:
            error: The exception to classify
            
        Returns:
            True if the error is retryable
        """
        error_type = self.classify_exception(error)
        return self.should_retry_error_type(error_type)
    
    def should_retry_error_type(self, error_type: ErrorType) -> bool:
        """
        Determine if an error type should trigger a retry.
        
        Args:
            error_type: The error type to check
            
        Returns:
            True if the error type is retryable
        """
        return error_type == ErrorType.TRANSIENT
    
    def classify_exception(self, exception: Exception) -> ErrorType:
        """
        Classify an exception into an error type.
        
        Args:
            exception: The exception to classify
            
        Returns:
            The appropriate ErrorType for the exception
        """
        exception_type = type(exception).__name__
        error_message = str(exception).lower()
        
        # Environment errors (fatal)
        environment_indicators = [
            'command not found', 'executable not found', 'no such file or directory',
            'datasets not found', 'version incompatible', 'network unreachable',
            'connection refused', 'dns resolution failed', 'ssl certificate',
            'authentication failed', 'unauthorized'
        ]
        
        # Filesystem errors (fatal)
        filesystem_indicators = [
            'permission denied', 'access denied', 'disk full', 'no space left',
            'read-only file system', 'file exists', 'directory not empty',
            'operation not permitted', 'input/output error'
        ]
        
        # Transient errors (retryable)
        transient_indicators = [
            'timeout', 'connection timeout', 'read timeout', 'rate limit',
            'too many requests', 'service unavailable', 'bad gateway',
            'gateway timeout', 'temporary failure', 'try again later',
            'server error', 'internal server error'
        ]
        
        # Data errors (non-fatal, continue processing)
        data_indicators = [
            'not found', 'no data available', 'invalid taxon', 'no genomes',
            'empty result', 'no assemblies', 'taxon id not found',
            'invalid taxonomy', 'no sequences found'
        ]
        
        # Check for specific exception types
        if exception_type in ['FileNotFoundError', 'PermissionError', 'OSError']:
            if 'permission' in error_message or 'access' in error_message:
                return ErrorType.FILESYSTEM
            elif 'not found' in error_message and 'datasets' in error_message:
                return ErrorType.ENVIRONMENT
            elif 'space' in error_message or 'disk' in error_message:
                return ErrorType.FILESYSTEM
            else:
                return ErrorType.PROCESSING
        
        elif exception_type in ['ConnectionError', 'TimeoutError', 'URLError']:
            return ErrorType.TRANSIENT
        
        elif exception_type in ['ValueError', 'KeyError', 'IndexError']:
            return ErrorType.DATA
        
        # Check error message content
        for indicator in environment_indicators:
            if indicator in error_message:
                return ErrorType.ENVIRONMENT
        
        for indicator in filesystem_indicators:
            if indicator in error_message:
                return ErrorType.FILESYSTEM
        
        for indicator in transient_indicators:
            if indicator in error_message:
                return ErrorType.TRANSIENT
        
        for indicator in data_indicators:
            if indicator in error_message:
                return ErrorType.DATA
        
        # Default to processing error for unclassified exceptions
        return ErrorType.PROCESSING
    
    def generate_summary_report(self, results: DownloadResults) -> str:
        """
        Generate a comprehensive summary report of the download session.
        
        Args:
            results: The download results to summarize
            
        Returns:
            Formatted summary report string
        """
        report_lines = []
        report_lines.append("=" * 60)
        report_lines.append("TAXONOMY GENOME DOWNLOADER - SUMMARY REPORT")
        report_lines.append("=" * 60)
        
        # Basic statistics
        report_lines.append(f"Total Taxa Processed: {results.total_taxa}")
        report_lines.append(f"Successful Downloads: {results.successful}")
        report_lines.append(f"Failed Downloads: {results.failed}")
        
        # Calculate success rate only if there are taxa to process
        if results.total_taxa > 0:
            success_rate = (results.successful / results.total_taxa * 100)
            report_lines.append(f"Success Rate: {success_rate:.1f}%")
        else:
            report_lines.append("Success Rate: N/A (no taxa processed)")
        
        report_lines.append(f"Total Files Downloaded: {results.total_files}")
        report_lines.append(f"Total Processing Time: {results.processing_time:.2f} seconds")
        
        if results.total_taxa > 0:
            avg_time = results.processing_time / results.total_taxa
            report_lines.append(f"Average Time per Taxon: {avg_time:.2f} seconds")
        
        report_lines.append("")
        
        # Error statistics
        if self.total_errors > 0:
            report_lines.append("ERROR STATISTICS:")
            report_lines.append("-" * 20)
            report_lines.append(f"Total Errors: {self.total_errors}")
            
            for error_type, count in self.error_counts.items():
                if count > 0:
                    percentage = (count / self.total_errors * 100)
                    fatal_indicator = " (FATAL)" if self.is_fatal_error_type(error_type) else ""
                    retry_indicator = " (RETRYABLE)" if self.should_retry_error_type(error_type) else ""
                    report_lines.append(f"  {error_type.value.title()}: {count} ({percentage:.1f}%){fatal_indicator}{retry_indicator}")
            
            report_lines.append("")
        
        # Failed taxa details
        if results.failed_taxa:
            report_lines.append("FAILED TAXA:")
            report_lines.append("-" * 15)
            
            # Group by error type
            failed_by_type: Dict[ErrorType, List[TaxonResult]] = {}
            for failed_taxon in results.failed_taxa:
                error_type = failed_taxon.error_type or ErrorType.PROCESSING
                if error_type not in failed_by_type:
                    failed_by_type[error_type] = []
                failed_by_type[error_type].append(failed_taxon)
            
            for error_type, taxa in failed_by_type.items():
                report_lines.append(f"\n{error_type.value.title()} Errors ({len(taxa)} taxa):")
                for taxon_result in taxa[:10]:  # Limit to first 10 per type
                    report_lines.append(f"  - {taxon_result.taxon}: {taxon_result.error_message}")
                
                if len(taxa) > 10:
                    report_lines.append(f"  ... and {len(taxa) - 10} more")
        
        report_lines.append("")
        report_lines.append("Log files location: " + str(self.log_dir))
        report_lines.append("Structured error log: " + str(self.error_log_file))
        report_lines.append("=" * 60)
        
        return "\n".join(report_lines)
    
    def _rotate_log_if_needed(self) -> None:
        """
        Check if log rotation is needed and perform it if necessary.
        
        This method is called automatically by the RotatingFileHandler,
        but can be called manually if needed.
        """
        try:
            # Check structured error log size
            if self.error_log_file.exists():
                file_size = self.error_log_file.stat().st_size
                if file_size > self.max_log_size:
                    self.logger.info(f"Structured error log size ({file_size} bytes) exceeds limit, rotation will occur on next write")
            
            # The actual rotation is handled by RotatingFileHandler automatically
            
        except Exception as e:
            self.logger.warning(f"Failed to check log rotation status: {e}")
    
    def get_error_statistics(self) -> Dict[str, Any]:
        """
        Get current error statistics.
        
        Returns:
            Dictionary containing error statistics
        """
        return {
            "total_errors": self.total_errors,
            "error_counts": {error_type.value: count for error_type, count in self.error_counts.items()},
            "log_directory": str(self.log_dir),
            "structured_log_file": str(self.error_log_file)
        }
    
    def close(self) -> None:
        """
        Close all logging handlers and release file handles.
        
        This method should be called when the ErrorHandler is no longer needed,
        especially important on Windows to prevent file locking issues.
        """
        try:
            # Close structured logger handlers
            if hasattr(self, 'structured_logger'):
                for handler in self.structured_logger.handlers[:]:
                    handler.close()
                    self.structured_logger.removeHandler(handler)
            
            # Close main logger handlers that we own
            if hasattr(self, 'logger'):
                for handler in self.logger.handlers[:]:
                    if hasattr(handler, 'close'):
                        handler.close()
        except Exception:
            pass  # Ignore cleanup errors
    
    def __enter__(self):
        """Context manager entry."""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - ensures cleanup."""
        self.close()
        return False
    
    def export_errors_to_json(self, output_file: Optional[str] = None) -> str:
        """
        Export all structured errors to a JSON file.
        
        Args:
            output_file: Optional output file path. If None, uses default location.
            
        Returns:
            Path to the exported file
        """
        if output_file is None:
            output_file = self.log_dir / "error_export.json"
        else:
            output_file = Path(output_file)
        
        errors = []
        
        try:
            # Read structured error log
            if self.error_log_file.exists():
                with open(self.error_log_file, 'r') as f:
                    for line in f:
                        try:
                            error_record = json.loads(line.strip())
                            errors.append(error_record)
                        except json.JSONDecodeError:
                            continue
            
            # Export to JSON
            export_data = {
                "export_timestamp": time.time(),
                "export_iso_timestamp": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
                "total_errors": len(errors),
                "statistics": self.get_error_statistics(),
                "errors": errors
            }
            
            with open(output_file, 'w') as f:
                json.dump(export_data, f, indent=2)
            
            self.logger.info(f"Exported {len(errors)} errors to {output_file}")
            return str(output_file)
            
        except Exception as e:
            self.logger.error(f"Failed to export errors: {e}")
            raise