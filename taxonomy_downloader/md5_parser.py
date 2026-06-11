"""
MD5 file parser for md5sum.txt files.

This module provides functionality to parse md5sum.txt files in various formats,
including standard format, binary mode format, and handling of comments and empty lines.
"""

import re
import logging
from pathlib import Path
from typing import List, Optional

from taxonomy_downloader.md5_models import MD5Entry


logger = logging.getLogger(__name__)


class MD5ParseError(Exception):
    """Exception raised when md5sum.txt file cannot be parsed."""
    pass


class MD5Parser:
    """Parses md5sum.txt files in various formats.
    
    Supports:
    - Standard format: "hash  filename"
    - Binary mode format: "hash *filename"
    - Comment lines starting with '#'
    - Empty lines
    
    Examples:
        >>> parser = MD5Parser()
        >>> entries = parser.parse_md5_file(Path("md5sum.txt"))
        >>> for entry in entries:
        ...     print(f"{entry.hash_value} {entry.file_path}")
    """
    
    # Regex pattern for parsing MD5 lines
    # Matches: hash (32 hex chars) + whitespace + optional '*' + filename
    MD5_LINE_PATTERN = re.compile(
        r'^([0-9a-fA-F]{32})\s+\*?(.+)$'
    )
    
    def __init__(self):
        """Initialize MD5 parser."""
        pass
    
    def parse_md5_file(self, md5_file: Path) -> List[MD5Entry]:
        """
        Parse md5sum.txt file and extract entries.
        
        Args:
            md5_file: Path to md5sum.txt file
            
        Returns:
            List of MD5Entry objects
            
        Raises:
            MD5ParseError: If file cannot be read or parsed
            FileNotFoundError: If md5_file does not exist
        
        Examples:
            >>> parser = MD5Parser()
            >>> entries = parser.parse_md5_file(Path("md5sum.txt"))
            >>> len(entries) > 0
            True
        """
        if not md5_file.exists():
            raise FileNotFoundError(f"MD5 file not found: {md5_file}")
        
        if not md5_file.is_file():
            raise MD5ParseError(f"Path is not a file: {md5_file}")
        
        try:
            with open(md5_file, 'r', encoding='utf-8') as f:
                lines = f.readlines()
        except PermissionError as e:
            raise MD5ParseError(f"Permission denied reading {md5_file}: {e}")
        except IOError as e:
            raise MD5ParseError(f"Error reading {md5_file}: {e}")
        
        entries = []
        for line_num, line in enumerate(lines, start=1):
            entry = self._parse_line(line, md5_file, line_num)
            if entry is not None:
                entries.append(entry)
        
        logger.info(f"Parsed {len(entries)} entries from {md5_file}")
        return entries
    
    def _parse_line(self, line: str, md5_file: Path, line_num: int) -> Optional[MD5Entry]:
        """
        Parse a single line from md5sum.txt.
        
        Supports formats:
        - Standard: "hash  filename"
        - Binary mode: "hash *filename"
        - Comments: "# comment"
        - Empty lines
        
        Args:
            line: Line from md5sum.txt
            md5_file: Path to md5sum.txt file (for path resolution)
            line_num: Line number (for error reporting)
            
        Returns:
            MD5Entry or None if line should be skipped
        
        Examples:
            >>> parser = MD5Parser()
            >>> entry = parser._parse_line(
            ...     "d41d8cd98f00b204e9800998ecf8427e  empty.txt",
            ...     Path("md5sum.txt"),
            ...     1
            ... )
            >>> entry.hash_value
            'd41d8cd98f00b204e9800998ecf8427e'
        """
        # Strip whitespace
        line = line.strip()
        
        # Skip empty lines
        if not line:
            return None
        
        # Skip comment lines
        if line.startswith('#'):
            return None
        
        # Try to match MD5 line pattern
        match = self.MD5_LINE_PATTERN.match(line)
        if not match:
            logger.warning(
                f"Invalid format in {md5_file} at line {line_num}: {line[:50]}"
            )
            return None
        
        hash_value = match.group(1).lower()  # Normalize to lowercase
        file_path = match.group(2).strip()
        
        # Resolve absolute path
        absolute_path = self._resolve_file_path(file_path, md5_file)
        
        return MD5Entry(
            hash_value=hash_value,
            file_path=file_path,
            absolute_path=absolute_path
        )
    
    def _resolve_file_path(self, filepath: str, md5_file: Path) -> Path:
        """
        Resolve file path relative to md5sum.txt location.
        
        Handles:
        - Relative paths (resolved relative to md5sum.txt directory)
        - Forward slashes (converted to platform-appropriate separators)
        - Cross-platform compatibility
        
        Args:
            filepath: File path from md5sum.txt entry
            md5_file: Path to md5sum.txt file
            
        Returns:
            Absolute path to the file
        
        Examples:
            >>> parser = MD5Parser()
            >>> path = parser._resolve_file_path(
            ...     "genome.fna",
            ...     Path("/data/md5sum.txt")
            ... )
            >>> path
            PosixPath('/data/genome.fna')
        """
        # Get directory containing md5sum.txt
        md5_dir = md5_file.parent
        
        # Convert forward slashes to platform-appropriate separators
        # pathlib.Path handles this automatically
        relative_path = Path(filepath)
        
        # Resolve relative to md5sum.txt directory
        absolute_path = (md5_dir / relative_path).resolve()
        
        return absolute_path
