"""
Exception classes for MD5 verification auto-fix functionality.

This module defines the exception hierarchy for the MD5 verification enhancement
feature. It distinguishes between recoverable errors (that allow processing to
continue with other files) and non-recoverable errors (that terminate the flow).
"""


class AutoFixError(Exception):
    """
    Base exception for MD5 auto-fix operations.
    
    This is the base class for all auto-fix related exceptions. It represents
    recoverable errors that should be logged but allow processing to continue
    with other files.
    
    Attributes:
        message: Human-readable error message
        context: Optional dictionary with additional error context
    
    Examples:
        >>> try:
        ...     raise AutoFixError("Failed to process file", {"file": "test.fna"})
        ... except AutoFixError as e:
        ...     print(e.message)
        ...     print(e.context)
        Failed to process file
        {'file': 'test.fna'}
    """
    
    def __init__(self, message: str, context: dict = None):
        """
        Initialize the AutoFixError.
        
        Args:
            message: Human-readable error message
            context: Optional dictionary with additional error context
        """
        self.message = message
        self.context = context or {}
        super().__init__(self.message)
    
    def __str__(self) -> str:
        """Return string representation of the error."""
        if self.context:
            context_str = ", ".join(f"{k}={v}" for k, v in self.context.items())
            return f"{self.message} ({context_str})"
        return self.message


class TempDirectoryError(AutoFixError):
    """
    Exception raised when temporary directory operations fail.
    
    This is a non-recoverable error that should terminate the auto-fix flow.
    If we cannot create or access the temporary directory, we cannot proceed
    with downloading files.
    
    According to requirement 8.4, this error should cause the flow to terminate
    and return an error code.
    
    Examples:
        >>> try:
        ...     raise TempDirectoryError("Cannot create temp directory", {"path": "/tmp/autofix"})
        ... except TempDirectoryError as e:
        ...     print(f"Fatal error: {e}")
        Fatal error: Cannot create temp directory (path=/tmp/autofix)
    """
    
    def __init__(self, message: str, context: dict = None):
        """
        Initialize the TempDirectoryError.
        
        Args:
            message: Human-readable error message
            context: Optional dictionary with additional error context
        """
        super().__init__(message, context)


class MD5FileError(AutoFixError):
    """
    Exception raised when MD5 file operations fail.
    
    This is a recoverable error. If we cannot read or parse an MD5 file for
    a specific file, we should log the error and continue processing other files.
    
    Examples:
        >>> try:
        ...     raise MD5FileError("Cannot parse md5sum.txt", {"file": "md5sum.txt", "line": 5})
        ... except MD5FileError as e:
        ...     print(f"MD5 file error: {e}")
        MD5 file error: Cannot parse md5sum.txt (file=md5sum.txt, line=5)
    """
    
    def __init__(self, message: str, context: dict = None):
        """
        Initialize the MD5FileError.
        
        Args:
            message: Human-readable error message
            context: Optional dictionary with additional error context
        """
        super().__init__(message, context)


class DownloaderError(AutoFixError):
    """
    Exception raised when file download operations fail.
    
    This is a recoverable error. If a specific file fails to download, we should
    log the error and continue processing other files. The failed file will be
    included in the "still failed" list in the final report.
    
    Examples:
        >>> try:
        ...     raise DownloaderError("Download failed", {"accession": "GCA_000302455.1", "reason": "timeout"})
        ... except DownloaderError as e:
        ...     print(f"Download error: {e}")
        Download error: Download failed (accession=GCA_000302455.1, reason=timeout)
    """
    
    def __init__(self, message: str, context: dict = None):
        """
        Initialize the DownloaderError.
        
        Args:
            message: Human-readable error message
            context: Optional dictionary with additional error context
        """
        super().__init__(message, context)


def is_fatal_error(error: Exception) -> bool:
    """
    Determine if an exception is fatal (should terminate the flow).
    
    According to the design document:
    - TempDirectoryError is non-recoverable (fatal)
    - Other AutoFixError subclasses are recoverable (non-fatal)
    
    Args:
        error: The exception to check
    
    Returns:
        True if the error is fatal and should terminate the flow, False otherwise
    
    Examples:
        >>> is_fatal_error(TempDirectoryError("Cannot create temp dir"))
        True
        >>> is_fatal_error(DownloaderError("Download failed"))
        False
        >>> is_fatal_error(MD5FileError("Cannot parse file"))
        False
        >>> is_fatal_error(ValueError("Invalid value"))
        False
    """
    return isinstance(error, TempDirectoryError)
