"""
Data models for accession-based genome downloads.
"""

from dataclasses import dataclass, field
from typing import Optional, List, Tuple
from .file_type_utils import expected_output_filenames_for_accession


@dataclass
class AccessionConfig:
    """Configuration for accession download mode."""
    accession_file: str
    output_dir: str
    api_key: Optional[str] = None
    batch_size: int = 100
    max_workers: int = 2
    datasets_executable: str = "datasets"
    max_retries: int = 3
    base_retry_delay: float = 1.0
    include_params: Optional[List[str]] = None
    temp_dir: Optional[str] = None
    resume_validate_files: bool = True
    download_timeout: int = 1800
    rehydrate_timeout: int = 7200
    keep_failed_temp: bool = False
    
    def __post_init__(self):
        """Initialize default values."""
        if self.include_params is None:
            self.include_params = ["genome"]
    
    def has_api_key(self) -> bool:
        """Check if API key is configured."""
        return self.api_key is not None and len(self.api_key) > 0
    
    def format_datasets_params(self) -> dict:
        """
        Format parameters for datasets command.
        
        Returns:
            Dictionary of parameter names to values for datasets CLI
        
        Examples:
            >>> config = AccessionConfig(accession_file="test.txt", output_dir="/tmp")
            >>> config.format_datasets_params()
            {'include': 'genome'}
            
            >>> config = AccessionConfig(
            ...     accession_file="test.txt",
            ...     output_dir="/tmp",
            ...     api_key="test-key",
            ...     include_params=["genome", "protein"]
            ... )
            >>> params = config.format_datasets_params()
            >>> params['api-key']
            'test-key'
            >>> params['include']
            'genome,protein'
        """
        params = {}
        
        if self.api_key:
            params['api-key'] = self.api_key
        
        if self.include_params:
            params['include'] = ','.join(self.include_params)
        
        return params
    
    def validate(self) -> List[str]:
        """Validate configuration and return list of errors."""
        errors = []
        
        # Validate accession file path
        if not self.accession_file:
            errors.append("Accession file path is required")
        
        # Validate output directory path
        if not self.output_dir:
            errors.append("Output directory path is required")
        
        # Validate batch size
        if self.batch_size < 1:
            errors.append("Batch size must be at least 1")
        elif self.batch_size > 1000:
            errors.append("Batch size should not exceed 1000 to avoid overwhelming NCBI servers")
        
        # Validate worker count
        if self.max_workers < 1:
            errors.append("Worker count must be at least 1")
        elif self.max_workers > 20:
            errors.append("Worker count should not exceed 20 to avoid overwhelming NCBI servers")
        
        # Validate retry configuration
        if self.max_retries < 0:
            errors.append("Max retries must be non-negative")
        elif self.max_retries > 10:
            errors.append("Max retries should not exceed 10")
        
        if self.base_retry_delay <= 0:
            errors.append("Base retry delay must be positive")
        elif self.base_retry_delay > 60:
            errors.append("Base retry delay should not exceed 60 seconds")

        if self.download_timeout <= 0:
            errors.append("Download timeout must be positive")

        if self.rehydrate_timeout <= 0:
            errors.append("Rehydrate timeout must be positive")
        
        # Validate API key format (basic check)
        if self.api_key and (len(self.api_key) < 10 or not self.api_key.replace('-', '').replace('_', '').isalnum()):
            errors.append("API key appears to be invalid format")
        
        # Validate include_params
        if self.include_params:
            valid_include_types = {'genome', 'protein', 'rna', 'cds', 'gff3', 'gtf', 'gbff', 'seq-report'}
            for param in self.include_params:
                if param not in valid_include_types:
                    errors.append(f"Invalid include parameter: '{param}'. Valid options are: {', '.join(sorted(valid_include_types))}")
        
        return errors


@dataclass
class ArtifactResult:
    """Trusted output artifact produced by a batch."""

    accession: str
    include_type: str
    filename: str
    expected_md5: str


@dataclass
class BatchResult:
    """Result of processing a single batch."""
    batch_num: int
    success: bool
    files_saved: int = 0
    md5_entries: List[Tuple[str, str]] = field(default_factory=list)
    artifacts: List[ArtifactResult] = field(default_factory=list)
    completed_accessions: List[str] = field(default_factory=list)
    error_message: Optional[str] = None
    error_type: Optional[str] = None


def get_filename_for_accession(accession: str) -> str:
    """
    Generate output filename for an accession.
    
    This function ensures filename consistency between file saving
    (in AccessionBatchProcessor) and file validation (in AccessionDownloader).
    
    CRITICAL: Any changes to filename format must be made here to maintain
    consistency across the entire codebase. This function MUST be used by
    both file saving and file validation operations.
    
    Args:
        accession: Full accession identifier (e.g., GCF_000001405.40)
        
    Returns:
        Filename string (e.g., "GCF_000001405.40.fna")
    
    Note:
        This function is placed in accession_models.py to avoid circular
        imports between AccessionDownloader and AccessionBatchProcessor.
        Both modules can safely import from this models module without
        creating dependency cycles.
    
    Examples:
        >>> get_filename_for_accession("GCF_000001405.40")
        'GCF_000001405.40.fna'
        >>> get_filename_for_accession("GCA_000001635.9")
        'GCA_000001635.9.fna'
    """
    return f"{accession}.fna"


def get_filenames_for_accession(accession: str, include_params: Optional[List[str]] = None) -> List[str]:
    """
    Generate possible output filenames for an accession and include parameters.

    Resume validation uses this include-aware form. The legacy
    get_filename_for_accession() function remains genome-only for compatibility.
    """
    return expected_output_filenames_for_accession(accession, include_params or ["genome"])
