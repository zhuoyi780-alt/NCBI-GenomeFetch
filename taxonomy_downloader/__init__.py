"""
NCBI-GenomeFetch

A command-line tool for batch downloading genome data from NCBI using taxonomy names.

Features:
- Batch download genomes using taxonomy names, TaxIDs, or accessions
- Cross-platform support (Windows, Linux, macOS)
- Progress tracking and resume capability
- Rate limiting and error handling
- MD5 checksum validation and repair workflows
- Structured logging and reporting
- Disk space dynamic backoff
- Task splitting for large taxonomy downloads
"""

__version__ = "1.0.0"
__author__ = "NCBI-GenomeFetch Team"
__email__ = ""
__license__ = "MIT"
__description__ = "A command-line tool for batch downloading genome data from NCBI using taxonomy names"

from .models import DownloadConfig, DownloadResults


def main(*args, **kwargs):
    """Run the command-line entry point."""
    from .cli import main as cli_main

    return cli_main(*args, **kwargs)

__all__ = [
    "__version__",
    "__author__", 
    "__email__",
    "__license__",
    "__description__",
    "main",
    "DownloadConfig",
    "DownloadResults",
]
