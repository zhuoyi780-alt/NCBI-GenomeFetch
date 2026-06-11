"""
Failed file collector for MD5 verification auto-fix functionality.

This module implements the FailedFileCollector class that identifies files
with FAIL, MISSING, or ERROR status from MD5 verification results and
determines their paths using md5sum.txt directory context.
"""

import logging
from pathlib import Path
from typing import List

from taxonomy_downloader.md5_models import VerificationResult, VerificationStatus, FileVerificationResult
from taxonomy_downloader.md5_autofix_models import FailedFile

logger = logging.getLogger(__name__)


class FailedFileCollector:
    """
    Collects failed files from MD5 verification results.
    
    This class filters files with FAIL, MISSING, or ERROR status and determines
    their paths using the md5sum.txt directory context. This approach avoids
    global file searches that could cause same-name file conflicts in Taxon mode.
    
    Key Algorithm:
        For each failed file, the original_path is determined by:
        original_path = md5sum_dir / md5_file_path
        
        Where:
        - md5sum_dir: Directory containing the md5sum.txt file (relative to verification_dir)
        - md5_file_path: Simplified filename from md5sum.txt
        
        This direct concatenation avoids conflicts when different subdirectories
        have files with the same name (e.g., dataset_catalog.json in multiple
        species directories).
    
    Attributes:
        verification_result: The MD5 verification result to process
        verification_dir: Root directory where verification was performed
    
    Examples:
        >>> from taxonomy_downloader.md5_models import VerificationResult, FileVerificationResult, VerificationStatus
        >>> result = VerificationResult(
        ...     total_files=3,
        ...     passed=1,
        ...     failed=1,
        ...     missing=1,
        ...     errors=0,
        ...     file_results=[
        ...         FileVerificationResult("file1.fna", "abc123", "abc123", VerificationStatus.PASS),
        ...         FileVerificationResult("file2.fna", "def456", "xyz789", VerificationStatus.FAIL),
        ...         FileVerificationResult("file3.fna", "ghi789", None, VerificationStatus.MISSING)
        ...     ]
        ... )
        >>> collector = FailedFileCollector(result, Path("/data"))
        >>> failed_files = collector.collect_failed_files()
        >>> len(failed_files)
        2
        >>> failed_files[0].status == VerificationStatus.FAIL
        True
    """
    
    def __init__(self, verification_result: VerificationResult, verification_dir: Path):
        """
        Initialize the FailedFileCollector.
        
        Args:
            verification_result: The MD5 verification result containing file results
            verification_dir: Root directory where verification was performed
        
        Examples:
            >>> result = VerificationResult(10, 8, 1, 1, 0, [], 5.0)
            >>> collector = FailedFileCollector(result, Path("/data"))
            >>> collector.verification_dir
            PosixPath('/data')
        """
        self.verification_result = verification_result
        self.verification_dir = Path(verification_dir)
        logger.info(
            f"Initialized FailedFileCollector for {verification_dir} "
            f"with {verification_result.total_files} total files"
        )
    
    def collect_failed_files(self) -> List[FailedFile]:
        """
        Collect all files that failed MD5 verification.
        
        This method filters files with FAIL, MISSING, or ERROR status from the
        verification results and builds FailedFile data structures with complete
        path information.
        
        The method uses the md5sum.txt directory context to determine the original
        path, avoiding global searches that could cause conflicts in Taxon mode
        where different subdirectories may have files with the same name.
        
        Returns:
            List of FailedFile objects containing complete information about
            each failed file, including md5_file_path, md5sum_dir, original_path,
            status, and expected_hash.
        
        Examples:
            >>> result = VerificationResult(
            ...     total_files=2,
            ...     passed=0,
            ...     failed=1,
            ...     missing=1,
            ...     errors=0,
            ...     file_results=[
            ...         FileVerificationResult("file1.fna", "abc123", "xyz789", VerificationStatus.FAIL),
            ...         FileVerificationResult("file2.fna", "def456", None, VerificationStatus.MISSING)
            ...     ]
            ... )
            >>> collector = FailedFileCollector(result, Path("/data"))
            >>> failed_files = collector.collect_failed_files()
            >>> len(failed_files)
            2
            >>> failed_files[0].status == VerificationStatus.FAIL
            True
            >>> failed_files[1].status == VerificationStatus.MISSING
            True
        """
        failed_files = []
        
        # Filter files with FAIL, MISSING, or ERROR status
        for file_result in self.verification_result.file_results:
            if file_result.status in (
                VerificationStatus.FAIL,
                VerificationStatus.MISSING,
                VerificationStatus.ERROR
            ):
                failed_file = self._build_failed_file(file_result)
                failed_files.append(failed_file)
        
        logger.info(
            f"Collected {len(failed_files)} failed files: "
            f"{self.verification_result.failed} FAIL, "
            f"{self.verification_result.missing} MISSING, "
            f"{self.verification_result.errors} ERROR"
        )
        
        return failed_files
    
    def _build_failed_file(self, file_result: FileVerificationResult) -> FailedFile:
        """
        Build a FailedFile data structure from a FileVerificationResult.
        
        This method determines the file paths using the md5sum.txt directory context:
        1. md5_file_path: The simplified filename from md5sum.txt (file_result.file_path)
        2. md5sum_dir: The directory containing md5sum.txt (extracted from file_result.file_path)
        3. original_path: Complete path = md5sum_dir / md5_file_path
        
        The algorithm avoids global file searches by using the md5sum.txt directory
        context. This prevents conflicts when different subdirectories have files
        with the same name.
        
        Args:
            file_result: The FileVerificationResult for a failed file
        
        Returns:
            FailedFile object with complete path information
        
        Examples:
            >>> file_result = FileVerificationResult(
            ...     file_path="2162/GCA_000302455.1.fna",
            ...     expected_hash="abc123",
            ...     computed_hash="xyz789",
            ...     status=VerificationStatus.FAIL
            ... )
            >>> collector = FailedFileCollector(
            ...     VerificationResult(1, 0, 1, 0, 0, [file_result], 1.0),
            ...     Path("/data")
            ... )
            >>> failed_file = collector._build_failed_file(file_result)
            >>> failed_file.md5_file_path
            'GCA_000302455.1.fna'
            >>> failed_file.md5sum_dir
            PosixPath('2162')
            >>> failed_file.original_path
            PosixPath('2162/GCA_000302455.1.fna')
        """
        # Parse the file_path to extract md5sum_dir and md5_file_path
        file_path = Path(file_result.file_path)
        
        # Determine md5sum_dir and md5_file_path based on directory structure
        # This logic handles both Taxon mode (hierarchical) and Accession mode (flat)
        if len(file_path.parts) > 1:
            # Taxon mode: file is in a subdirectory (e.g., "2162/GCA_000302455.1.fna")
            # The parent directory is where md5sum.txt is located
            # md5sum_dir is the parent directory, md5_file_path is the filename
            md5sum_dir = file_path.parent
            md5_file_path = file_path.name
        else:
            # Accession mode: file is in root directory (e.g., "GCA_000302455.1.fna")
            # The md5sum.txt is in the root directory (represented as ".")
            # md5sum_dir is ".", md5_file_path is the filename
            md5sum_dir = Path(".")
            md5_file_path = file_path.name
        
        # original_path is the complete relative path (same as file_result.file_path)
        # This is used later to determine where to place the redownloaded file
        original_path = file_path
        
        # Build FailedFile object with all path information
        # Note: accession_id is None here and will be populated by AccessionExtractor
        failed_file = FailedFile(
            md5_file_path=md5_file_path,
            md5sum_dir=md5sum_dir,
            original_path=original_path,
            status=file_result.status,
            expected_hash=file_result.expected_hash,
            accession_id=None,  # Will be extracted later by AccessionExtractor
            error_message=file_result.error_message
        )
        
        logger.debug(
            f"Built FailedFile: md5_file_path={md5_file_path}, "
            f"md5sum_dir={md5sum_dir}, original_path={original_path}, "
            f"status={file_result.status.value}"
        )
        
        return failed_file
