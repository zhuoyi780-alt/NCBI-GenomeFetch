"""
Taxon processing workflow with cross-platform temporary file handling.
"""

import os
import shutil
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
import platform

from .models import DownloadConfig, TaxonResult, ErrorType
from .datasets_interface import DatasetsInterface
from .file_organizer import FileOrganizer
from .error_handler import ErrorHandler
from .logging_config import get_logger
from .file_type_utils import (
    detect_file_type,
    extract_accession_from_path,
    find_requested_data_files,
    get_expected_file_extensions,
    standardize_filename,
)


class TaxonProcessor:
    """
    Handles the complete dehydration/rehydration workflow for a single taxon.
    
    Implements atomic file operations with cross-platform temporary file handling,
    including retry mechanisms for Windows file locking issues.
    """
    
    def __init__(self, config: DownloadConfig, error_handler: ErrorHandler):
        """
        Initialize TaxonProcessor.
        
        Args:
            config: Download configuration
            error_handler: Error handler for logging and classification
        """
        self.config = config
        self.error_handler = error_handler
        self.logger = get_logger("taxon_processor")
        
        # Initialize components
        self.datasets_interface = DatasetsInterface(config)
        self.file_organizer = FileOrganizer(config.output_dir)
        
        # Set up temporary directory with platform-appropriate detection
        self.temp_base_dir = self._setup_temp_directory()
        
        # Windows-specific retry configuration for atomic operations
        self.max_atomic_retries = 3
        self.atomic_retry_base_delay = 0.1  # 100ms base delay
        self.atomic_retry_max_delay = 2.0   # 2 second max delay
        
        self.logger.info(f"TaxonProcessor initialized with temp directory: {self.temp_base_dir}")
    
    def _setup_temp_directory(self) -> Path:
        """
        Set up temporary directory with platform-appropriate detection.
        
        Returns:
            Path to temporary directory base
        """
        if self.config.temp_dir:
            # User-specified temporary directory
            temp_base = Path(self.config.temp_dir)
            temp_base.mkdir(parents=True, exist_ok=True)
            self.logger.debug(f"Using user-specified temp directory: {temp_base}")
        else:
            # Platform-appropriate temporary directory detection
            temp_base = Path(tempfile.gettempdir())
            self.logger.debug(f"Using system temp directory: {temp_base}")
        
        # Create subdirectory for our operations
        our_temp_dir = temp_base / "taxonomy_downloader"
        our_temp_dir.mkdir(parents=True, exist_ok=True)
        
        # Log platform-specific information
        system = platform.system()
        self.logger.debug(f"Platform: {system}, Temp directory: {our_temp_dir}")
        
        return our_temp_dir
    
    def process_taxon(self, taxon: str) -> TaxonResult:
        """
        Process a single taxon through the complete dehydration/rehydration workflow.
        
        Implements all-or-nothing file placement with atomic operations and
        comprehensive cleanup on both success and failure.
        
        Args:
            taxon: Taxon name or TaxID to process
            
        Returns:
            TaxonResult with processing outcome
        """
        start_time = time.time()
        temp_files_to_cleanup = []
        
        try:
            self.logger.info(f"Starting processing for taxon: {taxon}")
            
            # Step 1: Download dehydrated package
            dehydrated_file = self._download_dehydrated(taxon)
            temp_files_to_cleanup.append(dehydrated_file)
            
            # Step 2: Extract dehydrated package
            extract_dir = self._extract_package(dehydrated_file)
            temp_files_to_cleanup.append(extract_dir)
            
            # Step 3: Rehydrate package
            self._rehydrate_package(extract_dir)
            
            # Step 4: Organize files atomically
            files_organized = self._organize_files_atomically(extract_dir, taxon)
            
            # Step 5: Cleanup temporary files on success
            self._cleanup_temp_files(temp_files_to_cleanup)
            
            processing_time = time.time() - start_time
            
            self.logger.info(f"Successfully processed taxon '{taxon}' in {processing_time:.2f}s, "
                           f"organized {files_organized} files")
            
            return TaxonResult(
                taxon=taxon,
                success=True,
                files_found=files_organized,
                processing_time=processing_time
            )
            
        except Exception as e:
            processing_time = time.time() - start_time
            
            # Classify error and log appropriately
            error_type = self.error_handler.classify_exception(e)
            
            self.error_handler.log_error(
                taxon=taxon,
                error_type=error_type,
                message=f"Processing failed: {str(e)}",
                exception=e,
                context={
                    "processing_time": processing_time,
                    "temp_files": [str(f) for f in temp_files_to_cleanup]
                }
            )
            
            # Cleanup temporary files on failure
            self._cleanup_temp_files(temp_files_to_cleanup)
            
            return TaxonResult(
                taxon=taxon,
                success=False,
                error_message=str(e),
                error_type=error_type,
                processing_time=processing_time
            )
    
    def _download_dehydrated(self, taxon: str) -> Path:
        """
        Download dehydrated package for a taxon.
        
        Args:
            taxon: Taxon name or TaxID
            
        Returns:
            Path to downloaded dehydrated package
            
        Raises:
            Exception: If download fails
        """
        # Create unique temporary file for this taxon
        temp_file = self.temp_base_dir / f"{self._sanitize_filename(taxon)}_dehydrated.zip"
        
        self.logger.debug(f"Downloading dehydrated package for '{taxon}' to {temp_file}")
        
        success = self.datasets_interface.download_taxonomy_dehydrated(taxon, str(temp_file))
        
        if not success:
            raise Exception(f"Failed to download dehydrated package for taxon '{taxon}'")
        
        if not temp_file.exists():
            raise Exception(f"Dehydrated package file not found after download: {temp_file}")
        
        # Validate file size
        file_size = temp_file.stat().st_size
        if file_size == 0:
            raise Exception(f"Downloaded dehydrated package is empty: {temp_file}")
        
        self.logger.debug(f"Downloaded dehydrated package: {file_size} bytes")
        return temp_file
    
    def _extract_package(self, zip_path: Path) -> Path:
        """
        Extract dehydrated package to temporary directory.
        
        Args:
            zip_path: Path to dehydrated package ZIP file
            
        Returns:
            Path to extraction directory
            
        Raises:
            Exception: If extraction fails
        """
        extract_dir = self.temp_base_dir / f"{zip_path.stem}_extracted"
        
        # Remove existing extraction directory if it exists
        if extract_dir.exists():
            shutil.rmtree(extract_dir)
        
        extract_dir.mkdir(parents=True)
        
        self.logger.debug(f"Extracting package {zip_path} to {extract_dir}")
        
        try:
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(extract_dir)
            
            # Validate extraction
            extracted_files = list(extract_dir.rglob("*"))
            if not extracted_files:
                raise Exception("No files found after extraction")
            
            self.logger.debug(f"Extracted {len(extracted_files)} files/directories")
            return extract_dir
            
        except zipfile.BadZipFile as e:
            raise Exception(f"Invalid ZIP file: {e}")
        except Exception as e:
            raise Exception(f"Extraction failed: {e}")
    
    def _rehydrate_package(self, extract_dir: Path) -> None:
        """
        Rehydrate the extracted package to download actual sequence files.
        
        Args:
            extract_dir: Directory containing extracted dehydrated package
            
        Raises:
            Exception: If rehydration fails
        """
        self.logger.debug(f"Rehydrating package in {extract_dir}")
        
        success = self.datasets_interface.rehydrate_package(str(extract_dir))
        
        if not success:
            raise Exception(f"Failed to rehydrate package in {extract_dir}")
        
        # Validate that rehydration produced requested data files
        include_params = self.config.include_params or ["genome"]
        data_files = find_requested_data_files(extract_dir, include_params)
        if not data_files:
            expected_extensions = get_expected_file_extensions(include_params)
            raise Exception(
                "No requested data files found after rehydration for include types: "
                f"{','.join(include_params)}; expected extensions: {expected_extensions}"
            )

        self.logger.debug(
            f"Rehydration completed, found {len(data_files)} requested data files"
        )
    
    def _organize_files_atomically(self, source_dir: Path, taxon: str) -> int:
        """
        Organize files atomically with all-or-nothing placement.
        
        Uses temporary file suffixes and atomic rename operations to ensure
        either complete success or clean failure with no partial files.
        
        Args:
            source_dir: Source directory containing files to organize
            taxon: Taxon name for organization
            
        Returns:
            Number of files organized
            
        Raises:
            Exception: If organization fails
        """
        self.logger.debug(f"Organizing files atomically for taxon '{taxon}'")
        
        # Create taxon directory
        sanitized_name = self.file_organizer._sanitize_taxon_name(taxon)
        taxon_dir = Path(self.config.output_dir) / sanitized_name
        taxon_dir.mkdir(parents=True, exist_ok=True)
        
        include_params = self.config.include_params or ["genome"]
        expected_extensions = self.config.get_expected_file_extensions()
        self.logger.info(
            f"Looking for include types {include_params} with extensions: {expected_extensions}"
        )

        # Find requested files with include-aware filtering
        data_files = find_requested_data_files(source_dir, include_params)
        
        # Find MD5 files
        md5_files = list(source_dir.rglob("md5sum.txt")) + list(source_dir.rglob("*.md5"))
        
        if not data_files:
            raise Exception(f"No data files found with extensions {expected_extensions}")
        
        # Track original → standardized filename mapping for MD5 updates
        filename_mapping = {}
        temp_files_created = []
        destination_names = set()
        
        try:
            # Step 1: Copy and rename all data files
            for data_file in data_files:
                # Extract accession from path
                accession = extract_accession_from_path(data_file)
                
                include_type = detect_file_type(data_file.name, include_params)

                if accession:
                    # Standardize filename
                    new_filename = standardize_filename(data_file, accession, include_type)
                    self.logger.debug(f"Renaming: {data_file.name} → {new_filename}")
                else:
                    # Keep original name if no accession found
                    new_filename = data_file.name
                    self.logger.warning(f"No accession found for {data_file}, keeping original name")

                if new_filename in destination_names:
                    raise Exception(f"Multiple source files map to output filename: {new_filename}")
                destination_names.add(new_filename)
                
                # Copy to temp location
                temp_dest = taxon_dir / f"{new_filename}.tmp"
                self._atomic_copy_with_retry(data_file, temp_dest)
                temp_files_created.append(temp_dest)
                
                # Track mapping for MD5 updates
                rel_path = self.file_organizer._relative_posix(data_file, source_dir)
                filename_mapping[rel_path] = new_filename
                stripped_path = self.file_organizer._strip_ncbi_data_prefix(rel_path)
                filename_mapping[stripped_path] = new_filename
            
            # Step 2: Process MD5 files with filename mapping
            if md5_files:
                temp_md5_file = taxon_dir / "md5sum.txt.tmp"
                self._create_merged_md5_file(md5_files, temp_md5_file, taxon_dir, filename_mapping)
                temp_files_created.append(temp_md5_file)
            
            # Step 3: Atomically rename all temp files
            final_files = []
            for temp_file in temp_files_created:
                final_file = temp_file.with_suffix('')  # Remove .tmp suffix
                self._atomic_rename_with_retry(temp_file, final_file)
                final_files.append(final_file)
            
            self.logger.info(f"Atomically organized {len(data_files)} files for taxon '{taxon}'")
            return len(data_files)  # Return count of data files organized
            
        except Exception as e:
            # Clean up any temporary files on failure
            self._cleanup_temp_files(temp_files_created)
            raise Exception(f"Atomic file organization failed: {e}")
    
    def _atomic_copy_with_retry(self, src: Path, dest: Path) -> None:
        """
        Perform atomic file copy with Windows-specific retry mechanism.
        
        Args:
            src: Source file path
            dest: Destination file path (should have .tmp suffix)
            
        Raises:
            Exception: If copy fails after all retries
        """
        for attempt in range(self.max_atomic_retries):
            try:
                shutil.copy2(src, dest)
                return
                
            except PermissionError as e:
                if attempt < self.max_atomic_retries - 1:
                    # Calculate exponential backoff delay
                    delay = min(
                        self.atomic_retry_base_delay * (2 ** attempt),
                        self.atomic_retry_max_delay
                    )
                    
                    self.logger.warning(f"PermissionError copying {src} to {dest}, "
                                      f"retrying in {delay:.2f}s (attempt {attempt + 1}/{self.max_atomic_retries})")
                    time.sleep(delay)
                else:
                    raise Exception(f"Failed to copy file after {self.max_atomic_retries} attempts: {e}")
            
            except Exception as e:
                raise Exception(f"File copy failed: {e}")
    
    def _atomic_rename_with_retry(self, src: Path, dest: Path) -> None:
        """
        Perform atomic rename with Windows-specific retry mechanism.
        
        Uses os.replace() for cross-platform atomic rename with retry logic
        to handle Windows PermissionError from antivirus/indexing services.
        
        Args:
            src: Source file path (temporary file)
            dest: Destination file path (final location)
            
        Raises:
            Exception: If rename fails after all retries
        """
        for attempt in range(self.max_atomic_retries):
            try:
                # Use os.replace() for atomic rename on both Windows and Unix
                os.replace(str(src), str(dest))
                return
                
            except PermissionError as e:
                if attempt < self.max_atomic_retries - 1:
                    # Calculate exponential backoff delay
                    delay = min(
                        self.atomic_retry_base_delay * (2 ** attempt),
                        self.atomic_retry_max_delay
                    )
                    
                    self.logger.warning(f"PermissionError renaming {src} to {dest}, "
                                      f"retrying in {delay:.2f}s (attempt {attempt + 1}/{self.max_atomic_retries}). "
                                      f"This may be caused by antivirus or file indexing services.")
                    time.sleep(delay)
                else:
                    raise Exception(f"Failed to rename file after {self.max_atomic_retries} attempts: {e}. "
                                  f"This may be caused by antivirus software or Windows file indexing services "
                                  f"temporarily locking the file.")
            
            except Exception as e:
                raise Exception(f"Atomic rename failed: {e}")
    
    def _create_merged_md5_file(self, md5_files: List[Path], output_file: Path, taxon_dir: Path, filename_mapping: Dict[str, str] = None) -> None:
        """
        Create merged MD5 file with corrected paths and filename mapping.
        
        Args:
            md5_files: List of MD5 files to merge
            output_file: Output file path (should have .tmp suffix)
            taxon_dir: Taxon directory for path correction
            filename_mapping: Optional mapping of original → new filenames for MD5 path updates
        """
        merged_lines = []
        
        for md5_file in md5_files:
            try:
                # Pass filename_mapping to FileOrganizer for MD5 path updates
                corrected_lines = self.file_organizer._read_and_fix_md5_file(
                    md5_file, 
                    taxon_dir, 
                    filename_mapping
                )
                merged_lines.extend(corrected_lines)
            except Exception as e:
                self.logger.warning(f"Failed to process MD5 file {md5_file}: {e}")
        
        if merged_lines:
            # Write merged MD5 file with Unix-style paths
            with open(output_file, 'w', encoding='utf-8', newline='\n') as f:
                f.write("# MD5 checksums generated by taxonomy-genome-downloader\n")
                f.write("# Compatible with standard md5sum tools on Windows and Linux\n")
                f.write("# Use 'md5sum -c md5sum.txt' to verify file integrity\n")
                f.write("#\n")
                
                for line in merged_lines:
                    # Ensure Unix-style paths (forward slashes)
                    unix_line = line.replace('\\', '/')
                    f.write(f"{unix_line}\n")
    
    def _cleanup_temp_files(self, temp_files: List[Path]) -> None:
        """
        Clean up temporary files and directories with platform-specific considerations.
        
        Args:
            temp_files: List of temporary files/directories to clean up
        """
        for temp_path in temp_files:
            if not temp_path or not temp_path.exists():
                continue
            
            try:
                if temp_path.is_file():
                    temp_path.unlink()
                    self.logger.debug(f"Cleaned up temp file: {temp_path}")
                elif temp_path.is_dir():
                    shutil.rmtree(temp_path)
                    self.logger.debug(f"Cleaned up temp directory: {temp_path}")
            except Exception as e:
                self.logger.warning(f"Failed to clean up temp path {temp_path}: {e}")
    
    def _sanitize_filename(self, filename: str) -> str:
        """
        Sanitize filename for cross-platform compatibility.
        
        Args:
            filename: Original filename
            
        Returns:
            Sanitized filename safe for all platforms
        """
        # Replace problematic characters and handle Unicode
        sanitized = filename.replace(' ', '_')
        
        # Keep only ASCII alphanumeric characters, underscore, hyphen, and dot
        # This ensures cross-platform compatibility and avoids encoding issues
        sanitized = ''.join(c for c in sanitized if c.isascii() and (c.isalnum() or c in '-_.'))
        
        # Ensure not empty
        if not sanitized:
            sanitized = "unknown"
        
        # Limit length
        if len(sanitized) > 100:
            sanitized = sanitized[:100]
        
        return sanitized
    
    def validate_temp_directory(self) -> Dict[str, Any]:
        """
        Validate temporary directory setup and permissions.
        
        Returns:
            Dictionary with validation results
        """
        result = {
            'temp_directory': str(self.temp_base_dir),
            'exists': self.temp_base_dir.exists(),
            'writable': False,
            'readable': False,
            'platform': platform.system(),
            'space_available': None,
            'errors': []
        }
        
        try:
            # Test write permissions
            test_file = self.temp_base_dir / "test_write.tmp"
            test_file.write_text("test")
            result['writable'] = True
            
            # Test read permissions
            content = test_file.read_text()
            result['readable'] = content == "test"
            
            # Clean up test file
            test_file.unlink()
            
            # Get available space (if possible)
            if hasattr(shutil, 'disk_usage'):
                usage = shutil.disk_usage(self.temp_base_dir)
                result['space_available'] = usage.free
            
        except Exception as e:
            result['errors'].append(str(e))
        
        return result
    
    def check_disk_space(self, estimated_size_bytes: int, num_concurrent: int = 1) -> Dict[str, Any]:
        """
        Check if sufficient disk space is available for processing.
        
        Based on analysis: peak usage = 2x output size per concurrent taxon
        - Temporary directory: 1x (rehydrated files)
        - Output directory: 1x (during atomic copy)
        
        Args:
            estimated_size_bytes: Estimated size of final output in bytes
            num_concurrent: Number of concurrent workers (default: 1)
            
        Returns:
            Dictionary with disk space check results
        """
        # Peak multiplier: 2x for single taxon (temp + output during copy)
        # For concurrent processing, multiply by number of workers
        peak_multiplier = 2.0 * num_concurrent
        required_bytes = int(estimated_size_bytes * peak_multiplier)
        
        # Add 20% safety margin
        required_with_margin = int(required_bytes * 1.2)
        
        result = {
            'estimated_output_size': estimated_size_bytes,
            'num_concurrent': num_concurrent,
            'peak_multiplier': peak_multiplier,
            'required_bytes': required_bytes,
            'required_with_margin': required_with_margin,
            'temp_dir_available': None,
            'output_dir_available': None,
            'temp_dir_sufficient': False,
            'output_dir_sufficient': False,
            'overall_sufficient': False,
            'warnings': []
        }
        
        try:
            # Check temporary directory space
            if self.temp_base_dir.exists():
                temp_usage = shutil.disk_usage(self.temp_base_dir)
                result['temp_dir_available'] = temp_usage.free
                result['temp_dir_sufficient'] = temp_usage.free >= required_with_margin
                
                if not result['temp_dir_sufficient']:
                    result['warnings'].append(
                        f"Temporary directory may not have enough space: "
                        f"{self._format_bytes(temp_usage.free)} available, "
                        f"{self._format_bytes(required_with_margin)} recommended"
                    )
            
            # Check output directory space
            output_dir = Path(self.config.output_dir)
            if output_dir.exists():
                output_usage = shutil.disk_usage(output_dir)
                result['output_dir_available'] = output_usage.free
                result['output_dir_sufficient'] = output_usage.free >= required_with_margin
                
                if not result['output_dir_sufficient']:
                    result['warnings'].append(
                        f"Output directory may not have enough space: "
                        f"{self._format_bytes(output_usage.free)} available, "
                        f"{self._format_bytes(required_with_margin)} recommended"
                    )
            
            # Check if temp and output are on same filesystem
            if result['temp_dir_available'] and result['output_dir_available']:
                # If on same filesystem, we need space for both simultaneously
                if self._same_filesystem(self.temp_base_dir, output_dir):
                    combined_required = required_with_margin
                    min_available = min(result['temp_dir_available'], result['output_dir_available'])
                    result['overall_sufficient'] = min_available >= combined_required
                    
                    if not result['overall_sufficient']:
                        result['warnings'].append(
                            "Temp and output directories are on the same filesystem. "
                            "Consider using --temp-dir to specify a different disk."
                        )
                else:
                    # Different filesystems - each needs its own space
                    result['overall_sufficient'] = (
                        result['temp_dir_sufficient'] and result['output_dir_sufficient']
                    )
            else:
                result['overall_sufficient'] = (
                    result.get('temp_dir_sufficient', True) and 
                    result.get('output_dir_sufficient', True)
                )
            
        except Exception as e:
            result['warnings'].append(f"Could not check disk space: {e}")
            # Assume sufficient if we can't check
            result['overall_sufficient'] = True
        
        return result
    
    def _same_filesystem(self, path1: Path, path2: Path) -> bool:
        """Check if two paths are on the same filesystem."""
        try:
            return path1.stat().st_dev == path2.stat().st_dev
        except Exception:
            return True  # Assume same filesystem if we can't determine
    
    def _format_bytes(self, bytes_value: int) -> str:
        """Format bytes as human-readable string."""
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if abs(bytes_value) < 1024.0:
                return f"{bytes_value:.1f} {unit}"
            bytes_value /= 1024.0
        return f"{bytes_value:.1f} PB"
    
    def get_processing_statistics(self) -> Dict[str, Any]:
        """
        Get processing statistics and configuration.
        
        Returns:
            Dictionary with processing statistics
        """
        return {
            'temp_directory': str(self.temp_base_dir),
            'platform': platform.system(),
            'max_atomic_retries': self.max_atomic_retries,
            'atomic_retry_base_delay': self.atomic_retry_base_delay,
            'atomic_retry_max_delay': self.atomic_retry_max_delay,
            'datasets_executable': self.config.datasets_executable,
            'rate_limit_per_second': self.config.rate_limit_per_second
        }
