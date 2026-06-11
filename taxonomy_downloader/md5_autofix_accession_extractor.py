"""
Accession ID extractor for MD5 verification auto-fix functionality.

This module implements the AccessionExtractor class that extracts NCBI Accession IDs
from file paths and filenames using regex pattern matching. It supports both GCF and
GCA formats and provides deduplication of extracted IDs.
"""

import logging
import re
from pathlib import Path
from typing import List, Optional, Set

from taxonomy_downloader.md5_autofix_models import FailedFile

logger = logging.getLogger(__name__)


class AccessionExtractor:
    """
    Extracts NCBI Accession IDs from file paths and filenames.
    
    This class uses regex pattern matching to identify and extract Accession IDs
    in the standard NCBI format (GCF_XXXXXXXXX.XX or GCA_XXXXXXXXX.XX) from file
    paths or filenames. It supports deduplication and handles invalid paths with
    appropriate warnings.
    
    Pattern Format:
        - GCF_XXXXXXXXX.XX: RefSeq genome assemblies
        - GCA_XXXXXXXXX.XX: GenBank genome assemblies
        - Where X is a digit (9 digits total) and XX is the version number
    
    Attributes:
        ACCESSION_PATTERN: Compiled regex pattern for matching Accession IDs
    
    Examples:
        >>> extractor = AccessionExtractor()
        >>> accession = extractor._extract_from_path("test_output/2162/GCA_000302455.1.fna")
        >>> accession
        'GCA_000302455.1'
        >>> accession = extractor._extract_from_path("GCF_000001405.40_genomic.fna")
        >>> accession
        'GCF_000001405.40'
        >>> accession = extractor._extract_from_path("invalid_file.txt")
        >>> accession is None
        True
    """
    
    # Regex pattern for NCBI Accession IDs
    # Format: GC[FA]_XXXXXXXXX.XX where X is a digit
    # Examples: GCA_000302455.1, GCF_000001405.40
    ACCESSION_PATTERN = re.compile(r'(GC[FA]_\d{9}\.\d+)', re.IGNORECASE)
    
    def __init__(self):
        """
        Initialize the AccessionExtractor.
        
        Examples:
            >>> extractor = AccessionExtractor()
            >>> extractor.ACCESSION_PATTERN is not None
            True
        """
        logger.info("Initialized AccessionExtractor")
    
    def extract_accessions(self, failed_files: List[FailedFile]) -> List[str]:
        """
        Extract Accession IDs from a list of failed files.
        
        This method processes each failed file, attempts to extract an Accession ID
        from its path or filename, and returns a deduplicated list of all successfully
        extracted IDs. Files without valid Accession IDs are logged as warnings and
        skipped.
        
        The method updates each FailedFile object's accession_id field if extraction
        is successful, allowing the file to be marked as redownloadable.
        
        Args:
            failed_files: List of FailedFile objects to process
        
        Returns:
            Deduplicated list of extracted Accession IDs
        
        Examples:
            >>> from taxonomy_downloader.md5_autofix_models import FailedFile
            >>> from taxonomy_downloader.md5_models import VerificationStatus
            >>> from pathlib import Path
            >>> extractor = AccessionExtractor()
            >>> failed_files = [
            ...     FailedFile("GCA_000302455.1.fna", Path("."), Path("GCA_000302455.1.fna"),
            ...                VerificationStatus.FAIL, "abc123"),
            ...     FailedFile("GCF_000001405.40.fna", Path("."), Path("GCF_000001405.40.fna"),
            ...                VerificationStatus.FAIL, "def456"),
            ...     FailedFile("GCA_000302455.1.gbff", Path("."), Path("GCA_000302455.1.gbff"),
            ...                VerificationStatus.FAIL, "ghi789")
            ... ]
            >>> accessions = extractor.extract_accessions(failed_files)
            >>> len(accessions)
            2
            >>> 'GCA_000302455.1' in accessions
            True
            >>> 'GCF_000001405.40' in accessions
            True
        """
        # Use a set to automatically deduplicate Accession IDs
        # Multiple files may have the same Accession ID (e.g., .fna and .gbff)
        accessions: Set[str] = set()
        skipped_count = 0
        
        for failed_file in failed_files:
            # Two-stage extraction strategy:
            # 1. Try full path first (e.g., "2162/GCA_000302455.1.fna")
            # 2. Fall back to just the filename (e.g., "GCA_000302455.1.fna")
            accession = self._extract_from_path(str(failed_file.original_path))
            
            if accession is None:
                # Try extracting from just the filename as fallback
                accession = self._extract_from_path(failed_file.md5_file_path)
            
            if accession:
                # Update the FailedFile object with the extracted Accession ID
                # This marks the file as redownloadable (can_redownload() returns True)
                failed_file.accession_id = accession
                accessions.add(accession)
                logger.debug(
                    f"Extracted Accession ID '{accession}' from {failed_file.original_path}"
                )
            else:
                # Log warning for files without valid Accession IDs
                # These files will be skipped in the redownload process
                logger.warning(
                    f"Could not extract valid Accession ID from {failed_file.original_path}, "
                    f"file will be skipped"
                )
                skipped_count += 1
        
        # Convert set to sorted list for consistent ordering
        accession_list = sorted(list(accessions))
        
        logger.info(
            f"Extracted {len(accession_list)} unique Accession IDs from "
            f"{len(failed_files)} failed files ({skipped_count} skipped)"
        )
        
        return accession_list
    
    def _extract_from_path(self, file_path: str) -> Optional[str]:
        """
        Extract Accession ID from a file path or filename.
        
        This method uses regex pattern matching to find the first occurrence of
        a valid NCBI Accession ID in the provided path or filename. The pattern
        matches both GCF (RefSeq) and GCA (GenBank) formats.
        
        The method is case-insensitive and will match Accession IDs anywhere in
        the path string.
        
        Args:
            file_path: File path or filename to extract from
        
        Returns:
            Extracted Accession ID if found, None otherwise
        
        Examples:
            >>> extractor = AccessionExtractor()
            >>> extractor._extract_from_path("test_output/2162/GCA_000302455.1.fna")
            'GCA_000302455.1'
            >>> extractor._extract_from_path("GCF_000001405.40_genomic.fna")
            'GCF_000001405.40'
            >>> extractor._extract_from_path("GCA_000837045.1_ViralProj14067_genomic.fna")
            'GCA_000837045.1'
            >>> extractor._extract_from_path("invalid_file.txt") is None
            True
            >>> extractor._extract_from_path("") is None
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
    
    def save_to_file(self, accessions: List[str], output_path: Path) -> None:
        """
        Save Accession IDs to a text file.
        
        This method writes the list of Accession IDs to a file, with one ID per line.
        The file is created at the specified output path. If the list is empty, no
        file is created.
        
        According to requirement 3.3, when there are no failed files, the system
        should not create the failed_accessions.txt file.
        
        Args:
            accessions: List of Accession IDs to save
            output_path: Path where the file should be created
        
        Raises:
            IOError: If file writing fails
        
        Examples:
            >>> import tempfile
            >>> extractor = AccessionExtractor()
            >>> with tempfile.TemporaryDirectory() as tmpdir:
            ...     output_file = Path(tmpdir) / "failed_accessions.txt"
            ...     extractor.save_to_file(["GCA_000302455.1", "GCF_000001405.40"], output_file)
            ...     content = output_file.read_text()
            ...     lines = content.strip().split('\\n')
            ...     len(lines)
            2
        """
        if not accessions:
            logger.info("No Accession IDs to save, skipping file creation")
            return
        
        try:
            # Ensure parent directory exists
            output_path.parent.mkdir(parents=True, exist_ok=True)
            
            # Write one Accession ID per line
            with open(output_path, 'w', encoding='utf-8') as f:
                for accession in accessions:
                    f.write(f"{accession}\n")
            
            logger.info(
                f"Saved {len(accessions)} Accession IDs to {output_path}"
            )
        
        except IOError as e:
            logger.error(f"Failed to write Accession list to {output_path}: {e}")
            raise
