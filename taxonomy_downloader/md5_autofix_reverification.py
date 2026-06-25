"""
Re-verification module for MD5 auto-fix functionality.

This module provides functionality to re-verify files after they have been
redownloaded and organized, ensuring they now pass MD5 verification.
"""

import hashlib
import logging
from pathlib import Path
from typing import Dict, List, Optional

from taxonomy_downloader.md5_autofix_models import VerificationResult
from taxonomy_downloader.md5_autofix_state import (
    AutoFixStateStore,
    AutoFixTask,
    AutoFixTaskStatus,
)

logger = logging.getLogger(__name__)


class ReVerification:
    """Re-verifies files after redownload and organization.
    
    This class calculates MD5 hashes for organized files and compares them
    with expected values from md5sum.txt to determine if the fix was successful.
    
    Attributes:
        verification_dir: Directory containing files to verify
        md5sum_file: Path to md5sum.txt file with expected hashes
        expected_hashes: Dictionary mapping file paths to expected MD5 hashes
    
    Examples:
        >>> verifier = ReVerification(
        ...     verification_dir=Path("/data"),
        ...     md5sum_file=Path("/data/md5sum.txt")
        ... )
        >>> result = verifier.verify_fixed_files(["file1.fna", "file2.fna"])
        >>> result.all_passed()
        True
    """
    
    def __init__(self, verification_dir: Path, md5sum_files: List[Path]):
        """Initialize the re-verification module.
        
        Args:
            verification_dir: Directory containing files to verify
            md5sum_files: List of paths to md5sum.txt files
        
        Raises:
            FileNotFoundError: If no valid md5sum files exist
        """
        self.verification_dir = Path(verification_dir)
        
        # Support both single file (Path) and multiple files (List[Path])
        if isinstance(md5sum_files, Path):
            md5sum_files = [md5sum_files]
        
        self.md5sum_files = [Path(f) for f in md5sum_files]
        self.md5sum_file = self.md5sum_files[0] if self.md5sum_files else None
        
        # Validate that at least one file exists
        valid_files = [f for f in self.md5sum_files if f.exists()]
        if self.md5sum_files and not valid_files:
            raise FileNotFoundError(
                f"No valid md5sum.txt files found. Checked: {self.md5sum_files}"
            )
        
        self.md5sum_files = valid_files
        
        # Load expected hashes from all md5sum.txt files
        # Create a mapping: file_path -> (expected_hash, md5sum_file)
        self.expected_hashes = self._load_all_expected_hashes()
        logger.info(
            f"Loaded {len(self.expected_hashes)} expected hashes from "
            f"{len(self.md5sum_files)} md5sum.txt file(s)"
        )
    
    def verify_fixed_files(self, file_paths: List[str]) -> VerificationResult:
        """Verify a list of fixed files against expected MD5 hashes.
        
        Args:
            file_paths: List of file paths (relative to verification_dir) to verify
        
        Returns:
            VerificationResult containing verification statistics and file lists
        
        Examples:
            >>> verifier = ReVerification(Path("/data"), Path("/data/md5sum.txt"))
            >>> result = verifier.verify_fixed_files(["file1.fna", "file2.fna"])
            >>> result.passed
            2
        """
        passed_files = []
        failed_files = []
        
        logger.info(f"Re-verifying {len(file_paths)} fixed files")
        
        for file_path in file_paths:
            file_path_str = str(file_path)
            full_path = self.verification_dir / file_path
            
            if not full_path.exists():
                logger.error(f"File not found for verification: {full_path}")
                failed_files.append(file_path_str)
                continue
            
            expected_hash = self._get_expected_hash(file_path_str)
            if expected_hash is None:
                logger.warning(f"No expected hash found for {file_path_str}")
                failed_files.append(file_path_str)
                continue
            
            calculated_hash = self._calculate_md5(full_path)
            
            if calculated_hash == expected_hash:
                logger.info(f"✓ Verification passed: {file_path_str}")
                passed_files.append(file_path_str)
            else:
                logger.error(
                    f"✗ Verification failed: {file_path_str} "
                    f"(expected: {expected_hash}, got: {calculated_hash})"
                )
                failed_files.append(file_path_str)
        
        result = VerificationResult(
            total_verified=len(file_paths),
            passed=len(passed_files),
            failed=len(failed_files),
            passed_files=passed_files,
            failed_files=failed_files
        )
        
        logger.info(
            f"Re-verification complete: {result.passed} passed, {result.failed} failed"
        )
        
        return result

    def verify_task(
        self,
        task: AutoFixTask,
        state_store: Optional[AutoFixStateStore] = None,
    ) -> bool:
        """Verify one auto-fix task using its expected hash from persistent state."""
        self._update_task(
            task,
            state_store,
            status=AutoFixTaskStatus.VERIFYING,
            last_error=None,
        )

        if state_store and state_store.state:
            task = state_store.state.tasks[task.task_id]

        target_path = self._task_target_path(task)
        if not target_path.exists() or not target_path.is_file():
            message = f"Target file not found for verification: {target_path}"
            logger.error(message)
            self._update_task(
                task,
                state_store,
                status=AutoFixTaskStatus.STILL_FAILED,
                last_error=message,
            )
            return False

        calculated_hash = self._calculate_md5(target_path)
        if calculated_hash == task.expected_hash:
            self._update_task(
                task,
                state_store,
                status=AutoFixTaskStatus.FIXED,
                computed_hash=calculated_hash,
                last_error=None,
            )
            logger.info("Verification passed for auto-fix task %s", task.task_id)
            return True

        message = (
            f"MD5 mismatch for {task.target_path or task.original_path}: "
            f"expected {task.expected_hash}, got {calculated_hash}"
        )
        logger.error(message)
        self._update_task(
            task,
            state_store,
            status=AutoFixTaskStatus.STILL_FAILED,
            computed_hash=calculated_hash,
            last_error=message,
        )
        return False

    def _task_target_path(self, task: AutoFixTask) -> Path:
        target_path = Path(task.target_path or task.original_path)
        if target_path.is_absolute():
            return target_path
        return self.verification_dir / target_path

    def _update_task(
        self,
        task: AutoFixTask,
        state_store: Optional[AutoFixStateStore],
        **changes,
    ) -> None:
        if state_store is not None:
            state_store.update_task(task.task_id, **changes)
            return

        for key, value in changes.items():
            if key == "status" and not isinstance(value, AutoFixTaskStatus):
                value = AutoFixTaskStatus(value)
            setattr(task, key, value)
    
    def _calculate_md5(self, file_path: Path) -> str:
        """Calculate MD5 hash of a file.
        
        Args:
            file_path: Path to the file
        
        Returns:
            MD5 hash as hexadecimal string
        
        Examples:
            >>> verifier = ReVerification(Path("/data"), Path("/data/md5sum.txt"))
            >>> hash_value = verifier._calculate_md5(Path("/data/file.txt"))
            >>> len(hash_value)
            32
        """
        md5_hash = hashlib.md5()
        
        try:
            with open(file_path, 'rb') as f:
                # Read file in chunks to handle large files efficiently
                # Using 8KB chunks is a good balance between memory usage and I/O performance
                # This prevents loading entire large genome files into memory
                for chunk in iter(lambda: f.read(8192), b''):
                    md5_hash.update(chunk)
            
            return md5_hash.hexdigest()
        except Exception as e:
            logger.error(f"Error calculating MD5 for {file_path}: {e}")
            return ""
    
    def _get_expected_hash(self, file_path: str) -> Optional[str]:
        """Get expected MD5 hash for a file from md5sum.txt.
        
        This method searches for the file in the expected_hashes dictionary,
        which contains hashes from all md5sum.txt files.
        
        Args:
            file_path: File path (relative to verification_dir)
        
        Returns:
            Expected MD5 hash, or None if not found
        
        Examples:
            >>> verifier = ReVerification(Path("/data"), [Path("/data/md5sum.txt")])
            >>> hash_value = verifier._get_expected_hash("file.fna")
            >>> hash_value is not None
            True
        """
        # Normalize path separators for comparison
        normalized_path = file_path.replace('\\', '/')
        
        # Try exact match first
        if normalized_path in self.expected_hashes:
            return self.expected_hashes[normalized_path]
        
        # Try matching just the filename (for files in subdirectories)
        filename = Path(normalized_path).name
        for stored_path, hash_value in self.expected_hashes.items():
            if Path(stored_path).name == filename:
                # Check if the directory matches
                stored_dir = str(Path(stored_path).parent)
                file_dir = str(Path(normalized_path).parent)
                if stored_dir == file_dir or stored_dir == '.':
                    return hash_value
        
        return None
    
    def _load_all_expected_hashes(self) -> Dict[str, str]:
        """Load expected MD5 hashes from all md5sum.txt files.
        
        This method loads hashes from multiple md5sum.txt files and combines them.
        For each file, it stores the path relative to the md5sum.txt location.
        
        Returns:
            Dictionary mapping file paths to MD5 hashes
        
        Examples:
            >>> verifier = ReVerification(Path("/data"), [Path("/data/md5sum.txt")])
            >>> hashes = verifier._load_all_expected_hashes()
            >>> isinstance(hashes, dict)
            True
        """
        all_hashes = {}
        
        for md5_file in self.md5sum_files:
            try:
                # Get the directory containing this md5sum.txt
                md5_dir = md5_file.parent
                
                # Calculate relative path from verification_dir to md5_dir
                try:
                    rel_md5_dir = md5_dir.relative_to(self.verification_dir)
                except ValueError:
                    # md5_file is not under verification_dir, skip it
                    logger.warning(f"Skipping {md5_file}: not under verification directory")
                    continue
                
                file_hashes = self._load_expected_hashes_from_file(md5_file, rel_md5_dir)
                
                # Merge into all_hashes (later files override earlier ones)
                all_hashes.update(file_hashes)
                
                logger.debug(
                    f"Loaded {len(file_hashes)} hashes from {md5_file} "
                    f"(directory: {rel_md5_dir})"
                )
            
            except Exception as e:
                logger.error(f"Error loading hashes from {md5_file}: {e}")
                continue
        
        return all_hashes
    
    def _load_expected_hashes_from_file(
        self, 
        md5_file: Path, 
        rel_dir: Path
    ) -> Dict[str, str]:
        """Load expected MD5 hashes from a single md5sum.txt file.
        
        Args:
            md5_file: Path to md5sum.txt file
            rel_dir: Relative directory path from verification_dir to md5_file's directory
        
        Returns:
            Dictionary mapping file paths to MD5 hashes
        """
        hashes = {}
        
        try:
            with open(md5_file, 'r', encoding='utf-8') as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    
                    # Skip empty lines and comments
                    if not line or line.startswith('#'):
                        continue
                    
                    # Parse MD5 sum file format: "hash  filename" or "hash *filename"
                    parts = line.split(None, 1)
                    if len(parts) != 2:
                        logger.warning(
                            f"Invalid format in {md5_file} line {line_num}: {line}"
                        )
                        continue
                    
                    hash_value, file_path = parts
                    
                    # Remove leading asterisk if present (binary mode indicator)
                    if file_path.startswith('*'):
                        file_path = file_path[1:]
                    
                    # Normalize path separators
                    file_path = file_path.replace('\\', '/')
                    
                    # Construct full relative path from verification_dir
                    # If rel_dir is '.', file is in root, otherwise prepend directory
                    if str(rel_dir) == '.':
                        full_rel_path = file_path
                    else:
                        full_rel_path = str(rel_dir / file_path).replace('\\', '/')
                    
                    # Store hash in lowercase for case-insensitive comparison
                    hashes[full_rel_path] = hash_value.lower()
        
        except Exception as e:
            logger.error(f"Error reading {md5_file}: {e}")
            raise
        
        return hashes
