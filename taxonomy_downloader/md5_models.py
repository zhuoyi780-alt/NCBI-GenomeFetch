"""
Data models for MD5 verification functionality.

This module defines the core data structures used throughout the MD5 verification
feature, including directory mode detection, verification status tracking, and
result aggregation.
"""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional, List


class DirectoryMode(Enum):
    """Directory structure mode for MD5 verification.
    
    Attributes:
        TAXON: Hierarchical structure with subdirectories (md5sum.txt in subdirs)
        ACCESSION: Flat structure with root md5sum.txt file
    """
    TAXON = "taxon"
    ACCESSION = "accession"


class VerificationStatus(Enum):
    """Status of file verification.
    
    Attributes:
        PASS: Computed hash matches expected hash
        FAIL: Computed hash does not match expected hash
        MISSING: Referenced file does not exist
        ERROR: Error occurred during verification (e.g., permission denied, I/O error)
    """
    PASS = "pass"
    FAIL = "fail"
    MISSING = "missing"
    ERROR = "error"


@dataclass
class MD5Entry:
    """Single entry from md5sum.txt file.
    
    Attributes:
        hash_value: Expected MD5 hash (32 hexadecimal characters)
        file_path: Relative path from md5sum.txt location
        absolute_path: Resolved absolute path to the file
    
    Examples:
        >>> entry = MD5Entry(
        ...     hash_value="d41d8cd98f00b204e9800998ecf8427e",
        ...     file_path="genome.fna",
        ...     absolute_path=Path("/data/genome.fna")
        ... )
        >>> entry.hash_value
        'd41d8cd98f00b204e9800998ecf8427e'
    """
    hash_value: str
    file_path: str
    absolute_path: Path


@dataclass
class FileVerificationResult:
    """Result of verifying a single file.
    
    Attributes:
        file_path: Relative path for display in reports
        expected_hash: Expected MD5 hash from md5sum.txt
        computed_hash: Computed MD5 hash (None if file missing or error occurred)
        status: Verification status (PASS, FAIL, MISSING, or ERROR)
        error_message: Error details if status is ERROR (None otherwise)
    
    Examples:
        >>> result = FileVerificationResult(
        ...     file_path="genome.fna",
        ...     expected_hash="d41d8cd98f00b204e9800998ecf8427e",
        ...     computed_hash="d41d8cd98f00b204e9800998ecf8427e",
        ...     status=VerificationStatus.PASS
        ... )
        >>> result.status == VerificationStatus.PASS
        True
    """
    file_path: str
    expected_hash: str
    computed_hash: Optional[str]
    status: VerificationStatus
    error_message: Optional[str] = None


@dataclass
class VerificationResult:
    """Overall verification results for all files.
    
    Attributes:
        total_files: Total number of files to verify
        passed: Number of files that passed verification
        failed: Number of files with hash mismatch
        missing: Number of files not found
        errors: Number of files with errors during verification
        file_results: List of individual file verification results
        processing_time: Total time taken for verification in seconds
    
    Examples:
        >>> result = VerificationResult(
        ...     total_files=10,
        ...     passed=8,
        ...     failed=1,
        ...     missing=1,
        ...     errors=0,
        ...     file_results=[],
        ...     processing_time=5.2
        ... )
        >>> result.total_files == result.passed + result.failed + result.missing + result.errors
        True
    """
    total_files: int
    passed: int
    failed: int
    missing: int
    errors: int
    file_results: List[FileVerificationResult] = field(default_factory=list)
    processing_time: float = 0.0
    
    def is_success(self) -> bool:
        """
        Check if verification was completely successful.
        
        Returns:
            True if all files passed, False if any failed, missing, or had errors
        
        Examples:
            >>> result = VerificationResult(10, 10, 0, 0, 0, [], 5.0)
            >>> result.is_success()
            True
            >>> result = VerificationResult(10, 9, 1, 0, 0, [], 5.0)
            >>> result.is_success()
            False
        """
        return self.failed == 0 and self.missing == 0 and self.errors == 0
    
    def get_exit_code(self) -> int:
        """
        Get appropriate exit code based on verification results.
        
        Returns:
            0 if all files passed, non-zero otherwise
        
        Examples:
            >>> result = VerificationResult(10, 10, 0, 0, 0, [], 5.0)
            >>> result.get_exit_code()
            0
            >>> result = VerificationResult(10, 9, 1, 0, 0, [], 5.0)
            >>> result.get_exit_code()
            1
        """
        return 0 if self.is_success() else 1
    
    def validate_counts(self) -> bool:
        """
        Validate that summary counts match the total.
        
        This property ensures data integrity by verifying that the sum of
        passed, failed, missing, and error counts equals the total file count.
        
        Returns:
            True if counts are consistent, False otherwise
        
        Examples:
            >>> result = VerificationResult(10, 8, 1, 1, 0, [], 5.0)
            >>> result.validate_counts()
            True
            >>> result = VerificationResult(10, 8, 1, 0, 0, [], 5.0)
            >>> result.validate_counts()
            False
        """
        return self.total_files == (self.passed + self.failed + self.missing + self.errors)
