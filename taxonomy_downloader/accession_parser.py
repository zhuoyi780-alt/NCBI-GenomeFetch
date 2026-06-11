"""
Accession file parser for the accession download feature.

This module provides functionality to parse accession list files,
handling deduplication, whitespace, and validation.
"""

import logging
from pathlib import Path
from typing import List, Tuple

logger = logging.getLogger("taxonomy_downloader.accession_parser")


def load_accessions(filepath: str) -> Tuple[List[str], int]:
    """
    Load accession numbers from a text file.
    
    Reads one accession per line, skipping empty and whitespace-only lines.
    Deduplicates accessions while preserving the order of first occurrence.
    
    Args:
        filepath: Path to the accession list file
        
    Returns:
        Tuple of (list of unique accession numbers, count of duplicates removed)
        
    Raises:
        FileNotFoundError: If the file does not exist
        ValueError: If the file is empty or contains no valid accessions
        
    Requirements: 2.1, 2.2, 2.3, 2.4, 2.5
    """
    file_path = Path(filepath)
    
    # Check if file exists
    if not file_path.exists():
        raise FileNotFoundError(f"Accession file not found: {filepath}")
    
    # Read file with UTF-8 encoding
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
    except Exception as e:
        logger.error(f"Failed to read accession file {filepath}: {e}")
        raise ValueError(f"Failed to read accession file: {e}")
    
    # Process lines: strip whitespace and skip empty lines
    accessions = []
    seen = set()
    duplicate_count = 0
    
    for line in lines:
        # Strip leading/trailing whitespace
        accession = line.strip()
        
        # Skip empty or whitespace-only lines
        if not accession:
            continue
        
        # Check for duplicates
        if accession in seen:
            duplicate_count += 1
            continue
        
        # Add to results
        seen.add(accession)
        accessions.append(accession)
    
    # Log deduplication info if duplicates were found
    if duplicate_count > 0:
        logger.info(f"Removed {duplicate_count} duplicate accession(s) from input")
    
    # Validate that we have at least one accession
    if not accessions:
        raise ValueError(f"No valid accessions found in {filepath}")
    
    logger.info(f"Loaded {len(accessions)} unique accession(s) from {filepath}")
    return accessions, duplicate_count
