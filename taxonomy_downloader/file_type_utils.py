"""
Shared utilities for file type detection and standardization.

This module provides centralized logic for handling different file types
in the taxonomy downloader, including extension mapping, file type detection,
and filename standardization. This ensures consistency between Taxon and
Accession modes (DRY principle).
"""

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence
import re


# Extension mapping for all supported include types
# Maps NCBI datasets include parameter values to their file extensions
EXTENSION_MAP = {
    'genome': ['.fna', '.fa', '.fasta'],
    'protein': ['.faa'],
    'rna': ['.fna', '.fa'],
    'cds': ['.fna', '.fa'],
    'gff3': ['.gff', '.gff3'],
    'gtf': ['.gtf'],
    'gbff': ['.gbff', '.gb'],
    'seq-report': ['.jsonl'],
}

# Output extension normalization
# Maps variant extensions to their standardized form
OUTPUT_EXTENSION_MAP = {
    '.fa': '.fna',
    '.fasta': '.fna',
    '.gff3': '.gff',
    '.gb': '.gbff',
}

VALID_INCLUDE_TYPES = tuple(EXTENSION_MAP.keys())

METADATA_FILENAMES = {
    'assembly_data_report.jsonl',
    'dataset_catalog.json',
    'assembly_stats.txt',
}


@dataclass(frozen=True)
class FileTypeSpec:
    """File matching and output naming rules for one include type."""

    include_type: str
    extensions: tuple[str, ...]


def normalize_include_params(include_params: Optional[Sequence[str]]) -> List[str]:
    """Normalize include parameters, defaulting to genome."""
    if not include_params or 'none' in include_params:
        return ['genome']

    normalized = []
    for include_type in include_params:
        if include_type in EXTENSION_MAP and include_type not in normalized:
            normalized.append(include_type)

    return normalized or ['genome']


def iter_file_specs(include_params: Optional[Sequence[str]]) -> List[FileTypeSpec]:
    """Return file type specs for requested include parameters."""
    return [
        FileTypeSpec(include_type=include_type, extensions=tuple(EXTENSION_MAP[include_type]))
        for include_type in normalize_include_params(include_params)
    ]


def get_expected_file_extensions(include_params: List[str]) -> List[str]:
    """
    Get expected file extensions for searching based on include_params.
    
    This function maps NCBI datasets include parameter values to the actual
    file extensions that should be searched for in the downloaded data.
    
    Args:
        include_params: List of include types (e.g., ['genome', 'protein'])
    
    Returns:
        Sorted list of unique file extensions (with dots) to search for
    
    Examples:
        >>> get_expected_file_extensions(['genome', 'protein'])
        ['.fa', '.faa', '.fasta', '.fna']
        
        >>> get_expected_file_extensions(['gff3', 'gtf'])
        ['.gff', '.gff3', '.gtf']
        
        >>> get_expected_file_extensions([])
        ['.fa', '.fasta', '.fna']
        
        >>> get_expected_file_extensions(['none'])
        ['.fa', '.fasta', '.fna']
    """
    extensions = set()
    for param in include_params or []:
        if param in EXTENSION_MAP:
            extensions.update(EXTENSION_MAP[param])
    
    # Default to genome if empty or 'none' specified
    if not extensions or 'none' in (include_params or []):
        extensions.update(EXTENSION_MAP['genome'])
    
    return sorted(extensions)


def detect_file_type(filename: str, allowed_types: Optional[Sequence[str]] = None) -> Optional[str]:
    """
    Detect include type from filename pattern.
    
    This function analyzes the filename to determine which NCBI datasets
    include type it corresponds to. This is useful for determining the
    correct output extension during file standardization.
    
    Args:
        filename: Name of the file (not full path)
        allowed_types: Optional include types to consider
    
    Returns:
        Include type string or None if not recognized
    
    Examples:
        >>> detect_file_type('protein.faa')
        'protein'
        
        >>> detect_file_type('cds_from_genomic.fna')
        'cds'
        
        >>> detect_file_type('genomic.gff')
        'gff3'
        
        >>> detect_file_type('genomic.gtf')
        'gtf'
        
        >>> detect_file_type('genomic.gbff')
        'gbff'
        
        >>> detect_file_type('sequence_report.jsonl')
        'seq-report'
        
        >>> detect_file_type('GCF_001267435.1_ASM126743v1_genomic.fna')
        'genome'
        
        >>> detect_file_type('rna_from_genomic.fna')
        'rna'
    """
    filename_lower = Path(filename).name.lower()
    allowed = set(normalize_include_params(allowed_types)) if allowed_types is not None else None

    def allowed_or_none(include_type: str) -> Optional[str]:
        if allowed is None or include_type in allowed:
            return include_type
        return None

    if filename_lower in METADATA_FILENAMES:
        return None
    
    # Pattern-based detection (order matters - most specific first)
    if filename_lower.endswith('.faa'):
        return allowed_or_none('protein')
    elif 'cds' in filename_lower or filename_lower.endswith('.cds'):
        return allowed_or_none('cds')
    elif 'rna' in filename_lower:
        return allowed_or_none('rna')
    elif filename_lower.endswith(('.gff', '.gff3')):
        return allowed_or_none('gff3')
    elif filename_lower.endswith('.gtf'):
        return allowed_or_none('gtf')
    elif filename_lower.endswith(('.gbff', '.gb')):
        return allowed_or_none('gbff')
    elif filename_lower.endswith('.jsonl') or 'sequence_report' in filename_lower:
        return allowed_or_none('seq-report')
    elif filename_lower.endswith(('.fna', '.fa', '.fasta')):
        return allowed_or_none('genome')
    
    return None


def should_include_file(file_path: Path, include_params: Optional[Sequence[str]]) -> bool:
    """Return True if a path is a requested data file."""
    if file_path.exists() and not file_path.is_file():
        return False
    return detect_file_type(file_path.name, include_params) is not None


def find_requested_data_files(root_dir: Path, include_params: Optional[Sequence[str]]) -> List[Path]:
    """Recursively find requested data files under a root directory."""
    root_path = Path(root_dir)
    data_files = []
    seen = set()
    for spec in iter_file_specs(include_params):
        for extension in spec.extensions:
            for path in sorted(root_path.rglob(f"*{extension}"), key=lambda p: p.as_posix()):
                if path in seen:
                    continue
                if should_include_file(path, [spec.include_type]):
                    data_files.append(path)
                    seen.add(path)
    return data_files


def get_output_extension(file_path: Path, include_type: Optional[str] = None) -> str:
    """
    Get standardized output extension for a file.
    
    This function determines the appropriate standardized extension for a file
    based on its current extension and optionally its detected include type.
    Special handling is provided for CDS files which use .cds extension.
    
    Args:
        file_path: Original file path
        include_type: Optional include type hint (e.g., 'cds', 'protein')
    
    Returns:
        Standardized extension (e.g., '.fna', '.faa', '.cds')
    
    Examples:
        >>> get_output_extension(Path('protein.faa'))
        '.faa'
        
        >>> get_output_extension(Path('cds_from_genomic.fna'), 'cds')
        '.cds'
        
        >>> get_output_extension(Path('genomic.gff3'))
        '.gff'
        
        >>> get_output_extension(Path('genome.fa'))
        '.fna'
        
        >>> get_output_extension(Path('genomic.gb'))
        '.gbff'
        
        >>> get_output_extension(Path('sequence_report.jsonl'))
        '.jsonl'
    """
    filename = file_path.name.lower()
    current_ext = file_path.suffix.lower()
    
    # Special case: CDS files should use .cds extension
    if include_type == 'cds' or 'cds' in filename:
        return '.cds'

    if include_type == 'rna' or 'rna' in filename:
        return '.rna.fna'
    
    # Normalize extensions using the mapping
    if current_ext in OUTPUT_EXTENSION_MAP:
        return OUTPUT_EXTENSION_MAP[current_ext]
    
    # Return as-is if already standard
    return current_ext


def extract_accession_from_path(file_path: Path) -> Optional[str]:
    """
    Extract accession (GCF_* or GCA_*) from file path.
    
    This function searches through the file path (including all parent
    directories) to find an NCBI accession number. Accessions follow
    the pattern GCF_NNNNNN.N or GCA_NNNNNN.N.
    
    Args:
        file_path: Path to file (can be relative or absolute)
    
    Returns:
        Accession string or None if not found
    
    Examples:
        >>> extract_accession_from_path(Path('/data/GCF_001267435.1/protein.faa'))
        'GCF_001267435.1'
        
        >>> extract_accession_from_path(Path('ncbi_dataset/data/GCA_000001405.28/genomic.fna'))
        'GCA_000001405.28'
        
        >>> extract_accession_from_path(Path('GCF_001267435.1_ASM126743v1_genomic.fna'))
        'GCF_001267435.1'
        
        >>> extract_accession_from_path(Path('/some/path/without/accession/file.fna'))
        None
    """
    accession_pattern = re.compile(r'(GC[AF]_\d+\.\d+)')
    
    # Check all parent directories
    for part in file_path.parts:
        match = accession_pattern.search(part)
        if match:
            return match.group(1)
    
    # Also check the filename itself (for files like GCF_001267435.1_ASM126743v1_genomic.fna)
    match = accession_pattern.search(file_path.name)
    if match:
        return match.group(1)
    
    return None


def standardize_filename(
    file_path: Path,
    accession: Optional[str] = None,
    include_type: Optional[str] = None,
) -> str:
    """
    Standardize filename to {accession}.{extension} format.
    
    This function converts any filename to the standardized format of
    {accession}.{extension}, where the extension is normalized according
    to the file type. This ensures consistent naming across all downloaded
    files.
    
    Args:
        file_path: Original file path
        accession: Optional accession (will be extracted from path if not provided)
        include_type: Optional include type hint
    
    Returns:
        Standardized filename in format {accession}.{extension}
    
    Raises:
        ValueError: If accession cannot be extracted from path and not provided
    
    Examples:
        >>> standardize_filename(Path('/data/GCF_001267435.1/protein.faa'))
        'GCF_001267435.1.faa'
        
        >>> standardize_filename(Path('/data/GCF_001267435.1/cds_from_genomic.fna'))
        'GCF_001267435.1.cds'
        
        >>> standardize_filename(Path('/data/GCF_001267435.1/GCF_001267435.1_ASM126743v1_genomic.fna'))
        'GCF_001267435.1.fna'
        
        >>> standardize_filename(Path('/data/GCF_001267435.1/genomic.gff'))
        'GCF_001267435.1.gff'
        
        >>> standardize_filename(Path('/data/GCF_001267435.1/genomic.gff3'))
        'GCF_001267435.1.gff'
        
        >>> standardize_filename(Path('protein.faa'), 'GCA_000001405.28')
        'GCA_000001405.28.faa'
    """
    if not accession:
        accession = extract_accession_from_path(file_path)
    
    if not accession:
        raise ValueError(f"Cannot extract accession from path: {file_path}")
    
    # Detect file type and get output extension
    include_type = include_type or detect_file_type(file_path.name)
    output_ext = get_output_extension(file_path, include_type)
    
    return f"{accession}{output_ext}"


def expected_output_filenames_for_accession(
    accession: str,
    include_params: Optional[Sequence[str]],
) -> List[str]:
    """Return possible standardized output filenames for one accession."""
    extensions_by_type = {
        'genome': ['.fna'],
        'protein': ['.faa'],
        'rna': ['.rna.fna'],
        'cds': ['.cds'],
        'gff3': ['.gff'],
        'gtf': ['.gtf'],
        'gbff': ['.gbff'],
        'seq-report': ['.jsonl'],
    }
    filenames = []
    for include_type in normalize_include_params(include_params):
        for extension in extensions_by_type[include_type]:
            filenames.append(f"{accession}{extension}")
    return filenames
