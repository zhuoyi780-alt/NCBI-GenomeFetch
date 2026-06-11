"""
Data models for MD5 verification auto-fix functionality.

This module defines the data structures used for the MD5 verification enhancement
feature, which automatically redownloads and organizes files that fail MD5 verification.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, List
from enum import Enum

from taxonomy_downloader.md5_models import VerificationStatus


class FileVerificationStatus(Enum):
    """Extended verification status for auto-fix workflow.
    
    Attributes:
        PASS: File passed MD5 verification
        FAIL: File failed MD5 verification (hash mismatch)
        MISSING: File is missing from filesystem
        ERROR: Error occurred during verification
        REDOWNLOADED: File was successfully redownloaded
        FIXED: File was successfully fixed (redownloaded and verified)
        STILL_FAILED: File still fails after redownload attempt
        SKIPPED: File was skipped (e.g., no valid Accession ID)
    """
    PASS = "pass"
    FAIL = "fail"
    MISSING = "missing"
    ERROR = "error"
    REDOWNLOADED = "redownloaded"
    FIXED = "fixed"
    STILL_FAILED = "still_failed"
    SKIPPED = "skipped"


@dataclass
class FailedFile:
    """Information about a file that failed MD5 verification.
    
    This class stores all necessary information to redownload and organize
    a failed file, including the md5sum.txt directory context to avoid
    global file searches that could cause same-name file conflicts.
    
    Attributes:
        md5_file_path: Path recorded in md5sum.txt (simplified filename only)
        md5sum_dir: Directory containing the md5sum.txt file (relative to verification_dir)
        original_path: Complete relative path in filesystem (md5sum_dir / md5_file_path)
        status: Verification status (FAIL, MISSING, or ERROR)
        expected_hash: Expected MD5 hash value from md5sum.txt
        accession_id: Extracted NCBI Accession ID (if successfully extracted)
        error_message: Error details if status is ERROR
    
    Examples:
        >>> failed = FailedFile(
        ...     md5_file_path="GCA_000302455.1.fna",
        ...     md5sum_dir=Path("2162"),
        ...     original_path=Path("2162/GCA_000302455.1.fna"),
        ...     status=VerificationStatus.FAIL,
        ...     expected_hash="abc123...",
        ...     accession_id="GCA_000302455.1"
        ... )
        >>> failed.can_redownload()
        True
    """
    md5_file_path: str
    md5sum_dir: Path
    original_path: Path
    status: VerificationStatus
    expected_hash: str
    accession_id: Optional[str] = None
    error_message: Optional[str] = None
    
    def can_redownload(self) -> bool:
        """
        Check if this file can be redownloaded.
        
        A file can be redownloaded if it has a valid Accession ID.
        
        Returns:
            True if accession_id is not None, False otherwise
        
        Examples:
            >>> failed = FailedFile("file.fna", Path("."), Path("file.fna"),
            ...                     VerificationStatus.FAIL, "abc123",
            ...                     accession_id="GCA_000302455.1")
            >>> failed.can_redownload()
            True
            >>> failed.accession_id = None
            >>> failed.can_redownload()
            False
        """
        return self.accession_id is not None


@dataclass
class AutoFixResult:
    """Result of the auto-fix process for failed files.
    
    Attributes:
        total_failed: Total number of files that failed verification
        redownloaded: Number of files attempted to redownload
        successfully_fixed: Number of files successfully fixed
        still_failed: Number of files that still fail after redownload
        skipped: Number of files skipped (no valid Accession ID)
        fixed_files: List of file paths that were successfully fixed
        still_failed_files: List of file paths that still fail
        skipped_files: List of file paths that were skipped
        processing_time: Total processing time in seconds
        report_path: Path to the generated report file
    
    Examples:
        >>> result = AutoFixResult(
        ...     total_failed=10,
        ...     redownloaded=8,
        ...     successfully_fixed=7,
        ...     still_failed=1,
        ...     skipped=2,
        ...     processing_time=120.5
        ... )
        >>> result.get_success_rate()
        87.5
    """
    total_failed: int
    redownloaded: int
    successfully_fixed: int
    still_failed: int
    skipped: int
    fixed_files: List[str] = field(default_factory=list)
    still_failed_files: List[str] = field(default_factory=list)
    skipped_files: List[str] = field(default_factory=list)
    processing_time: float = 0.0
    report_path: Optional[Path] = None
    
    def get_success_rate(self) -> float:
        """
        Calculate the success rate of the fix operation.
        
        Returns:
            Percentage of successfully fixed files out of redownloaded files
        
        Examples:
            >>> result = AutoFixResult(10, 8, 7, 1, 2)
            >>> result.get_success_rate()
            87.5
            >>> result = AutoFixResult(10, 0, 0, 0, 10)
            >>> result.get_success_rate()
            0.0
        """
        if self.redownloaded == 0:
            return 0.0
        return (self.successfully_fixed / self.redownloaded) * 100


@dataclass
class OrganizeResult:
    """Result of organizing downloaded files.
    
    Attributes:
        total_files: Total number of files to organize
        organized: Number of files successfully organized
        failed: Number of files that failed to organize
        organized_files: List of successfully organized file paths
        failed_files: List of file paths that failed to organize
        error_messages: Dictionary mapping file paths to error messages
    
    Examples:
        >>> result = OrganizeResult(
        ...     total_files=5,
        ...     organized=4,
        ...     failed=1,
        ...     organized_files=["file1.fna", "file2.fna"],
        ...     failed_files=["file3.fna"]
        ... )
        >>> result.is_complete_success()
        False
    """
    total_files: int
    organized: int
    failed: int
    organized_files: List[str] = field(default_factory=list)
    failed_files: List[str] = field(default_factory=list)
    error_messages: dict = field(default_factory=dict)
    
    def is_complete_success(self) -> bool:
        """
        Check if all files were successfully organized.
        
        Returns:
            True if all files were organized, False otherwise
        
        Examples:
            >>> result = OrganizeResult(5, 5, 0)
            >>> result.is_complete_success()
            True
            >>> result = OrganizeResult(5, 4, 1)
            >>> result.is_complete_success()
            False
        """
        return self.failed == 0


@dataclass
class VerificationResult:
    """Result of re-verifying fixed files.
    
    Attributes:
        total_verified: Total number of files verified
        passed: Number of files that passed verification
        failed: Number of files that failed verification
        passed_files: List of file paths that passed
        failed_files: List of file paths that failed
    
    Examples:
        >>> result = VerificationResult(
        ...     total_verified=5,
        ...     passed=4,
        ...     failed=1,
        ...     passed_files=["file1.fna", "file2.fna"],
        ...     failed_files=["file3.fna"]
        ... )
        >>> result.all_passed()
        False
    """
    total_verified: int
    passed: int
    failed: int
    passed_files: List[str] = field(default_factory=list)
    failed_files: List[str] = field(default_factory=list)
    
    def all_passed(self) -> bool:
        """
        Check if all verified files passed.
        
        Returns:
            True if all files passed, False otherwise
        
        Examples:
            >>> result = VerificationResult(5, 5, 0)
            >>> result.all_passed()
            True
            >>> result = VerificationResult(5, 4, 1)
            >>> result.all_passed()
            False
        """
        return self.failed == 0


@dataclass
class AutoFixReport:
    """Detailed report of the auto-fix process.
    
    This class aggregates all information about the auto-fix process
    for report generation.
    
    Attributes:
        start_time: Process start timestamp
        end_time: Process end timestamp
        verification_directory: Directory that was verified
        failed_files: List of FailedFile objects
        auto_fix_result: Result of the auto-fix process
        organize_result: Result of file organization
        verification_result: Result of re-verification
    
    Examples:
        >>> from datetime import datetime
        >>> report = AutoFixReport(
        ...     start_time=datetime.now(),
        ...     end_time=datetime.now(),
        ...     verification_directory=Path("/data"),
        ...     failed_files=[],
        ...     auto_fix_result=AutoFixResult(0, 0, 0, 0, 0),
        ...     organize_result=OrganizeResult(0, 0, 0),
        ...     verification_result=VerificationResult(0, 0, 0)
        ... )
        >>> report.verification_directory
        PosixPath('/data')
    """
    start_time: str
    end_time: str
    verification_directory: Path
    failed_files: List[FailedFile]
    auto_fix_result: AutoFixResult
    organize_result: OrganizeResult
    verification_result: VerificationResult
