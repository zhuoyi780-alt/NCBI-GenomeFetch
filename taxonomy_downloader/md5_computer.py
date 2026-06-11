"""
MD5 hash computer for file verification.

This module provides functionality to compute MD5 hashes for files using a
streaming approach to handle files of any size without memory issues.
"""

import hashlib
import logging
from pathlib import Path
from typing import Optional, Callable, Iterator


logger = logging.getLogger(__name__)


class MD5Computer:
    """Computes MD5 hashes for files using streaming approach.
    
    The computer uses a configurable chunk size to read files in blocks,
    allowing efficient processing of large files without loading them
    entirely into memory.
    
    Attributes:
        chunk_size: Size of chunks for streaming (default 8KB)
    
    Examples:
        >>> computer = MD5Computer()
        >>> hash_value = computer.compute_hash(Path("genome.fna"))
        >>> len(hash_value)
        32
        
        >>> # With custom chunk size
        >>> computer = MD5Computer(chunk_size=16384)
        >>> hash_value = computer.compute_hash(Path("large_file.fna"))
    """
    
    DEFAULT_CHUNK_SIZE = 8192  # 8KB default chunk size
    
    def __init__(self, chunk_size: int = DEFAULT_CHUNK_SIZE):
        """
        Initialize MD5 hash computer.
        
        Args:
            chunk_size: Size of chunks for streaming in bytes (default 8KB)
        
        Raises:
            ValueError: If chunk_size is not positive
        
        Examples:
            >>> computer = MD5Computer()
            >>> computer.chunk_size
            8192
            
            >>> computer = MD5Computer(chunk_size=16384)
            >>> computer.chunk_size
            16384
        """
        if chunk_size <= 0:
            raise ValueError(f"Chunk size must be positive, got {chunk_size}")
        
        self.chunk_size = chunk_size
        logger.debug(f"Initialized MD5Computer with chunk_size={chunk_size}")
    
    def compute_hash(self, 
                    file_path: Path, 
                    progress_callback: Optional[Callable[[int], None]] = None) -> str:
        """
        Compute MD5 hash for a file using streaming approach.
        
        The file is read in chunks to avoid loading large files entirely
        into memory. An optional progress callback can be provided to
        track processing progress.
        
        Args:
            file_path: Path to file to hash
            progress_callback: Optional callback function called with bytes processed
            
        Returns:
            MD5 hash as hexadecimal string (32 characters, lowercase)
            
        Raises:
            FileNotFoundError: If file doesn't exist
            PermissionError: If file cannot be read due to permissions
            IOError: If file read fails for other reasons
            IsADirectoryError: If path points to a directory
        
        Examples:
            >>> computer = MD5Computer()
            >>> hash_value = computer.compute_hash(Path("test.txt"))
            >>> isinstance(hash_value, str)
            True
            >>> len(hash_value)
            32
            
            >>> # With progress callback
            >>> def progress(bytes_read):
            ...     print(f"Processed {bytes_read} bytes")
            >>> hash_value = computer.compute_hash(Path("test.txt"), progress)
        """
        # Validate file exists
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        
        # Validate it's a file, not a directory
        if file_path.is_dir():
            raise IsADirectoryError(f"Path is a directory, not a file: {file_path}")
        
        # Compute hash using streaming
        try:
            md5_hash = hashlib.md5()
            bytes_processed = 0
            
            for chunk in self._stream_file(file_path):
                md5_hash.update(chunk)
                bytes_processed += len(chunk)
                
                # Call progress callback if provided
                if progress_callback is not None:
                    progress_callback(bytes_processed)
            
            hash_value = md5_hash.hexdigest().lower()
            logger.debug(f"Computed MD5 hash for {file_path}: {hash_value}")
            
            return hash_value
            
        except PermissionError as e:
            logger.error(f"Permission denied reading {file_path}: {e}")
            raise
        except IOError as e:
            logger.error(f"I/O error reading {file_path}: {e}")
            raise
    
    def _stream_file(self, file_path: Path) -> Iterator[bytes]:
        """
        Stream file contents in chunks.
        
        Reads the file in chunks of size self.chunk_size to avoid
        loading large files entirely into memory.
        
        Args:
            file_path: Path to file to stream
            
        Yields:
            Chunks of file data as bytes
            
        Raises:
            PermissionError: If file cannot be read due to permissions
            IOError: If file read fails
        
        Examples:
            >>> computer = MD5Computer(chunk_size=1024)
            >>> chunks = list(computer._stream_file(Path("test.txt")))
            >>> all(isinstance(chunk, bytes) for chunk in chunks)
            True
        """
        try:
            with open(file_path, 'rb') as f:
                while True:
                    chunk = f.read(self.chunk_size)
                    if not chunk:
                        break
                    yield chunk
        except PermissionError:
            raise
        except IOError:
            raise
