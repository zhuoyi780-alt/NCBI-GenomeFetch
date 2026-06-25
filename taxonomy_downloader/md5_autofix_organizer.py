"""
File organizer adapter for MD5 verification auto-fix functionality.

This module implements the FileOrganizerAdapter class that organizes downloaded
files by matching them to failed file records, applying filename simplification,
and moving them to their original paths.
"""

import logging
import os
import re
import shutil
import zipfile
from pathlib import Path
from typing import List, Dict, Optional, Tuple

from taxonomy_downloader.md5_autofix_models import FailedFile, OrganizeResult
from taxonomy_downloader.md5_autofix_filename_simplifier import FilenameSimplifier
from taxonomy_downloader.md5_autofix_state import (
    AutoFixStateStore,
    AutoFixTask,
    AutoFixTaskStatus,
)

logger = logging.getLogger(__name__)


class FileOrganizerAdapter:
    """
    Organizes downloaded files by matching them to failed file records.
    
    This class builds an index from Accession ID to FailedFile for fast matching,
    discovers downloaded files in the temporary directory, matches files to
    FailedFile records, applies filename simplification, and moves files to
    their original paths.
    
    Key Functionality:
        1. Build index: Accession ID -> FailedFile for O(1) lookup
        2. Discover downloaded files in temp directory
        3. Match files to FailedFile records using Accession ID
        4. Apply FilenameSimplifier to rename files
        5. Move files to original_path locations
        6. Create necessary directory structures
        7. Handle file overwriting and permission errors
    
    Attributes:
        verification_dir: Root directory where verification was performed
        failed_files: List of FailedFile objects to organize
        accession_index: Dictionary mapping Accession ID to FailedFile
        ACCESSION_PATTERN: Regex pattern for matching Accession IDs
    
    Examples:
        >>> from taxonomy_downloader.md5_autofix_models import FailedFile
        >>> from taxonomy_downloader.md5_models import VerificationStatus
        >>> failed_files = [
        ...     FailedFile(
        ...         md5_file_path="GCA_000302455.1.fna",
        ...         md5sum_dir=Path("2162"),
        ...         original_path=Path("2162/GCA_000302455.1.fna"),
        ...         status=VerificationStatus.FAIL,
        ...         expected_hash="abc123",
        ...         accession_id="GCA_000302455.1"
        ...     )
        ... ]
        >>> organizer = FileOrganizerAdapter(Path("/data"), failed_files)
        >>> len(organizer.accession_index)
        1
    """
    
    # Regex pattern for NCBI Accession IDs
    ACCESSION_PATTERN = re.compile(r'(GC[FA]_\d{9}\.\d+)', re.IGNORECASE)
    
    def __init__(self, verification_dir: Path, failed_files: List[FailedFile]):
        """
        Initialize the FileOrganizerAdapter.
        
        Builds an index from Accession ID to FailedFile for fast O(1) lookup
        during file matching.
        
        Args:
            verification_dir: Root directory where verification was performed
            failed_files: List of FailedFile objects with accession_id populated
        
        Examples:
            >>> failed_files = [
            ...     FailedFile("file.fna", Path("."), Path("file.fna"),
            ...                VerificationStatus.FAIL, "abc123",
            ...                accession_id="GCA_000302455.1")
            ... ]
            >>> organizer = FileOrganizerAdapter(Path("/data"), failed_files)
            >>> organizer.verification_dir
            PosixPath('/data')
        """
        self.verification_dir = Path(verification_dir)
        self.failed_files = failed_files
        
        # Build index: Accession ID -> FailedFile for O(1) lookup
        self.accession_index: Dict[str, FailedFile] = {}
        for failed_file in failed_files:
            if failed_file.accession_id:
                self.accession_index[failed_file.accession_id] = failed_file
        
        logger.info(
            f"Initialized FileOrganizerAdapter with {len(failed_files)} failed files, "
            f"{len(self.accession_index)} have valid Accession IDs"
        )

    def organize_downloaded_files(self, temp_dir: Path) -> OrganizeResult:
        """
        Organize downloaded files from temporary directory to original paths.
        
        This method:
        1. Discovers downloaded files in the temporary directory
        2. Matches each file to a FailedFile record using Accession ID
        3. Applies filename simplification
        4. Moves files to their original_path locations
        5. Creates necessary directory structures
        6. Handles file overwriting and permission errors
        
        Args:
            temp_dir: Temporary directory containing downloaded files
        
        Returns:
            OrganizeResult with statistics and lists of organized/failed files
        
        Examples:
            >>> organizer = FileOrganizerAdapter(Path("/data"), [])
            >>> result = organizer.organize_downloaded_files(Path("/tmp/downloads"))
            >>> result.total_files >= 0
            True
        """
        logger.info(f"Starting file organization from {temp_dir}")
        
        # Discover downloaded files
        discovered_files = self._discover_downloaded_files(temp_dir)
        
        logger.info(f"Discovered {len(discovered_files)} files in temporary directory")
        
        organized_files = []
        failed_files = []
        error_messages = {}
        
        # Process each discovered file
        for idx, (accession_id, source_path) in enumerate(discovered_files, 1):
            # Requirement 12.3: Display current file being processed
            logger.info(f"Organizing file {idx} of {len(discovered_files)}: {source_path.name}")
            
            try:
                # Match to FailedFile record
                failed_file = self._match_accession_to_failed_file(accession_id)
                
                if not failed_file:
                    logger.warning(
                        f"No matching FailedFile record for Accession ID: {accession_id}, "
                        f"file: {source_path.name}"
                    )
                    failed_files.append(str(source_path))
                    error_messages[str(source_path)] = f"No matching record for {accession_id}"
                    continue
                
                # Move and rename file
                self._move_and_rename_file(source_path, failed_file)
                
                organized_files.append(str(failed_file.original_path))
                logger.info(f"Successfully organized: {failed_file.original_path}")
                
            except Exception as e:
                logger.error(
                    f"Failed to organize file {source_path.name} "
                    f"(Accession: {accession_id}): {e}"
                )
                failed_files.append(str(source_path))
                error_messages[str(source_path)] = str(e)
        
        result = OrganizeResult(
            total_files=len(discovered_files),
            organized=len(organized_files),
            failed=len(failed_files),
            organized_files=organized_files,
            failed_files=failed_files,
            error_messages=error_messages
        )
        
        logger.info(
            f"File organization complete: {result.organized} organized, "
            f"{result.failed} failed out of {result.total_files} total"
        )
        
        return result

    def organize_task(
        self, task: AutoFixTask, state_store: AutoFixStateStore
    ) -> bool:
        """
        Organize one resumable auto-fix task from persistent cache to target path.

        This task-aware path keeps the downloaded cache intact, creates an audit
        backup of any replaced target, and records state after each durable step.
        """
        try:
            state_store.update_task(task.task_id, status=AutoFixTaskStatus.ORGANIZING)
            task = state_store.state.tasks[task.task_id]

            if not task.cached_file:
                raise FileNotFoundError("Task has no cached_file recorded")

            source_path = self._resolve_inside_verification_dir(
                task.cached_file, "cached file"
            )
            target_path = self._resolve_inside_verification_dir(
                task.target_path or task.original_path, "target path"
            )

            if not source_path.exists() or not source_path.is_file():
                raise FileNotFoundError(f"Cached file not found: {source_path}")

            target_path.parent.mkdir(parents=True, exist_ok=True)

            backup_path = None
            if target_path.exists():
                backup_dir = state_store.backups_dir / task.task_id
                backup_dir.mkdir(parents=True, exist_ok=True)
                backup_path = backup_dir / f"{target_path.name}.bak"
                shutil.copy2(str(target_path), str(backup_path))

            tmp_target = target_path.with_name(f".{target_path.name}.md5_autofix_tmp")
            shutil.copy2(str(source_path), str(tmp_target))
            os.replace(str(tmp_target), str(target_path))

            update = {
                "status": AutoFixTaskStatus.ORGANIZED,
                "target_path": state_store.relative_to_verification_dir(target_path),
                "last_error": None,
            }
            if backup_path is not None:
                update["backup_path"] = state_store.relative_to_verification_dir(
                    backup_path
                )
            state_store.update_task(task.task_id, **update)
            logger.info("Organized auto-fix task %s to %s", task.task_id, target_path)
            return True

        except Exception as exc:
            logger.error("Failed to organize auto-fix task %s: %s", task.task_id, exc)
            state_store.update_task(
                task.task_id,
                status=AutoFixTaskStatus.FAILED_FATAL,
                last_error=str(exc),
            )
            return False

    def _resolve_inside_verification_dir(self, path_value: str, label: str) -> Path:
        path = Path(path_value)
        if not path.is_absolute():
            path = self.verification_dir / path

        root = self.verification_dir.resolve()
        resolved = path.resolve()
        try:
            resolved.relative_to(root)
        except ValueError:
            raise ValueError(f"{label} is outside verification directory: {path_value}")
        return resolved
    
    def _discover_downloaded_files(self, temp_dir: Path) -> List[Tuple[str, Path]]:
        """
        Discover downloaded files in the temporary directory.
        
        This method supports two directory structures:
        1. AccessionDownloader direct output: files directly in temp_dir with standardized names
        2. Standard ncbi_dataset structure: temp_dir/accession/ncbi_dataset/data/accession/
        
        Args:
            temp_dir: Temporary directory containing downloaded files
        
        Returns:
            List of (accession_id, file_path) tuples
        """
        discovered = []
        
        # Step 1: Check for .zip files and extract them
        for item in temp_dir.iterdir():
            if item.is_file() and item.suffix == '.zip':
                logger.info(f"Extracting zip file: {item.name}")
                try:
                    extract_dir = temp_dir / item.stem
                    with zipfile.ZipFile(item, 'r') as zip_ref:
                        zip_ref.extractall(extract_dir)
                    logger.debug(f"Extracted {item.name} to {extract_dir}")
                except Exception as e:
                    logger.error(f"Failed to extract {item.name}: {e}")
        
        # Step 2: Check for AccessionDownloader direct output format
        # Files are directly in temp_dir with standardized names (e.g., GCA_000302455.1.fna)
        for item in temp_dir.iterdir():
            if item.is_file() and not item.name.endswith('.zip'):
                # Try to extract accession from filename
                accession_id = self._extract_accession_from_path(item.name)
                if accession_id:
                    discovered.append((accession_id, item))
                    logger.debug(f"Discovered file (direct): {item.name} for {accession_id}")
        
        # If we found files in direct format, return them
        if discovered:
            logger.info(f"Found {len(discovered)} files in AccessionDownloader direct output format")
            return discovered
        
        # Step 3: Search for standard ncbi_dataset structure
        # temp_dir/accession_id/ncbi_dataset/data/accession_id/
        for accession_dir in temp_dir.iterdir():
            if not accession_dir.is_dir():
                continue
            
            # Try to extract accession_id from directory name
            accession_id = self._extract_accession_from_path(accession_dir.name)
            
            if not accession_id:
                logger.debug(f"Directory {accession_dir.name} is not an Accession ID, checking subdirectories")
                continue
            
            # Look for files in the standard ncbi_dataset/data/accession_id/ structure
            data_dir = accession_dir / "ncbi_dataset" / "data" / accession_id
            
            if data_dir.exists() and data_dir.is_dir():
                # Standard structure found - collect all files in the data directory
                for file_path in data_dir.iterdir():
                    if file_path.is_file():
                        discovered.append((accession_id, file_path))
                        logger.debug(f"Discovered file (standard): {file_path.name} for {accession_id}")
            else:
                # Fallback: search for files directly in accession_dir
                logger.debug(f"Standard data directory not found, searching in {accession_dir}")
                for file_path in accession_dir.rglob('*'):
                    if file_path.is_file() and not file_path.name.endswith('.zip'):
                        file_accession = self._extract_accession_from_path(str(file_path))
                        if file_accession:
                            discovered.append((file_accession, file_path))
                            logger.debug(f"Discovered file (fallback): {file_path.name} for {file_accession}")
        
        return discovered
    
    def _match_accession_to_failed_file(self, accession_id: str) -> Optional[FailedFile]:
        """
        Match an Accession ID to a FailedFile record using the index.
        
        Uses O(1) dictionary lookup for fast matching.
        
        Args:
            accession_id: NCBI Accession ID to match
        
        Returns:
            FailedFile object if found, None otherwise
        
        Examples:
            >>> failed_file = FailedFile("file.fna", Path("."), Path("file.fna"),
            ...                          VerificationStatus.FAIL, "abc123",
            ...                          accession_id="GCA_000302455.1")
            >>> organizer = FileOrganizerAdapter(Path("/data"), [failed_file])
            >>> matched = organizer._match_accession_to_failed_file("GCA_000302455.1")
            >>> matched is not None
            True
        """
        return self.accession_index.get(accession_id)
    
    def _move_and_rename_file(self, source_path: Path, failed_file: FailedFile) -> None:
        """
        Move and rename a file to its original path with simplified filename.
        
        This method:
        1. Applies FilenameSimplifier to generate the simplified filename
        2. Constructs the target path using failed_file.original_path
        3. Creates necessary directory structures
        4. Moves and renames the file (overwrites if exists)
        
        Args:
            source_path: Path to the downloaded file in temporary directory
            failed_file: FailedFile record containing target path information
        
        Raises:
            PermissionError: If file cannot be written due to permissions
            OSError: If file operation fails
        
        Examples:
            >>> # This method requires actual file system operations
            >>> # See unit tests for examples
            pass
        """
        # Step 1: Apply filename simplification
        # Convert NCBI original filename (e.g., "GCA_000837045.1_ViralProj14067_genomic.fna")
        # to simplified format (e.g., "GCA_000837045.1.fna")
        original_filename = source_path.name
        simplified_filename = FilenameSimplifier.simplify_filename(original_filename)
        
        # Step 2: Construct target path
        # The target directory is verification_dir / failed_file.md5sum_dir
        # This ensures the file goes back to its original location
        # The target filename is the simplified filename to match md5sum.txt
        target_dir = self.verification_dir / failed_file.md5sum_dir
        target_path = target_dir / simplified_filename
        
        logger.debug(
            f"Moving file: {source_path} -> {target_path} "
            f"(original: {original_filename}, simplified: {simplified_filename})"
        )
        
        # Step 3: Create target directory if it doesn't exist
        # This handles cases where the directory structure was deleted
        target_dir.mkdir(parents=True, exist_ok=True)
        
        # Step 4: Move and rename file (overwrite if exists)
        # This replaces the failed file with the newly downloaded version
        shutil.move(str(source_path), str(target_path))
        
        logger.debug(f"Successfully moved file to {target_path}")
    
    def _extract_accession_from_path(self, file_path: str) -> Optional[str]:
        """
        Extract Accession ID from a file path or filename.
        
        Uses regex pattern matching to find NCBI Accession IDs.
        
        Args:
            file_path: File path or filename to extract from
        
        Returns:
            Extracted Accession ID if found, None otherwise
        
        Examples:
            >>> organizer = FileOrganizerAdapter(Path("/data"), [])
            >>> organizer._extract_accession_from_path("GCA_000302455.1")
            'GCA_000302455.1'
            >>> organizer._extract_accession_from_path("invalid") is None
            True
        """
        if not file_path:
            return None
        
        match = self.ACCESSION_PATTERN.search(file_path)
        
        if match:
            accession = match.group(1)
            # Normalize to uppercase for consistency
            return accession.upper()
        
        return None
