"""
MD5 verification orchestrator for downloaded genome files.

This module provides the main coordination logic for MD5 verification,
including directory mode detection, MD5 file discovery, and verification
orchestration.
"""

import logging
import time
from pathlib import Path
from typing import List

from taxonomy_downloader.md5_models import (
    DirectoryMode,
    VerificationStatus,
    FileVerificationResult,
    VerificationResult,
    MD5Entry
)
from taxonomy_downloader.md5_parser import MD5Parser, MD5ParseError
from taxonomy_downloader.md5_computer import MD5Computer


logger = logging.getLogger(__name__)


class MD5Verifier:
    """Orchestrates MD5 verification for downloaded genome files.
    
    The verifier detects the directory structure mode (Taxon or Accession),
    discovers md5sum.txt files, and coordinates the verification process
    using the MD5Parser and MD5Computer components.
    
    Attributes:
        directory: Path to directory containing files to verify
        parser: MD5Parser instance for parsing md5sum.txt files
        computer: MD5Computer instance for computing file hashes
    
    Examples:
        >>> verifier = MD5Verifier("/path/to/downloads")
        >>> result = verifier.verify()
        >>> print(f"Passed: {result.passed}, Failed: {result.failed}")
    """
    
    def __init__(self, directory: str):
        """
        Initialize verifier for a directory.
        
        Args:
            directory: Path to directory containing files to verify
            
        Raises:
            ValueError: If directory path is empty
            FileNotFoundError: If directory does not exist
            NotADirectoryError: If path is not a directory
        
        Examples:
            >>> verifier = MD5Verifier("/path/to/downloads")
            >>> isinstance(verifier.directory, Path)
            True
        """
        if not directory:
            raise ValueError("Directory path cannot be empty")
        
        self.directory = Path(directory)
        
        if not self.directory.exists():
            raise FileNotFoundError(f"Directory not found: {self.directory}")
        
        if not self.directory.is_dir():
            raise NotADirectoryError(f"Path is not a directory: {self.directory}")
        
        self.parser = MD5Parser()
        self.computer = MD5Computer()
        
        logger.info(f"Initialized MD5Verifier for directory: {self.directory}")
    
    def verify(self) -> VerificationResult:
        """
        Execute verification process.
        
        This method:
        1. Detects directory mode (Taxon or Accession)
        2. Discovers md5sum.txt files
        3. Verifies all files referenced in md5sum.txt files
        4. Aggregates results
        
        Returns:
            VerificationResult with statistics and file results
            
        Raises:
            ValueError: If no md5sum.txt files are found
        
        Examples:
            >>> verifier = MD5Verifier("/path/to/downloads")
            >>> result = verifier.verify()
            >>> result.total_files >= 0
            True
        """
        start_time = time.time()
        
        # Detect directory mode
        mode = self._detect_directory_mode()
        logger.info(f"Detected directory mode: {mode.value}")
        
        # Find md5sum.txt files
        md5_files = self._find_md5_files(mode)
        
        if not md5_files:
            raise ValueError(
                f"No md5sum.txt files found in {self.directory}. "
                f"Cannot verify files without MD5 checksums."
            )
        
        logger.info(f"Found {len(md5_files)} md5sum.txt file(s)")
        
        # Verify all files
        all_file_results = []
        for md5_file in md5_files:
            logger.info(f"Processing {md5_file}")
            file_results = self._verify_md5_file(md5_file)
            all_file_results.extend(file_results)
        
        # Aggregate results
        total_files = len(all_file_results)
        passed = sum(1 for r in all_file_results if r.status == VerificationStatus.PASS)
        failed = sum(1 for r in all_file_results if r.status == VerificationStatus.FAIL)
        missing = sum(1 for r in all_file_results if r.status == VerificationStatus.MISSING)
        errors = sum(1 for r in all_file_results if r.status == VerificationStatus.ERROR)
        
        processing_time = time.time() - start_time
        
        result = VerificationResult(
            total_files=total_files,
            passed=passed,
            failed=failed,
            missing=missing,
            errors=errors,
            file_results=all_file_results,
            processing_time=processing_time
        )
        
        logger.info(
            f"Verification complete: {passed}/{total_files} passed, "
            f"{failed} failed, {missing} missing, {errors} errors "
            f"in {processing_time:.2f}s"
        )
        
        return result
    
    def _detect_directory_mode(self) -> DirectoryMode:
        """
        Detect if directory is Taxon mode or Accession mode.
        
        Detection logic:
        - If md5sum.txt exists in root AND no subdirectory md5sum.txt files exist:
          -> Accession mode
        - If md5sum.txt files exist in subdirectories OR no root md5sum.txt:
          -> Taxon mode
        
        Returns:
            DirectoryMode enum (TAXON or ACCESSION)
        
        Examples:
            >>> verifier = MD5Verifier("/path/to/accession_downloads")
            >>> mode = verifier._detect_directory_mode()
            >>> mode in [DirectoryMode.TAXON, DirectoryMode.ACCESSION]
            True
        """
        root_md5 = self.directory / "md5sum.txt"
        
        # Check for subdirectory md5sum.txt files
        # Look one level deep for efficiency
        subdir_md5_files = list(self.directory.glob("*/md5sum.txt"))
        
        if root_md5.exists() and not subdir_md5_files:
            # Only root md5sum.txt exists -> Accession mode
            logger.debug("Detected Accession mode: root md5sum.txt only")
            return DirectoryMode.ACCESSION
        else:
            # Either subdirectory md5 files exist, or no root md5sum.txt
            # -> Taxon mode
            logger.debug(
                f"Detected Taxon mode: "
                f"root_md5={root_md5.exists()}, "
                f"subdir_md5_count={len(subdir_md5_files)}"
            )
            return DirectoryMode.TAXON
    
    def _find_md5_files(self, mode: DirectoryMode) -> List[Path]:
        """
        Find all md5sum.txt files in the directory.
        
        For Taxon mode: Recursively search subdirectories
        For Accession mode: Look in root directory only
        
        Args:
            mode: Directory mode (TAXON or ACCESSION)
        
        Returns:
            List of paths to md5sum.txt files
        
        Examples:
            >>> verifier = MD5Verifier("/path/to/downloads")
            >>> mode = DirectoryMode.TAXON
            >>> files = verifier._find_md5_files(mode)
            >>> all(f.name == "md5sum.txt" for f in files)
            True
        """
        if mode == DirectoryMode.ACCESSION:
            # Look for md5sum.txt in root directory only
            root_md5 = self.directory / "md5sum.txt"
            if root_md5.exists() and root_md5.is_file():
                logger.debug(f"Found root md5sum.txt: {root_md5}")
                return [root_md5]
            else:
                logger.warning(f"No md5sum.txt found in root: {self.directory}")
                return []
        
        else:  # DirectoryMode.TAXON
            # Recursively search for md5sum.txt files in subdirectories
            md5_files = list(self.directory.glob("**/md5sum.txt"))
            
            # Filter to ensure they are files (not directories)
            md5_files = [f for f in md5_files if f.is_file()]
            
            logger.debug(f"Found {len(md5_files)} md5sum.txt files recursively")
            return md5_files
    
    def _verify_md5_file(self, md5_file: Path) -> List[FileVerificationResult]:
        """
        Verify all files referenced in an md5sum.txt file.
        
        This method:
        1. Parses the md5sum.txt file to extract entries
        2. For each entry, verifies the file exists and computes its hash
        3. Compares computed hash with expected hash
        4. Handles errors gracefully (missing files, read errors, etc.)
        
        Args:
            md5_file: Path to md5sum.txt file
            
        Returns:
            List of verification results for each file
        
        Examples:
            >>> verifier = MD5Verifier("/path/to/downloads")
            >>> results = verifier._verify_md5_file(Path("md5sum.txt"))
            >>> all(isinstance(r, FileVerificationResult) for r in results)
            True
        """
        results = []
        
        # Parse md5sum.txt file
        try:
            entries = self.parser.parse_md5_file(md5_file)
        except MD5ParseError as e:
            logger.error(f"Failed to parse {md5_file}: {e}")
            # Return empty results if we can't parse the file
            return results
        except FileNotFoundError as e:
            logger.error(f"MD5 file not found: {e}")
            return results
        
        # Verify each file
        for entry in entries:
            result = self._verify_single_file(entry, md5_file)
            results.append(result)
        
        return results
    
    def _verify_single_file(self, entry: MD5Entry, md5_file: Path) -> FileVerificationResult:
        """
        Verify a single file against its expected MD5 hash.
        
        Args:
            entry: MD5Entry with expected hash and file path
            md5_file: Path to the md5sum.txt file (for calculating relative paths)
            
        Returns:
            FileVerificationResult with verification status
        
        Examples:
            >>> verifier = MD5Verifier("/path/to/downloads")
            >>> entry = MD5Entry("abc123...", "file.txt", Path("/path/file.txt"))
            >>> result = verifier._verify_single_file(entry, Path("md5sum.txt"))
            >>> result.status in [VerificationStatus.PASS, VerificationStatus.FAIL,
            ...                   VerificationStatus.MISSING, VerificationStatus.ERROR]
            True
        """
        # Calculate full relative path from verification directory
        # entry.file_path is relative to md5sum.txt location (usually just filename)
        # entry.absolute_path is the full absolute path
        # We want the path relative to self.directory (verification root)
        try:
            full_relative_path = entry.absolute_path.relative_to(self.directory)
            display_path = str(full_relative_path).replace('\\', '/')
        except ValueError:
            # If relative_to fails, use the original file_path
            display_path = entry.file_path
        
        # Check if file exists
        if not entry.absolute_path.exists():
            logger.info(f"File not found: {display_path}")
            return FileVerificationResult(
                file_path=display_path,
                expected_hash=entry.hash_value,
                computed_hash=None,
                status=VerificationStatus.MISSING,
                error_message=f"File not found: {entry.absolute_path}"
            )
        
        # Compute hash
        try:
            computed_hash = self.computer.compute_hash(entry.absolute_path)
            
            # Compare hashes
            if computed_hash == entry.hash_value:
                logger.debug(f"PASS: {display_path}")
                return FileVerificationResult(
                    file_path=display_path,
                    expected_hash=entry.hash_value,
                    computed_hash=computed_hash,
                    status=VerificationStatus.PASS
                )
            else:
                logger.info(
                    f"FAIL: {display_path} "
                    f"(expected: {entry.hash_value}, got: {computed_hash})"
                )
                return FileVerificationResult(
                    file_path=display_path,
                    expected_hash=entry.hash_value,
                    computed_hash=computed_hash,
                    status=VerificationStatus.FAIL
                )
        
        except PermissionError as e:
            logger.error(f"Permission denied reading {display_path}: {e}")
            return FileVerificationResult(
                file_path=display_path,
                expected_hash=entry.hash_value,
                computed_hash=None,
                status=VerificationStatus.ERROR,
                error_message=f"Permission denied: {e}"
            )
        
        except IOError as e:
            logger.error(f"I/O error reading {display_path}: {e}")
            return FileVerificationResult(
                file_path=display_path,
                expected_hash=entry.hash_value,
                computed_hash=None,
                status=VerificationStatus.ERROR,
                error_message=f"I/O error: {e}"
            )
        
        except Exception as e:
            logger.error(f"Unexpected error verifying {display_path}: {e}")
            return FileVerificationResult(
                file_path=display_path,
                expected_hash=entry.hash_value,
                computed_hash=None,
                status=VerificationStatus.ERROR,
                error_message=f"Unexpected error: {e}"
            )
