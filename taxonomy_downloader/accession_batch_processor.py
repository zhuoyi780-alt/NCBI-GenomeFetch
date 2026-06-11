"""
Batch processor for accession-based genome downloads.

This module handles the download, extraction, rehydration, and file organization
for a single batch of accession numbers, including exponential backoff retry logic
for rate limit errors.
"""

import os
import shutil
import subprocess
import time
import zipfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Callable
from .accession_models import ArtifactResult, BatchResult, get_filename_for_accession
from .logging_config import get_logger, get_console
from .file_type_utils import (
    detect_file_type,
    find_requested_data_files,
    get_expected_file_extensions,
    normalize_include_params,
    standardize_filename,
)


class PackageStatus(Enum):
    """Preflight status for a dehydrated datasets package."""

    READY = "ready"
    MISSING_FETCH_MANIFEST = "missing_fetch_manifest"
    EMPTY_FETCH_MANIFEST = "empty_fetch_manifest"
    MALFORMED_PACKAGE = "malformed_package"


@dataclass
class PackageInspection:
    status: PackageStatus
    message: str


def redact_command(cmd: List[str]) -> List[str]:
    """Return a command list with sensitive values redacted."""
    redacted = []
    hide_next = False
    for part in cmd:
        if hide_next:
            redacted.append("***REDACTED***")
            hide_next = False
            continue
        redacted.append(part)
        if part == "--api-key":
            hide_next = True
    return redacted


class AccessionBatchProcessor:
    """Processes a single batch of accessions with retry logic."""
    
    def __init__(
        self,
        datasets_exe: str,
        output_dir: str,
        temp_root: str,
        api_key: Optional[str] = None,
        max_retries: int = 3,
        base_retry_delay: float = 1.0,
        include_params: Optional[List[str]] = None,
        download_timeout: int = 1800,
        rehydrate_timeout: int = 7200,
        keep_failed_temp: bool = False,
    ):
        """
        Initialize batch processor.
        
        Args:
            datasets_exe: Path to datasets executable
            output_dir: Output directory for final files
            temp_root: Root directory for temporary batch files
            api_key: Optional NCBI API key
            max_retries: Maximum retry attempts for rate limit errors
            base_retry_delay: Base delay in seconds for exponential backoff
            include_params: List of file types to include (e.g., ['genome', 'protein'])
        """
        self.datasets_exe = datasets_exe
        self.output_dir = Path(output_dir)
        self.temp_root = Path(temp_root)
        self.api_key = api_key
        self.max_retries = max_retries
        self.base_retry_delay = base_retry_delay
        self.include_params = include_params or ["genome"]
        self.download_timeout = download_timeout
        self.rehydrate_timeout = rehydrate_timeout
        self.keep_failed_temp = keep_failed_temp
        self.last_error_type = None
        self.last_error_message = None
        self.logger = get_logger("accession_batch_processor")
        self.console = get_console()
        
        # Ensure output directory exists
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _datasets_command_prefix(self) -> List[str]:
        """Return the datasets executable argv prefix for shell=False."""
        return [self.datasets_exe.strip("\"'")]
    
    def process_batch(self, batch_num: int, accessions: List[str]) -> BatchResult:
        """
        Process a single batch of accessions.
        
        Steps:
        1. Download dehydrated package (with exponential backoff retry)
        2. Extract zip file
        3. Rehydrate package (with exponential backoff retry)
        4. Move and rename .fna files
        5. Extract MD5 checksums
        6. Cleanup temporary files (guaranteed via try-finally)
        
        Args:
            batch_num: Batch sequence number
            accessions: List of accession numbers to process
            
        Returns:
            BatchResult with file count and MD5 info
        """
        self.batch_num = batch_num  # Store for use in retry logging
        batch_temp_dir = self.temp_root / f"batch_{batch_num}"
        keep_temp_dir = False
        
        try:
            # Create batch temporary directory
            batch_temp_dir.mkdir(parents=True, exist_ok=True)
            
            # Define paths
            output_zip = batch_temp_dir / "download.zip"
            extract_dir = batch_temp_dir / "extracted"
            
            # Step 1: Download dehydrated package
            self.logger.info(f"[Batch {batch_num}] Downloading {len(accessions)} accessions...")
            if not self._download_dehydrated(accessions, str(output_zip)):
                keep_temp_dir = self.keep_failed_temp
                return BatchResult(
                    batch_num=batch_num,
                    success=False,
                    files_saved=0,
                    md5_entries=[],
                    error_message=self.last_error_message or "Download failed",
                    error_type=self.last_error_type or "download_failed",
                )
            
            # Step 2: Extract package
            self.logger.info(f"[Batch {batch_num}] Extracting package...")
            if not self._extract_package(str(output_zip), str(extract_dir)):
                keep_temp_dir = self.keep_failed_temp
                return BatchResult(
                    batch_num=batch_num,
                    success=False,
                    files_saved=0,
                    md5_entries=[],
                    error_message="Extraction failed",
                    error_type="extraction_failed",
                )

            preflight = self._inspect_dehydrated_package(extract_dir)
            if preflight.status is not PackageStatus.READY:
                keep_temp_dir = self.keep_failed_temp
                return BatchResult(
                    batch_num=batch_num,
                    success=False,
                    files_saved=0,
                    md5_entries=[],
                    error_message=preflight.message,
                    error_type=self._package_error_type(preflight.status),
                )
            
            # Step 3: Rehydrate package
            self.logger.info(f"[Batch {batch_num}] Rehydrating package...")
            if not self._rehydrate_package(str(extract_dir)):
                keep_temp_dir = self.keep_failed_temp
                return BatchResult(
                    batch_num=batch_num,
                    success=False,
                    files_saved=0,
                    md5_entries=[],
                    error_message=self.last_error_message or "Rehydration failed",
                    error_type=self.last_error_type or "rehydrate_failed",
                )
            
            # Step 4: Parse MD5 file
            data_path = extract_dir / "ncbi_dataset" / "data"
            md5_path = extract_dir / "md5sum.txt"  # MD5 file is in the root of extracted package
            
            md5_map = {}
            if md5_path.exists():
                md5_map = self._parse_md5_file(str(md5_path))
                self.logger.debug(f"[Batch {batch_num}] Parsed {len(md5_map)} MD5 entries")
            else:
                self.logger.warning(f"[Batch {batch_num}] No MD5 file found at {md5_path}")
            
            # Step 5: Organize files and track completed accessions
            self.logger.info(f"[Batch {batch_num}] Organizing files...")
            completed_accessions = []
            artifacts: List[ArtifactResult] = []
            md5_entries = self._organize_files(
                str(data_path), md5_map, completed_accessions, artifacts
            )
            
            files_saved = len(md5_entries)
            self.logger.info(f"[Batch {batch_num}] Successfully processed {files_saved} files")
            
            # Check for partial failures: some accessions in batch didn't produce files
            expected_count = len(accessions)
            actual_count = len(completed_accessions)
            
            if actual_count < expected_count:
                missing_count = expected_count - actual_count
                missing_accessions = set(accessions) - set(completed_accessions)
                self.logger.warning(
                    f"[Batch {batch_num}] Partial failure: {missing_count} accession(s) "
                    f"did not produce files: {missing_accessions}"
                )
                # Still return success=True because batch processing succeeded,
                # but completed_accessions will reflect actual completions
            
            return BatchResult(
                batch_num=batch_num,
                success=True,
                files_saved=files_saved,
                md5_entries=md5_entries,
                artifacts=artifacts,
                completed_accessions=completed_accessions
            )
            
        except Exception as e:
            keep_temp_dir = self.keep_failed_temp
            self.logger.error(f"[Batch {batch_num}] Unexpected error: {e}", exc_info=True)
            return BatchResult(
                batch_num=batch_num,
                success=False,
                files_saved=0,
                md5_entries=[],
                error_message=str(e),
                error_type="unexpected_error",
            )
        
        finally:
            # Guaranteed cleanup of temporary directory
            if batch_temp_dir.exists() and not keep_temp_dir:
                try:
                    shutil.rmtree(batch_temp_dir)
                    self.logger.debug(f"[Batch {batch_num}] Cleaned up temporary directory")
                except Exception as e:
                    self.logger.error(f"[Batch {batch_num}] Failed to cleanup temp directory: {e}")
            elif keep_temp_dir:
                self.logger.warning(f"[Batch {batch_num}] Preserved failed temp directory: {batch_temp_dir}")
    
    def _download_dehydrated(self, accessions: List[str], output_zip: str) -> bool:
        """
        Download dehydrated package for accessions with retry logic.
        
        Args:
            accessions: List of accession numbers
            output_zip: Output zip file path
            
        Returns:
            True if successful, False otherwise
        """
        def download_operation():
            # Build command
            cmd = self._datasets_command_prefix() + [
                "download",
                "genome",
                "accession"
            ]
            cmd.extend(accessions)
            cmd.extend([
                "--filename", output_zip,
                "--dehydrated"
            ])
            
            # Add API key if available
            if self.api_key:
                cmd.extend(["--api-key", self.api_key])
            
            # Add include parameter
            if self.include_params:
                cmd.extend(["--include", ",".join(self.include_params)])
            
            # Execute command with extended timeout for large batches
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.download_timeout,
                shell=False
            )
            
            if result.returncode != 0:
                error_msg = result.stderr.lower()
                # Check for rate limit errors
                if any(keyword in error_msg for keyword in ['rate limit', 'too many requests', '429']):
                    raise RateLimitError(result.stderr)
                raise Exception(f"Download failed: {result.stderr}")
            
            return True
        
        def safe_download_operation():
            try:
                return download_operation()
            except subprocess.TimeoutExpired:
                self.last_error_type = "download_timeout"
                self.last_error_message = (
                    f"Download timed out after {self.download_timeout}s"
                )
                raise OperationTimeoutError(self.last_error_message)
        
        return self._execute_with_retry(safe_download_operation, "download")
    
    def _extract_package(self, zip_path: str, extract_dir: str) -> bool:
        """
        Extract zip package to directory.
        
        Args:
            zip_path: Path to zip file
            extract_dir: Directory to extract to
            
        Returns:
            True if successful, False otherwise
        """
        try:
            extract_path = Path(extract_dir)
            extract_path.mkdir(parents=True, exist_ok=True)
            
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(extract_path)
            
            self.logger.debug(f"Extracted package to {extract_dir}")
            return True
            
        except Exception as e:
            self.logger.error(f"[Batch {self.batch_num}] Extraction failed: {e}", exc_info=True)
            return False
    
    def _inspect_dehydrated_package(self, extract_dir: Path) -> PackageInspection:
        """Validate required dehydrated package inputs before rehydration."""
        extract_path = Path(extract_dir)
        ncbi_dataset_dir = extract_path / "ncbi_dataset"
        fetch_candidates = [
            ncbi_dataset_dir / "fetch.txt",
            extract_path / "fetch.txt",
        ]
        fetch_path = next((path for path in fetch_candidates if path.exists()), None)
        if fetch_path is None and (
            not ncbi_dataset_dir.exists() or not ncbi_dataset_dir.is_dir()
        ):
            return PackageInspection(
                PackageStatus.MALFORMED_PACKAGE,
                "Dehydrated package is missing ncbi_dataset directory",
            )

        if fetch_path is None:
            return PackageInspection(
                PackageStatus.MISSING_FETCH_MANIFEST,
                "ncbi_dataset/fetch.txt missing after dehydrated extraction",
            )

        if fetch_path.stat().st_size == 0:
            return PackageInspection(
                PackageStatus.EMPTY_FETCH_MANIFEST,
                "ncbi_dataset/fetch.txt is empty after dehydrated extraction",
            )

        return PackageInspection(PackageStatus.READY, "Package ready for rehydration")

    def _package_error_type(self, status: PackageStatus) -> str:
        return {
            PackageStatus.MISSING_FETCH_MANIFEST: "package_missing_fetch_manifest",
            PackageStatus.EMPTY_FETCH_MANIFEST: "package_empty_fetch_manifest",
            PackageStatus.MALFORMED_PACKAGE: "malformed_package",
            PackageStatus.READY: "",
        }[status]
    
    def _rehydrate_package(self, directory: str) -> bool:
        """
        Rehydrate the extracted package with retry logic.
        
        This method is resilient to partial failures. If some files are unavailable
        (403 Forbidden errors), it will continue processing available files and only
        log warnings for unavailable ones.
        
        Args:
            directory: Directory containing dehydrated package
            
        Returns:
            True if successful (including partial success), False only for complete failure
        """
        def rehydrate_operation():
            # Build command
            cmd = self._datasets_command_prefix() + [
                "rehydrate",
                "--directory", directory
            ]
            
            # Add API key if available
            if self.api_key:
                cmd.extend(["--api-key", self.api_key])
            
            # Execute command with extended timeout for large batches
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.rehydrate_timeout,
                shell=False
            )
            
            if result.returncode != 0:
                error_msg = result.stderr.lower()
                
                # Check for rate limit errors (should retry)
                if any(keyword in error_msg for keyword in ['rate limit', 'too many requests', '429']):
                    raise RateLimitError(result.stderr)
                
                # Check for 403 Forbidden errors (file unavailable - partial failure)
                if '403 forbidden' in error_msg or 'file unavailable' in error_msg:
                    # Count how many files failed with 403
                    forbidden_count = result.stderr.count('403 Forbidden')
                    
                    self.logger.warning(
                        f"[Batch {self.batch_num}] Rehydration completed with {forbidden_count} "
                        f"unavailable file(s) (403 Forbidden). These files may have been "
                        f"suppressed or removed from NCBI."
                    )
                    
                    # Log the specific files that failed
                    for line in result.stderr.split('\n'):
                        if '403 Forbidden' in line or 'File unavailable' in line:
                            self.logger.warning(f"[Batch {self.batch_num}] {line.strip()}")
                    
                    # Return True to continue processing available files
                    # The organize step will handle missing files gracefully
                    return True
                
                # For other errors, raise exception to trigger retry
                raise Exception(f"Rehydration failed: {result.stderr}")
            
            return True
        
        def safe_rehydrate_operation():
            try:
                return rehydrate_operation()
            except subprocess.TimeoutExpired:
                self.last_error_type = "rehydrate_timeout"
                self.last_error_message = (
                    f"Rehydrate timed out after {self.rehydrate_timeout}s"
                )
                raise OperationTimeoutError(self.last_error_message)
        
        return self._execute_with_retry(safe_rehydrate_operation, "rehydrate")
    
    def _get_file_extensions(self) -> List[str]:
        """
        Get file extensions based on include_params.
        
        Uses the shared file_type_utils module to determine which file
        extensions should be searched for based on the configured include
        parameters.
        
        Returns:
            Sorted list of file extensions (with dots) to search for
        
        Examples:
            >>> processor._get_file_extensions()  # with include_params=['genome', 'protein']
            ['.fa', '.faa', '.fasta', '.fna']
        """
        return get_expected_file_extensions(self.include_params)
    
    def _standardize_filename(self, file_path: Path, accession: str) -> str:
        """
        Standardize filename to {accession}.{extension} format.
        
        Uses the shared file_type_utils module to ensure consistent naming
        across all file types. This includes:
        - Extension normalization (.fa → .fna, .gff3 → .gff, .gb → .gbff)
        - CDS files get .cds extension
        - All files follow {accession}.{extension} format
        
        Args:
            file_path: Original file path
            accession: Accession number (e.g., 'GCF_001267435.1')
        
        Returns:
            Standardized filename
        
        Examples:
            >>> processor._standardize_filename(Path('protein.faa'), 'GCF_001267435.1')
            'GCF_001267435.1.faa'
            
            >>> processor._standardize_filename(Path('cds_from_genomic.fna'), 'GCF_001267435.1')
            'GCF_001267435.1.cds'
            
            >>> processor._standardize_filename(Path('genomic.gff'), 'GCF_001267435.1')
            'GCF_001267435.1.gff'
        """
        return standardize_filename(file_path, accession)
    
    def _find_md5_hash(self, md5_map: Dict[str, str], accession: str, filename: str) -> Optional[str]:
        """
        Find MD5 hash for a file from the MD5 map.
        
        Args:
            md5_map: Dictionary mapping file paths to MD5 hashes
            accession: Accession number
            filename: Original filename
        
        Returns:
            MD5 hash string or None if not found
        """
        # Try to find MD5 entry by matching the original path
        for original_path, hash_value in md5_map.items():
            # Check if this path corresponds to our file
            if accession in original_path and filename in original_path:
                return hash_value
        return None
    
    def _organize_files(
        self, 
        data_path: str, 
        md5_map: Dict[str, str],
        completed_accessions: List[str],
        artifacts: Optional[List[ArtifactResult]] = None,
    ) -> List[Tuple[str, str]]:
        """
        Organize all file types with standardized naming.
        
        This method now handles all file types specified in include_params,
        not just .fna files. Files are renamed to {accession}.{extension}
        format for consistency.
        
        Args:
            data_path: Path to data directory containing accession subdirectories
            md5_map: Mapping of file paths to MD5 hashes
            completed_accessions: List to track successfully saved accessions
            
        Returns:
            List of (md5_hash, filename) tuples
        """
        data_dir = Path(data_path)
        if not data_dir.exists():
            self.logger.warning(f"Data directory does not exist: {data_path}")
            return []
        
        # Get expected file extensions based on include_params
        file_extensions = self._get_file_extensions()
        self.logger.info(f"Looking for files with extensions: {file_extensions}")
        
        md5_entries = []
        artifacts = artifacts if artifacts is not None else []
        requested_types = set(normalize_include_params(self.include_params))
        
        # Iterate through accession subdirectories
        for accession_dir in data_dir.iterdir():
            if not accession_dir.is_dir():
                continue
            
            # Extract accession from directory name (preserve full accession with version)
            accession = accession_dir.name
            
            saved_types = set()
            
            matching_files = find_requested_data_files(accession_dir, self.include_params)

            for data_file in matching_files:
                try:
                    # Standardize filename to {accession}.{extension} format
                    include_type = detect_file_type(data_file.name, self.include_params)
                    output_filename = standardize_filename(data_file, accession, include_type)
                    output_path = self.output_dir / output_filename

                    # Get MD5 hash for this file
                    md5_hash = self._find_md5_hash(md5_map, accession, data_file.name)
                    if not md5_hash:
                        self.last_error_type = "checksum_mapping_missing"
                        self.last_error_message = f"No MD5 hash found for {output_filename}"
                        self.logger.warning(self.last_error_message)
                        continue

                    temp_output = output_path.with_name(
                        f".{output_path.name}.tmp.batch{getattr(self, 'batch_num', 0)}.{os.getpid()}"
                    )
                    shutil.copy2(data_file, temp_output)
                    os.replace(temp_output, output_path)
                    self.logger.debug(f"Copied {data_file.name} -> {output_filename}")

                    md5_entries.append((md5_hash, output_filename))
                    artifacts.append(
                        ArtifactResult(
                            accession=accession,
                            include_type=include_type or "genome",
                            filename=output_filename,
                            expected_md5=md5_hash,
                        )
                    )
                    if include_type:
                        saved_types.add(include_type)

                except Exception as e:
                    self.logger.error(f"Failed to organize file {data_file}: {e}")
            
            if requested_types.issubset(saved_types):
                completed_accessions.append(accession)
            elif saved_types:
                missing_types = requested_types - saved_types
                self.logger.warning(
                    f"Accession {accession} missing requested include types: "
                    f"{', '.join(sorted(missing_types))}"
                )
        
        return md5_entries
    
    def _parse_md5_file(self, md5_path: str) -> Dict[str, str]:
        """
        Parse md5sum.txt and return path->hash mapping.
        
        Handles both / and \\ path separators (cross-platform).
        
        Args:
            md5_path: Path to md5sum.txt file
            
        Returns:
            Dictionary mapping file paths to MD5 hashes
        """
        md5_map = {}
        
        try:
            with open(md5_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    
                    # Skip empty lines and comments
                    if not line or line.startswith('#'):
                        continue
                    
                    # Parse MD5 line: "hash  filepath" or "hash *filepath"
                    parts = line.split(None, 1)
                    if len(parts) != 2:
                        continue
                    
                    hash_value, filepath = parts
                    
                    # Remove binary mode indicator if present
                    if filepath.startswith('*'):
                        filepath = filepath[1:]
                    
                    # Normalize path separators to forward slashes
                    filepath = filepath.replace('\\', '/')
                    
                    md5_map[filepath] = hash_value
            
            self.logger.debug(f"Parsed {len(md5_map)} MD5 entries from {md5_path}")
            
        except Exception as e:
            self.logger.error(f"Failed to parse MD5 file {md5_path}: {e}")
        
        return md5_map
    
    def _execute_with_retry(self, func: Callable, operation_name: str) -> bool:
        """
        Execute function with exponential backoff retry for rate limit errors.
        
        Args:
            func: Function to execute (should raise RateLimitError on rate limit)
            operation_name: Name of operation for logging
            
        Returns:
            True if successful, False otherwise
        """
        for attempt in range(self.max_retries + 1):
            try:
                func()
                return True
                
            except RateLimitError as e:
                self.last_error_type = "rate_limited"
                self.last_error_message = str(e)
                if attempt < self.max_retries:
                    # Calculate exponential backoff delay
                    delay = self.base_retry_delay * (2 ** attempt)
                    
                    # Log to file
                    self.logger.warning(
                        f"[Batch {self.batch_num}] Rate limit hit during {operation_name}, "
                        f"retrying in {delay}s... (attempt {attempt + 1}/{self.max_retries})"
                    )
                    
                    # Display to console
                    self.console.print_warning(
                        f"[Batch {self.batch_num}] Rate limit hit, retrying in {delay:.0f}s... "
                        f"(attempt {attempt + 1}/{self.max_retries})"
                    )
                    
                    time.sleep(delay)
                else:
                    # Log final failure
                    self.logger.error(
                        f"[Batch {self.batch_num}] Rate limit error during {operation_name} "
                        f"after {self.max_retries} retries: {e}"
                    )
                    return False
            
            except OperationTimeoutError as e:
                self.logger.error(
                    f"[Batch {getattr(self, 'batch_num', '?')}] "
                    f"Error during {operation_name}: {e}"
                )
                return False
            
            except Exception as e:
                self.last_error_message = str(e)
                if self.last_error_type is None:
                    self.last_error_type = f"{operation_name}_failed"
                self.logger.error(
                    f"[Batch {getattr(self, 'batch_num', '?')}] "
                    f"Error during {operation_name}: {e}"
                )
                return False
        
        return False


class RateLimitError(Exception):
    """Exception raised when a rate limit error is detected."""
    pass


class OperationTimeoutError(Exception):
    """Exception raised for sanitized subprocess timeouts."""
