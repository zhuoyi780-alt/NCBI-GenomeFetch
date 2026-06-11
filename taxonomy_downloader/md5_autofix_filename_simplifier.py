"""
Filename simplifier for MD5 verification auto-fix functionality.

This module provides functionality to simplify NCBI genome filenames to the format:
{Accession_ID}{Extension}

For example:
- Input: "GCA_000837045.1_ViralProj14067_genomic.fna"
- Output: "GCA_000837045.1.fna"
"""

import re
import logging
from pathlib import Path
from typing import Optional, Set

logger = logging.getLogger(__name__)


class FilenameSimplifier:
    """
    Simplifies NCBI genome filenames to Accession_ID + Extension format.
    
    This class applies the File_Organizer's filename simplification rules,
    ensuring that redownloaded files have consistent naming with existing files.
    
    Supported Extensions:
        .fna, .fa, .fasta, .gbff, .gff, .gtf, .txt, .faa, .tar.gz, .gz
    
    Examples:
        >>> simplifier = FilenameSimplifier()
        >>> simplifier.simplify_filename("GCA_000837045.1_ViralProj14067_genomic.fna")
        'GCA_000837045.1.fna'
        >>> simplifier.simplify_filename("GCF_000001405.40_GRCh38.p14_genomic.gbff")
        'GCF_000001405.40.gbff'
    """
    
    # NCBI Accession ID pattern: GCA_ or GCF_ followed by 9 digits, a dot, and version number
    ACCESSION_PATTERN = re.compile(r'(GC[FA]_\d{9}\.\d+)', re.IGNORECASE)
    
    # Supported file extensions for genome files
    SUPPORTED_EXTENSIONS: Set[str] = {
        '.fna', '.fa', '.fasta',  # FASTA nucleotide
        '.faa',                    # FASTA amino acid
        '.gbff', '.gb', '.gbk',    # GenBank
        '.gff', '.gff3',           # GFF
        '.gtf',                    # GTF
        '.txt',                    # Text files
        '.tar.gz', '.gz',          # Compressed files
    }
    
    @staticmethod
    def simplify_filename(original_filename: str) -> str:
        """
        Simplify a filename to Accession_ID + Extension format.
        
        This method extracts the Accession ID and file extension from the original
        filename and combines them to create a simplified filename.
        
        Args:
            original_filename: Original NCBI filename (e.g., "GCA_000837045.1_ViralProj14067_genomic.fna")
        
        Returns:
            Simplified filename (e.g., "GCA_000837045.1.fna")
            If no Accession ID is found, returns the original filename and logs a warning.
        
        Examples:
            >>> FilenameSimplifier.simplify_filename("GCA_000837045.1_ViralProj14067_genomic.fna")
            'GCA_000837045.1.fna'
            >>> FilenameSimplifier.simplify_filename("GCF_000001405.40_GRCh38.p14_genomic.gbff")
            'GCF_000001405.40.gbff'
            >>> FilenameSimplifier.simplify_filename("unknown_file.fna")
            'unknown_file.fna'
        """
        # Extract Accession ID
        accession_id = FilenameSimplifier._extract_accession_from_filename(original_filename)
        
        if not accession_id:
            logger.warning(
                f"No recognizable Accession ID found in filename: {original_filename}. "
                "Keeping original filename."
            )
            return original_filename
        
        # Extract extension
        extension = FilenameSimplifier._extract_extension(original_filename)
        
        # Generate simplified filename
        simplified = f"{accession_id}{extension}"
        
        logger.debug(f"Simplified filename: {original_filename} -> {simplified}")
        
        return simplified
    
    @staticmethod
    def _extract_accession_from_filename(filename: str) -> Optional[str]:
        """
        Extract Accession ID from a filename.
        
        Searches for NCBI Accession ID pattern (GCA_XXXXXXXXX.XX or GCF_XXXXXXXXX.XX)
        in the filename.
        
        Args:
            filename: Filename to extract from
        
        Returns:
            Accession ID if found, None otherwise
        
        Examples:
            >>> FilenameSimplifier._extract_accession_from_filename("GCA_000837045.1_ViralProj14067_genomic.fna")
            'GCA_000837045.1'
            >>> FilenameSimplifier._extract_accession_from_filename("unknown_file.fna")
            None
        """
        match = FilenameSimplifier.ACCESSION_PATTERN.search(filename)
        if match:
            return match.group(1)
        return None
    
    @staticmethod
    def _extract_extension(filename: str) -> str:
        """
        Extract file extension from a filename.
        
        Handles both single extensions (.fna) and multi-part extensions (.tar.gz).
        
        Args:
            filename: Filename to extract extension from
        
        Returns:
            File extension including the dot (e.g., ".fna", ".tar.gz")
            Returns empty string if no extension is found.
        
        Examples:
            >>> FilenameSimplifier._extract_extension("file.fna")
            '.fna'
            >>> FilenameSimplifier._extract_extension("file.tar.gz")
            '.tar.gz'
            >>> FilenameSimplifier._extract_extension("file.genomic.fna")
            '.fna'
            >>> FilenameSimplifier._extract_extension("file")
            ''
        """
        path = Path(filename)
        
        # Check for multi-part extensions like .tar.gz
        if len(path.suffixes) >= 2:
            # Check if the last two suffixes form a known multi-part extension
            multi_ext = ''.join(path.suffixes[-2:])
            if multi_ext.lower() in FilenameSimplifier.SUPPORTED_EXTENSIONS:
                return multi_ext
        
        # Return the last suffix (single extension)
        if path.suffix:
            return path.suffix
        
        return ''
