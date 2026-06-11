"""
Core data models and enums for the taxonomy downloader.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Optional, List, Dict, Set
from pathlib import Path
import time

from taxonomy_downloader.file_type_utils import (
    get_expected_file_extensions,
    detect_file_type,
    get_output_extension,
)


class ErrorType(Enum):
    """Classification of error types for proper handling."""
    ENVIRONMENT = "environment"  # Fatal: missing tools, network failure
    DATA = "data"               # Non-fatal: taxon not found, no genomes
    TRANSIENT = "transient"     # Retryable: rate limits, temporary network issues
    FILESYSTEM = "filesystem"   # Fatal: permissions, disk space
    PROCESSING = "processing"   # Non-fatal: extraction failure, corruption


class SpaceLevel(Enum):
    """Disk space level classification for backoff control."""
    NORMAL = "normal"      # >= warning_threshold, full concurrency
    WARNING = "warning"    # >= critical_threshold, < warning_threshold, reduced concurrency
    CRITICAL = "critical"  # >= minimum_threshold, < critical_threshold, single worker
    PAUSED = "paused"      # < minimum_threshold, pause new tasks


@dataclass
class SpaceStatus:
    """Disk space status for both temp and output directories."""
    temp_dir_free_bytes: int
    temp_dir_free_percent: float
    output_dir_free_bytes: int
    output_dir_free_percent: float
    level: SpaceLevel
    timestamp: float


@dataclass
class DownloadConfig:
    """Configuration parameters for the download process."""
    input_file: str
    output_dir: str
    temp_dir: Optional[str] = None  # User-specified temporary directory
    api_key: Optional[str] = None
    include_params: List[str] = None
    assembly_source: Optional[str] = None
    additional_params: Dict[str, str] = None
    max_workers: int = 2
    datasets_executable: str = "datasets"
    datasets_version: str = "16.0.0"  # Required minimum datasets version
    rate_limit_per_second: int = 3  # Default rate limit (will be updated based on API key)
    resume_validate_files: bool = True  # Validate output files before skipping completed state entries
    download_timeout: int = 1800
    rehydrate_timeout: int = 7200
    keep_failed_temp: bool = False
    
    # Disk space backoff configuration
    disk_check_interval: float = 30.0  # Normal state check interval (seconds)
    disk_critical_interval: float = 5.0  # CRITICAL state check interval (seconds)
    disk_warning_threshold: float = 0.20  # Warning threshold (20%)
    disk_critical_threshold: float = 0.10  # Critical threshold (10%)
    disk_minimum_threshold: float = 0.05  # Minimum threshold (5%)
    # Minimum absolute values for each level (for large disks)
    disk_warning_min_bytes: int = 10 * 1024 * 1024 * 1024   # 10GB
    disk_critical_min_bytes: int = 5 * 1024 * 1024 * 1024   # 5GB
    disk_minimum_bytes: int = 1 * 1024 * 1024 * 1024        # 1GB
    # Hysteresis margin to prevent thrashing
    disk_hysteresis_margin: float = 0.02  # 2%
    enable_disk_backoff: bool = True  # Enable/disable disk backoff feature
    
    def __post_init__(self):
        """Initialize default values and calculate rate limits."""
        if self.include_params is None:
            self.include_params = ["genome"]
        if self.additional_params is None:
            self.additional_params = {}
        
        # Set rate limit based on API key presence
        self.rate_limit_per_second = 10 if self.api_key else 3
    
    def format_datasets_params(self) -> Dict[str, str]:
        """Format parameters for datasets command construction."""
        params = {}
        
        if self.api_key:
            params['api-key'] = self.api_key
        
        if self.include_params:
            params['include'] = ','.join(self.include_params)
        
        if self.assembly_source:
            params['assembly-source'] = self.assembly_source
        
        # Add additional parameters
        params.update(self.additional_params)
        
        return params
    
    def validate_parameters(self) -> List[str]:
        """Validate configuration parameters and return list of errors."""
        errors = []
        
        # Validate include parameters
        valid_include_types = {
            'genome', 'rna', 'protein', 'cds', 'gff3', 'gtf', 
            'gbff', 'seq-report', 'none'
        }
        for param in self.include_params:
            if param not in valid_include_types:
                errors.append(f"Invalid include parameter: {param}. "
                            f"Valid options: {', '.join(sorted(valid_include_types))}")
        
        # Validate assembly source
        if self.assembly_source and self.assembly_source not in ['refseq', 'genbank', 'all']:
            errors.append(f"Invalid assembly source: {self.assembly_source}. "
                         "Valid options: refseq, genbank, all")
        
        # Validate worker count
        if self.max_workers < 1:
            errors.append("Worker count must be at least 1")
        elif self.max_workers > 20:
            errors.append("Worker count should not exceed 20 to avoid overwhelming NCBI servers")
        
        # Validate API key format (basic check)
        if self.api_key and (len(self.api_key) < 10 or not self.api_key.replace('-', '').replace('_', '').isalnum()):
            errors.append("API key appears to be invalid format")

        if self.download_timeout <= 0:
            errors.append("Download timeout must be positive")

        if self.rehydrate_timeout <= 0:
            errors.append("Rehydrate timeout must be positive")
        
        # Validate disk backoff thresholds (minimum < critical < warning)
        if self.enable_disk_backoff:
            if not (0 < self.disk_minimum_threshold < self.disk_critical_threshold < self.disk_warning_threshold <= 1.0):
                errors.append(
                    f"Disk thresholds must satisfy: 0 < minimum ({self.disk_minimum_threshold}) "
                    f"< critical ({self.disk_critical_threshold}) < warning ({self.disk_warning_threshold}) <= 1.0"
                )
            
            # Validate absolute byte thresholds
            if not (0 < self.disk_minimum_bytes < self.disk_critical_min_bytes < self.disk_warning_min_bytes):
                errors.append(
                    f"Disk byte thresholds must satisfy: 0 < minimum_bytes ({self.disk_minimum_bytes}) "
                    f"< critical_bytes ({self.disk_critical_min_bytes}) < warning_bytes ({self.disk_warning_min_bytes})"
                )
            
            # Validate hysteresis margin
            if self.disk_hysteresis_margin < 0 or self.disk_hysteresis_margin > 0.1:
                errors.append(
                    f"Disk hysteresis margin ({self.disk_hysteresis_margin}) must be between 0 and 0.1"
                )
            
            # Validate check intervals
            if self.disk_check_interval <= 0:
                errors.append("Disk check interval must be positive")
            if self.disk_critical_interval <= 0:
                errors.append("Disk critical interval must be positive")
        
        return errors
    
    def get_expected_file_extensions(self) -> List[str]:
        """
        Get expected file extensions for searching based on include_params.
        
        Delegates to shared utility function for consistency across modes.
        
        Returns:
            Sorted list of unique file extensions (with dots) to search for
        
        Examples:
            >>> config = DownloadConfig(input_file='taxa.txt', output_dir='out', 
            ...                         include_params=['genome', 'protein'])
            >>> config.get_expected_file_extensions()
            ['.fa', '.faa', '.fasta', '.fna']
        """
        return get_expected_file_extensions(self.include_params)
    
    def detect_file_type(self, filename: str) -> Optional[str]:
        """
        Detect include type from filename pattern.
        
        Delegates to shared utility function for consistency across modes.
        
        Args:
            filename: Name of the file (not full path)
        
        Returns:
            Include type string or None if not recognized
        
        Examples:
            >>> config = DownloadConfig(input_file='taxa.txt', output_dir='out')
            >>> config.detect_file_type('protein.faa')
            'protein'
            >>> config.detect_file_type('cds_from_genomic.fna')
            'cds'
        """
        return detect_file_type(filename)
    
    def get_output_extension(self, file_path: Path, include_type: Optional[str] = None) -> str:
        """
        Get standardized output extension for a file.
        
        Delegates to shared utility function for consistency across modes.
        
        Args:
            file_path: Original file path
            include_type: Optional include type hint (e.g., 'cds', 'protein')
        
        Returns:
            Standardized extension (e.g., '.fna', '.faa', '.cds')
        
        Examples:
            >>> config = DownloadConfig(input_file='taxa.txt', output_dir='out')
            >>> config.get_output_extension(Path('protein.faa'))
            '.faa'
            >>> config.get_output_extension(Path('cds_from_genomic.fna'), 'cds')
            '.cds'
        """
        return get_output_extension(file_path, include_type)


@dataclass
class TaxonResult:
    """Result of processing a single taxon."""
    taxon: str
    success: bool
    files_found: int = 0
    error_message: Optional[str] = None
    processing_time: float = 0.0
    error_type: Optional[ErrorType] = None


@dataclass
class DownloadResults:
    """Aggregated results from all taxon processing."""
    total_taxa: int
    successful: int
    failed: int
    total_files: int
    failed_taxa: List[TaxonResult]
    processing_time: float
    start_time: float = None
    
    def __post_init__(self):
        """Set start time if not provided."""
        if self.start_time is None:
            self.start_time = time.time()


@dataclass
class FileInfo:
    """Information about a downloaded file."""
    original_path: str
    filename: str
    size: int
    checksum: Optional[str] = None


@dataclass
class ProgressState:
    """State information for resume functionality."""
    completed_taxa: Set[str]
    failed_taxa: Set[str]
    start_time: float
    last_update: float
    
    def __post_init__(self):
        """Initialize sets if None."""
        if self.completed_taxa is None:
            self.completed_taxa = set()
        if self.failed_taxa is None:
            self.failed_taxa = set()
