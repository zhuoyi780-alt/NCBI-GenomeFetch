"""
File organization and MD5 handling for downloaded genome data.
"""

import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import List, Dict, Optional, Tuple, Sequence
from .logging_config import get_logger
from .file_type_utils import (
    detect_file_type,
    extract_accession_from_path,
    find_requested_data_files,
    get_expected_file_extensions,
    standardize_filename,
)


class FileOrganizer:
    """
    Handles file organization and MD5 checksum management for downloaded genome data.
    
    Uses cross-platform path operations with pathlib.Path for compatibility.
    """
    
    def __init__(self, output_dir: str):
        """
        Initialize FileOrganizer.
        
        Args:
            output_dir: Base output directory for organized files
        """
        self.output_dir = Path(output_dir)
        self.logger = get_logger("file_organizer")
        
        # Ensure output directory exists
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.logger.debug(f"FileOrganizer initialized with output directory: {self.output_dir}")
    
    def organize_taxon_files(
        self,
        source_dir: str,
        taxon: str,
        include_params: Optional[Sequence[str]] = None,
    ) -> int:
        """
        Organize files for a single taxon into the output directory structure.
        
        Args:
            source_dir: Source directory containing extracted files
            taxon: Taxon name for directory creation
            include_params: Requested NCBI datasets include types
            
        Returns:
            Number of files organized
        """
        source_path = Path(source_dir)
        if not source_path.exists():
            self.logger.error(f"Source directory does not exist: {source_path}")
            return 0
        
        # Create taxon directory
        taxon_dir = self._create_taxon_directory(taxon)
        if not taxon_dir:
            return 0
        
        files_organized = 0
        
        try:
            # Copy requested data files
            copied_count, filename_mapping = self._copy_requested_data_files(
                source_path,
                taxon_dir,
                include_params or ["genome"],
            )
            files_organized += copied_count
            
            # 添加同步点：确保所有文件操作完全完成
            self._ensure_files_synced(taxon_dir)
            
            # Copy and fix MD5 files - 现在安全执行
            self._copy_and_fix_md5_files(source_path, taxon_dir, filename_mapping)
            
            self.logger.info(f"Organized {files_organized} files for taxon '{taxon}' in {taxon_dir}")
            
        except Exception as e:
            self.logger.error(f"Error organizing files for taxon '{taxon}': {e}")
            # Clean up partial organization on error
            self._cleanup_directory(taxon_dir)
            return 0
        
        return files_organized
    
    def _create_taxon_directory(self, taxon: str) -> Optional[Path]:
        """
        Create directory for a taxon with sanitized name.
        
        Args:
            taxon: Original taxon name
            
        Returns:
            Path to created directory, or None if creation failed
        """
        sanitized_name = self._sanitize_taxon_name(taxon)
        taxon_dir = self.output_dir / sanitized_name
        
        try:
            taxon_dir.mkdir(parents=True, exist_ok=True)
            self.logger.debug(f"Created taxon directory: {taxon_dir}")
            return taxon_dir
        except Exception as e:
            self.logger.error(f"Failed to create taxon directory '{taxon_dir}': {e}")
            return None
    
    def _sanitize_taxon_name(self, taxon: str) -> str:
        """
        Sanitize taxon name for use as directory name.
        
        Args:
            taxon: Original taxon name
            
        Returns:
            Sanitized name safe for filesystem use
        """
        # Replace spaces with underscores
        sanitized = taxon.replace(' ', '_')
        
        # Remove or replace problematic characters
        # Keep alphanumeric, underscore, hyphen, dot, parentheses
        sanitized = re.sub(r'[^\w\-\.\(\)]', '_', sanitized)
        
        # Remove multiple consecutive underscores
        sanitized = re.sub(r'_+', '_', sanitized)
        
        # Remove leading/trailing underscores
        sanitized = sanitized.strip('_')
        
        # Ensure name is not empty
        if not sanitized:
            sanitized = "unknown_taxon"
        
        # Limit length to avoid filesystem issues
        if len(sanitized) > 200:
            sanitized = sanitized[:200].rstrip('_')
        
        self.logger.debug(f"Sanitized taxon name: '{taxon}' -> '{sanitized}'")
        return sanitized
    
    def _copy_fna_files(self, source_dir: Path, dest_dir: Path) -> int:
        """
        Copy FNA files from source to destination, preserving original names.
        
        Args:
            source_dir: Source directory to search for FNA files
            dest_dir: Destination directory
            
        Returns:
            Number of files copied
        """
        fna_files = []
        
        # Search for FNA files recursively
        for fna_file in source_dir.rglob("*.fna"):
            if fna_file.is_file():
                fna_files.append(fna_file)
        
        # Also search for .fa files (alternative extension)
        for fa_file in source_dir.rglob("*.fa"):
            if fa_file.is_file():
                fna_files.append(fa_file)
        
        if not fna_files:
            self.logger.warning(f"No FNA files found in {source_dir}")
            return 0
        
        files_copied = 0
        for fna_file in fna_files:
            try:
                dest_file = dest_dir / fna_file.name
                
                # Use atomic copy operation
                self._atomic_copy(fna_file, dest_file)
                files_copied += 1
                
                self.logger.debug(f"Copied FNA file: {fna_file.name}")
                
            except Exception as e:
                self.logger.error(f"Failed to copy FNA file '{fna_file}': {e}")
        
        self.logger.info(f"Copied {files_copied} FNA files to {dest_dir}")
        return files_copied

    def _copy_requested_data_files(
        self,
        source_dir: Path,
        dest_dir: Path,
        include_params: Sequence[str],
    ) -> Tuple[int, Dict[str, str]]:
        """
        Copy requested data files from source to destination using standardized names.

        Returns the number copied and an MD5 mapping keyed by normalized source paths.
        """
        data_files = find_requested_data_files(source_dir, include_params)
        if not data_files:
            expected_extensions = get_expected_file_extensions(list(include_params))
            self.logger.warning(
                f"No requested data files found in {source_dir} for include types "
                f"{','.join(include_params)} with extensions {expected_extensions}"
            )
            return 0, {}

        files_copied = 0
        filename_mapping: Dict[str, str] = {}
        destination_names = set()

        for data_file in data_files:
            try:
                accession = extract_accession_from_path(data_file)
                include_type = detect_file_type(data_file.name, include_params)
                if accession:
                    dest_name = standardize_filename(data_file, accession, include_type)
                else:
                    dest_name = data_file.name

                if dest_name in destination_names:
                    raise ValueError(f"Multiple source files map to output filename {dest_name}")
                destination_names.add(dest_name)

                dest_file = dest_dir / dest_name
                self._atomic_copy(data_file, dest_file)
                files_copied += 1

                rel_path = self._relative_posix(data_file, source_dir)
                filename_mapping[rel_path] = dest_name
                stripped = self._strip_ncbi_data_prefix(rel_path)
                filename_mapping[stripped] = dest_name

                self.logger.debug(f"Copied requested data file: {data_file} -> {dest_name}")

            except Exception as e:
                self.logger.error(f"Failed to copy requested data file '{data_file}': {e}")
                raise

        self.logger.info(f"Copied {files_copied} requested data files to {dest_dir}")
        return files_copied, filename_mapping
    
    def _copy_and_fix_md5_files(
        self,
        source_dir: Path,
        dest_dir: Path,
        filename_mapping: Optional[Dict[str, str]] = None,
    ) -> None:
        """
        Copy MD5 files and fix their paths for the new directory structure.
        
        Args:
            source_dir: Source directory to search for MD5 files
            dest_dir: Destination directory
            filename_mapping: Optional mapping of source paths to output filenames
        """
        md5_files = []
        
        # Search for MD5 files recursively
        for md5_file in source_dir.rglob("md5sum.txt"):
            if md5_file.is_file():
                md5_files.append(md5_file)
        
        # Also search for other common MD5 file names
        for pattern in ["*.md5", "checksums.txt", "md5checksums.txt"]:
            for md5_file in source_dir.rglob(pattern):
                if md5_file.is_file():
                    md5_files.append(md5_file)
        
        if not md5_files:
            self.logger.info(f"No MD5 files found in {source_dir}")
            return
        
        # Merge all MD5 files and fix paths with deduplication
        merged_md5_entries = {}  # filename -> (checksum, line)
        
        for md5_file in md5_files:
            try:
                content = self._read_and_fix_md5_file(md5_file, dest_dir, filename_mapping)
                if content:
                    # Process each line and deduplicate by filename
                    for line in content:
                        parts = line.split(None, 1)
                        if len(parts) == 2:
                            checksum, filepath = parts
                            
                            # Remove binary mode indicator if present
                            if filepath.startswith('*'):
                                filepath = filepath[1:]
                            
                            # Extract filename for deduplication key
                            filename = filepath.split('/')[-1]
                            
                            # Keep the entry (later entries override earlier ones)
                            merged_md5_entries[filename] = (checksum, line)
                            
                    self.logger.debug(f"Processed MD5 file: {md5_file}")
            except Exception as e:
                self.logger.error(f"Failed to process MD5 file '{md5_file}': {e}")
        
        if merged_md5_entries:
            # Convert deduplicated entries back to list
            merged_md5_content = [line for checksum, line in merged_md5_entries.values()]
            
            # Write merged MD5 file
            dest_md5_file = dest_dir / "md5sum.txt"
            try:
                self._write_md5_file(dest_md5_file, merged_md5_content)
                self.logger.info(f"Created merged MD5 file with {len(merged_md5_content)} unique entries: {dest_md5_file}")
            except Exception as e:
                self.logger.error(f"Failed to write merged MD5 file: {e}")
    
    def _read_and_fix_md5_file(self, md5_file: Path, dest_dir: Path, filename_mapping: Dict[str, str] = None) -> List[str]:
        """
        Read MD5 file and fix paths to work in the destination directory.
        
        CRITICAL: Always generates forward slashes (/) in MD5 files regardless of platform
        to ensure compatibility with standard md5sum tools (including Git Bash on Windows).
        
        Args:
            md5_file: Path to MD5 file to read
            dest_dir: Destination directory for path correction
            filename_mapping: Optional mapping of original → new filenames for MD5 path updates
            
        Returns:
            List of corrected MD5 lines with Unix-style paths
        """
        try:
            with open(md5_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()
        except UnicodeDecodeError:
            # Try with different encoding for legacy files
            try:
                with open(md5_file, 'r', encoding='latin-1') as f:
                    lines = f.readlines()
            except Exception as e:
                self.logger.error(f"Failed to read MD5 file with any encoding: {e}")
                return []
        
        corrected_lines = []
        
        for line_num, line in enumerate(lines, 1):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            
            try:
                corrected_line = self._process_md5_line(line, dest_dir, filename_mapping)
                if corrected_line:
                    corrected_lines.append(corrected_line)
            except Exception as e:
                self.logger.warning(f"Skipping invalid MD5 line {line_num} in {md5_file}: {e}")
                continue
        
        return corrected_lines
    
    def _process_md5_line(self, line: str, dest_dir: Path, filename_mapping: Dict[str, str] = None) -> Optional[str]:
        """
        Process a single MD5 line and correct its path.
        
        Handles various MD5 formats:
        - Standard: "hash  filename"
        - Binary mode: "hash *filename"  
        - BSD format: "MD5 (filename) = hash"
        
        Args:
            line: Original MD5 line
            dest_dir: Destination directory for path correction
            filename_mapping: Optional mapping of original → new filenames for MD5 path updates
            
        Returns:
            Corrected MD5 line with Unix-style path, or None if invalid
        """
        # Handle BSD format: MD5 (filename) = hash
        bsd_match = re.match(r'^MD5\s*\((.+?)\)\s*=\s*([a-fA-F0-9]{32})$', line)
        if bsd_match:
            filepath, hash_value = bsd_match.groups()
            corrected_path = self._fix_md5_path_comprehensive(filepath, dest_dir, filename_mapping)
            if corrected_path:
                return f"{hash_value}  {corrected_path}"
            return None
        
        # Handle standard format: "hash  filename" or "hash *filename"
        # Split on whitespace, but be careful with filenames containing spaces
        parts = re.split(r'\s+', line, maxsplit=1)
        if len(parts) != 2:
            # Try alternative parsing for lines with multiple spaces
            match = re.match(r'^([a-fA-F0-9]{32})\s+(\*?)(.+)$', line)
            if not match:
                raise ValueError(f"Invalid MD5 line format: {line}")
            hash_value, binary_flag, filepath = match.groups()
        else:
            hash_value, filepath = parts
            binary_flag = ''
        
        # Validate hash format
        if not re.match(r'^[a-fA-F0-9]{32}$', hash_value):
            raise ValueError(f"Invalid MD5 hash format: {hash_value}")
        
        # Remove binary mode indicator if present
        if filepath.startswith('*'):
            filepath = filepath[1:]
        
        # Fix the file path with comprehensive handling
        corrected_path = self._fix_md5_path_comprehensive(filepath, dest_dir, filename_mapping)
        if corrected_path:
            return f"{hash_value}  {corrected_path}"
        
        return None
    
    def _fix_md5_path_comprehensive(self, original_path: str, dest_dir: Path, filename_mapping: Dict[str, str] = None) -> Optional[str]:
        """
        Comprehensive MD5 path correction handling various NCBI formats.
        
        CRITICAL: Always returns forward slashes (/) regardless of platform
        to ensure compatibility with standard md5sum tools.
        
        Args:
            original_path: Original path from MD5 file
            dest_dir: Destination directory
            filename_mapping: Optional mapping of original → new filenames for MD5 path updates
            
        Returns:
            Corrected relative path with forward slashes, or None if file should be excluded
        """
        # Clean up the path
        path = original_path.strip().strip('"\'')  # Remove quotes
        
        # Convert backslashes to forward slashes for processing
        path = path.replace('\\', '/')
        
        filename = self._resolve_mapped_md5_filename(path, filename_mapping)
        if not filename:
            self.logger.debug(f"Excluding MD5 entry with no copied file mapping: {path}")
            return None
        if filename_mapping:
            return filename
        
        # Validate filename
        if not filename or filename in ['.', '..']:
            self.logger.warning(f"Invalid filename after path processing: '{filename}'")
            return None
        
        # Define file types that we expect to copy vs. metadata files we don't copy
        genome_file_extensions = {'.fna', '.fa', '.fasta', '.faa', '.gbff', '.gb', '.gff', '.gff3', '.gtf', '.cds'}
        metadata_files = {
            'assembly_data_report.jsonl',
            'dataset_catalog.json',
            'sequence_report.jsonl',
            'assembly_stats.txt'
        }
        
        # Check if this is a genome file we should have copied
        file_extension = Path(filename).suffix.lower()
        is_genome_file = file_extension in genome_file_extensions
        is_metadata_file = filename in metadata_files
        
        # Check if file exists in destination directory
        dest_file = dest_dir / filename
        file_exists = dest_file.exists()
        
        if file_exists:
            # File exists - include in MD5 without warning
            self.logger.debug(f"MD5 reference file found: {filename}")
            return filename
        elif is_genome_file and not filename_mapping:
            # Genome file should exist but doesn't - this might indicate a real issue
            # But don't generate warning during processing as it might be a timing issue
            # Include in MD5 file anyway as the file should be there
            self.logger.debug(f"MD5 reference for expected genome file: {filename}")
            return filename
        elif is_metadata_file:
            # Metadata file doesn't exist and we don't copy these - exclude from MD5
            self.logger.debug(f"Excluding metadata file from MD5: {filename}")
            return None
        else:
            self.logger.debug(f"Excluding MD5 reference for missing file: {filename}")
            return None

    def _relative_posix(self, file_path: Path, source_dir: Path) -> str:
        """Return a normalized source-relative path."""
        try:
            return file_path.relative_to(source_dir).as_posix()
        except ValueError:
            return file_path.as_posix()

    def _strip_ncbi_data_prefix(self, path: str) -> str:
        """Strip common NCBI dataset path prefixes while preserving accession directories."""
        normalized = path.strip().strip('"\'').replace('\\', '/')
        for prefix in [
            './ncbi_dataset/data/',
            'ncbi_dataset/data/',
            './data/',
            'data/',
            './ncbi_dataset/',
            'ncbi_dataset/',
            './',
            '/data/',
            '/ncbi_dataset/data/',
            '/ncbi_dataset/',
        ]:
            if normalized.startswith(prefix):
                return normalized[len(prefix):]
        return normalized.lstrip('/')

    def _resolve_mapped_md5_filename(
        self,
        original_path: str,
        filename_mapping: Optional[Dict[str, str]],
    ) -> Optional[str]:
        """Resolve an MD5 path using exact, prefix-stripped, and unique basename mapping."""
        normalized = original_path.strip().strip('"\'').replace('\\', '/')
        candidates = [
            normalized,
            normalized.lstrip('./'),
            self._strip_ncbi_data_prefix(normalized),
            Path(normalized).name,
        ]

        if filename_mapping:
            for candidate in candidates:
                if candidate in filename_mapping:
                    return filename_mapping[candidate]

            basename = Path(normalized).name
            basename_matches = {
                dest
                for source, dest in filename_mapping.items()
                if Path(source).name == basename
            }
            if len(basename_matches) == 1:
                return next(iter(basename_matches))
            return None

        stripped = self._strip_ncbi_data_prefix(normalized)
        return Path(stripped).name if stripped else None
    
    def _write_md5_file(self, md5_file: Path, lines: List[str]) -> None:
        """
        Write MD5 file with corrected paths using Unix-style forward slashes.
        
        CRITICAL: Always uses forward slashes (/) in generated MD5 files
        regardless of platform for compatibility with standard md5sum tools.
        
        Args:
            md5_file: Path to MD5 file to write
            lines: List of MD5 lines to write (should already have forward slashes)
        """
        # Use atomic write operation
        temp_file = md5_file.with_suffix('.tmp')
        
        try:
            with open(temp_file, 'w', encoding='utf-8', newline='\n') as f:  # Force Unix line endings
                f.write("# MD5 checksums generated by taxonomy-genome-downloader\n")
                f.write("# Compatible with standard md5sum tools on Windows and Linux\n")
                f.write("# Use 'md5sum -c md5sum.txt' to verify file integrity\n")
                f.write("#\n")
                
                for line in lines:
                    # Ensure line uses forward slashes (should already be the case)
                    unix_line = line.replace('\\', '/')
                    f.write(f"{unix_line}\n")
            
            # Atomic rename
            temp_file.replace(md5_file)
            
            self.logger.debug(f"Written MD5 file with {len(lines)} entries using Unix-style paths")
            
        except Exception as e:
            # Clean up temp file on error
            if temp_file.exists():
                temp_file.unlink()
            raise e
    
    def _atomic_copy(self, src: Path, dest: Path) -> None:
        """
        Perform atomic file copy operation.
        
        Args:
            src: Source file path
            dest: Destination file path
        """
        temp_dest = dest.with_suffix(dest.suffix + '.tmp')
        
        try:
            # Copy file to temporary location
            shutil.copy2(src, temp_dest)
            
            # Atomic rename
            temp_dest.replace(dest)
            
        except Exception as e:
            # Clean up temp file on error
            if temp_dest.exists():
                temp_dest.unlink()
            raise e
    
    def _cleanup_directory(self, directory: Path) -> None:
        """
        Clean up directory and its contents.
        
        Args:
            directory: Directory to clean up
        """
        try:
            if directory.exists():
                shutil.rmtree(directory)
                self.logger.debug(f"Cleaned up directory: {directory}")
        except Exception as e:
            self.logger.error(f"Failed to clean up directory '{directory}': {e}")
    
    def validate_organized_files(self, taxon: str) -> Dict[str, any]:
        """
        Validate that files are properly organized for a taxon.
        
        Args:
            taxon: Taxon name to validate
            
        Returns:
            Dictionary with validation results
        """
        sanitized_name = self._sanitize_taxon_name(taxon)
        taxon_dir = self.output_dir / sanitized_name
        
        result = {
            'taxon': taxon,
            'directory_exists': taxon_dir.exists(),
            'fna_files': [],
            'data_files': [],
            'files_by_type': {},
            'md5_file_exists': False,
            'md5_valid': False,
            'md5_uses_forward_slashes': False,
            'total_files': 0
        }
        
        if not taxon_dir.exists():
            return result
        
        # Count all organized data files
        fna_files = list(taxon_dir.glob("*.fna")) + list(taxon_dir.glob("*.fa"))
        result['fna_files'] = [f.name for f in fna_files]
        data_files = []
        files_by_type = {}
        for file_path in sorted(taxon_dir.iterdir(), key=lambda path: path.name):
            if not file_path.is_file() or file_path.name == "md5sum.txt":
                continue
            include_type = detect_file_type(file_path.name)
            if include_type:
                data_files.append(file_path.name)
                files_by_type.setdefault(include_type, []).append(file_path.name)

        result['data_files'] = data_files
        result['files_by_type'] = files_by_type
        result['total_files'] = len(data_files)
        
        # Check MD5 file
        md5_file = taxon_dir / "md5sum.txt"
        result['md5_file_exists'] = md5_file.exists()
        
        if md5_file.exists():
            try:
                # Validate MD5 file format and path standards
                with open(md5_file, 'r', encoding='utf-8') as f:
                    content = f.read()
                    lines = content.splitlines()
                
                valid_lines = 0
                uses_forward_slashes = True
                
                for line in lines:
                    line = line.strip()
                    if line and not line.startswith('#'):
                        # Check for valid MD5 format
                        parts = re.split(r'\s+', line, maxsplit=1)
                        if len(parts) == 2 and len(parts[0]) == 32:
                            valid_lines += 1
                            
                            # Check if paths use forward slashes (Unix standard)
                            filepath = parts[1]
                            if filepath.startswith('*'):
                                filepath = filepath[1:]
                            
                            if '\\' in filepath:
                                uses_forward_slashes = False
                                self.logger.warning(f"MD5 file contains backslashes: {filepath}")
                
                result['md5_valid'] = valid_lines > 0
                result['md5_uses_forward_slashes'] = uses_forward_slashes
                
            except Exception as e:
                self.logger.error(f"Error validating MD5 file for '{taxon}': {e}")
        
        return result
    
    def test_md5_compatibility(self, taxon: str) -> Dict[str, any]:
        """
        Test MD5 file compatibility with standard md5sum tools.
        
        Args:
            taxon: Taxon name to test
            
        Returns:
            Dictionary with compatibility test results
        """
        sanitized_name = self._sanitize_taxon_name(taxon)
        taxon_dir = self.output_dir / sanitized_name
        md5_file = taxon_dir / "md5sum.txt"
        
        result = {
            'taxon': taxon,
            'md5_file_exists': md5_file.exists(),
            'format_valid': False,
            'paths_unix_style': False,
            'files_referenced_exist': False,
            'compatible_with_md5sum': False
        }
        
        if not md5_file.exists():
            return result
        
        try:
            with open(md5_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            valid_entries = 0
            unix_style_paths = 0
            existing_files = 0
            total_entries = 0
            
            for line in lines:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                
                total_entries += 1
                
                # Parse MD5 line
                parts = re.split(r'\s+', line, maxsplit=1)
                if len(parts) == 2:
                    hash_value, filepath = parts
                    
                    # Validate hash format
                    if re.match(r'^[a-fA-F0-9]{32}$', hash_value):
                        valid_entries += 1
                        
                        # Remove binary mode indicator
                        if filepath.startswith('*'):
                            filepath = filepath[1:]
                        
                        # Check for Unix-style paths (forward slashes only)
                        if '\\' not in filepath:
                            unix_style_paths += 1
                        
                        # Check if referenced file exists
                        referenced_file = taxon_dir / filepath
                        if referenced_file.exists():
                            existing_files += 1
            
            result['format_valid'] = valid_entries == total_entries and total_entries > 0
            result['paths_unix_style'] = unix_style_paths == total_entries and total_entries > 0
            result['files_referenced_exist'] = existing_files == total_entries and total_entries > 0
            result['compatible_with_md5sum'] = (
                result['format_valid'] and 
                result['paths_unix_style'] and 
                result['files_referenced_exist']
            )
            
            self.logger.debug(f"MD5 compatibility test for '{taxon}': "
                            f"{valid_entries}/{total_entries} valid, "
                            f"{unix_style_paths}/{total_entries} unix-style, "
                            f"{existing_files}/{total_entries} files exist")
            
        except Exception as e:
            self.logger.error(f"Error testing MD5 compatibility for '{taxon}': {e}")
        
        return result
    
    def _ensure_files_synced(self, directory: Path) -> None:
        """
        确保目录中的所有文件操作完全同步到文件系统。
        
        Args:
            directory: 需要同步的目录
        """
        try:
            # 强制文件系统同步
            import os
            if hasattr(os, 'sync'):
                os.sync()  # Unix系统
            
            # 验证目录可访问性
            if directory.exists() and directory.is_dir():
                # 尝试列出目录内容以确保文件系统状态一致
                list(directory.iterdir())
                
            self.logger.debug(f"File system sync completed for directory: {directory}")
            
        except Exception as e:
            self.logger.warning(f"File system sync failed, but continuing: {e}")
