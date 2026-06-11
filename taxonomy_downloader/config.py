"""
Configuration management and input validation.
"""

import os
import argparse
import sys
from pathlib import Path
from typing import List, Dict, Optional, Any
from .models import DownloadConfig, ErrorType


class ConfigurationError(Exception):
    """Raised when configuration validation fails."""
    pass


class ConfigManager:
    """Manages download configuration and validates inputs."""
    
    def __init__(self):
        self.config: Optional[DownloadConfig] = None
    
    def parse_args(self, args: List[str] = None) -> DownloadConfig:
        """Parse command-line arguments and create configuration."""
        parser = self._create_parser()
        parsed_args = parser.parse_args(args)
        
        # Check for mutual exclusivity between mode parameters first
        mode_params = [
            ('md5sum', parsed_args.md5sum),
            ('split', parsed_args.split),
            ('input', parsed_args.input),
            ('accession', parsed_args.accession)
        ]
        active_modes = [(name, value) for name, value in mode_params if value]
        
        # Special handling: --md5sum-auto-fix can be used alone or with --md5sum
        # If only --md5sum-auto-fix is provided, it's a standalone auto-fix mode
        if parsed_args.md5sum_auto_fix and not parsed_args.md5sum:
            # Standalone auto-fix mode
            return self._create_standalone_autofix_config(parsed_args)
        
        if len(active_modes) > 1:
            mode_names = [f"--{name}" for name, _ in active_modes]
            parser.error(f"Mode parameters are mutually exclusive: {', '.join(mode_names)}")

        if parsed_args.rebuild_md5:
            if not parsed_args.accession:
                parser.error("--rebuild-md5 requires -a/--accession")
            if not parsed_args.output:
                parser.error("--rebuild-md5 requires -o/--output")
            if not parsed_args.dehy:
                parser.error("--rebuild-md5 requires --dehy")

            dehy_path = Path(parsed_args.dehy)
            if not dehy_path.exists():
                parser.error(f"Dehydrated package does not exist: {parsed_args.dehy}")
            if not dehy_path.is_file():
                parser.error(f"Dehydrated package is not a file: {parsed_args.dehy}")

            output_dir = Path(parsed_args.output)
            if not output_dir.exists():
                parser.error(f"Output directory does not exist: {parsed_args.output}")
            if not output_dir.is_dir():
                parser.error(f"Output path is not a directory: {parsed_args.output}")

            config = DownloadConfig(
                input_file="",
                output_dir=parsed_args.output,
                temp_dir=parsed_args.temp_dir,
                api_key=None,
                include_params=parsed_args.include.split(",") if parsed_args.include else ["genome"],
                assembly_source=None,
                additional_params={},
                max_workers=1,
                datasets_executable=parsed_args.datasets_exe or "datasets",
                resume_validate_files=not parsed_args.no_validate_resume_files,
            )
            config._rebuild_md5_mode = True
            config._accession_file = parsed_args.accession
            config._dehy_package = str(dehy_path.resolve())
            return config
        
        # Check if split mode is requested
        if parsed_args.split:
            # Return a special config indicating split mode
            # We'll use a sentinel value to indicate split mode
            config = DownloadConfig(
                input_file="",  # Not used in split mode
                output_dir=parsed_args.output or "",  # Optional in split mode
                temp_dir=None,
                api_key=None,
                include_params=["genome"],
                assembly_source=None,
                additional_params={},
                max_workers=1,
                datasets_executable="datasets"
            )
            # Store split mode info in config
            config._split_mode = True
            config._split_taxon = parsed_args.split
            return config
        
        # Check if MD5 verification mode is requested
        if parsed_args.md5sum:
            # Validate that the directory parameter is provided and not empty
            if not parsed_args.md5sum.strip():
                parser.error("--md5sum requires a non-empty directory path")
            
            # Validate that the directory exists
            md5sum_dir = Path(parsed_args.md5sum)
            if not md5sum_dir.exists():
                parser.error(f"MD5 verification directory does not exist: {parsed_args.md5sum}")
            
            if not md5sum_dir.is_dir():
                parser.error(f"MD5 verification path is not a directory: {parsed_args.md5sum}")
            
            # Return a special config indicating MD5 verification mode
            config = DownloadConfig(
                input_file="",  # Not used in MD5 verification mode
                output_dir="",  # Not used in MD5 verification mode
                temp_dir=None,
                api_key=parsed_args.api_key or self._detect_api_key(),
                include_params=parsed_args.include.split(',') if parsed_args.include else ["genome"],
                assembly_source=None,
                additional_params={},
                max_workers=parsed_args.workers,
                datasets_executable=parsed_args.datasets_exe or "datasets",
                resume_validate_files=not parsed_args.no_validate_resume_files,
                download_timeout=parsed_args.download_timeout,
                rehydrate_timeout=parsed_args.rehydrate_timeout,
                keep_failed_temp=parsed_args.keep_failed_temp,
            )
            # Store MD5 verification mode info in config
            config._md5_verification_mode = True
            config._md5_verification_dir = str(md5sum_dir.resolve())
            config._md5_auto_fix = bool(parsed_args.md5sum_auto_fix)
            config._md5_failed_file = parsed_args.md5sum_auto_fix if parsed_args.md5sum_auto_fix else None
            config._batch_size = parsed_args.batch  # Store batch size for auto-fix
            return config
        
        # Check for mutual exclusivity between --input and --accession (legacy check, now handled above)
        # This is kept for backwards compatibility but should never be reached
        
        # Check if accession mode is requested
        if parsed_args.accession:
            # Return a special config indicating accession mode
            # We'll use a sentinel value to indicate accession mode
            config = DownloadConfig(
                input_file="",  # Not used in accession mode
                output_dir=parsed_args.output or "",
                temp_dir=parsed_args.temp_dir,
                api_key=None,
                include_params=parsed_args.include.split(',') if parsed_args.include else ["genome"],
                assembly_source=None,
                additional_params={},
                max_workers=parsed_args.workers,
                datasets_executable=parsed_args.datasets_exe or "datasets",
                resume_validate_files=not parsed_args.no_validate_resume_files
            )
            # Store accession mode info in config
            config._accession_mode = True
            config._accession_file = parsed_args.accession
            config._batch_size = parsed_args.batch
            config._api_key = parsed_args.api_key or self._detect_api_key()
            config._resume_validate_files = not parsed_args.no_validate_resume_files
            config._download_timeout = parsed_args.download_timeout
            config._rehydrate_timeout = parsed_args.rehydrate_timeout
            config._keep_failed_temp = parsed_args.keep_failed_temp
            return config
        
        # Download mode - validate required arguments
        if not parsed_args.input:
            parser.error("the following arguments are required: -i/--input")
        if not parsed_args.output:
            parser.error("the following arguments are required: -o/--output")
        
        # Detect API key from environment if not provided
        api_key = parsed_args.api_key or self._detect_api_key()
        
        # Create configuration from parsed arguments
        config = DownloadConfig(
            input_file=parsed_args.input,
            output_dir=parsed_args.output,
            temp_dir=parsed_args.temp_dir,
            api_key=api_key,
            include_params=parsed_args.include.split(',') if parsed_args.include else ["genome"],
            assembly_source=parsed_args.assembly_source,
            additional_params=self._parse_additional_params(parsed_args.additional_params),
            max_workers=parsed_args.workers,
            datasets_executable=parsed_args.datasets_exe or "datasets",
            resume_validate_files=not parsed_args.no_validate_resume_files,
            download_timeout=parsed_args.download_timeout,
            rehydrate_timeout=parsed_args.rehydrate_timeout,
            keep_failed_temp=parsed_args.keep_failed_temp,
            # Disk space backoff configuration
            disk_check_interval=parsed_args.disk_check_interval,
            disk_warning_threshold=parsed_args.disk_warning_threshold,
            disk_critical_threshold=parsed_args.disk_critical_threshold,
            disk_minimum_threshold=parsed_args.disk_minimum_threshold,
            disk_warning_min_bytes=parsed_args.disk_warning_bytes,
            disk_critical_min_bytes=parsed_args.disk_critical_bytes,
            disk_minimum_bytes=parsed_args.disk_minimum_bytes,
            enable_disk_backoff=not parsed_args.disable_disk_backoff,
        )
        
        # Validate configuration
        self._validate_config(config)
        self.config = config
        return config
    
    def _create_parser(self) -> argparse.ArgumentParser:
        """Create command-line argument parser."""
        parser = argparse.ArgumentParser(
            description="Download genome data from NCBI using taxonomy names or accession numbers",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            epilog="""
Examples:
  %(prog)s -i taxa.txt -o genomes/
  %(prog)s -i taxa.txt -o genomes/ -k YOUR_API_KEY -w 4
  %(prog)s -i taxa.txt -o genomes/ --include genome,protein --assembly-source refseq
  %(prog)s -a accessions.txt -o genomes/
  %(prog)s -a accessions.txt -o genomes/ -b 50 -w 4
  %(prog)s -s Archaea -o split_output/
  %(prog)s -s Archaea
  %(prog)s --md5sum genomes/
  %(prog)s --md5sum genomes/ --md5sum-auto-fix
  %(prog)s --md5sum-auto-fix
  %(prog)s --md5sum-auto-fix custom_failed_files.txt
            """
        )
        
        # Mode selection (mutually exclusive with download mode)
        parser.add_argument(
            "-s", "--split",
            metavar="TAXON",
            help="Enter split mode for the specified taxon. "
                 "Accepts a taxon name (e.g., -s Archaea) or "
                 "a path to the taxon data folder (e.g., -s Archaea/ or -s ./data/Archaea)"
        )
        parser.add_argument(
            "--md5sum",
            metavar="DIRECTORY",
            help="Verify MD5 checksums for files in the specified directory. "
                 "Failed files will be saved to md5_failed_files.txt in the current directory"
        )
        parser.add_argument(
            "--md5sum-auto-fix",
            metavar="FILE",
            nargs="?",
            const="md5_failed_files.txt",
            help="Automatically redownload and fix files from failed file list. "
                 "Can be used alone (reads md5_failed_files.txt from current directory) "
                 "or with --md5sum for one-step verification and fixing. "
                 "Optionally specify a custom failed file list path"
        )
        parser.add_argument(
            "--rebuild-md5",
            action="store_true",
            help="Rebuild accession md5sum.txt and .accession_manifest.json from a dehydrated package"
        )
        parser.add_argument(
            "--dehy",
            metavar="ZIP",
            help="Dehydrated datasets zip package used by --rebuild-md5"
        )
        
        # Required arguments for download mode
        parser.add_argument(
            "-i", "--input",
            help="Input file containing taxonomy names (one per line). Mutually exclusive with -a/--accession"
        )
        parser.add_argument(
            "-a", "--accession",
            metavar="FILE",
            help="Input file containing accession numbers (one per line). Mutually exclusive with -i/--input"
        )
        parser.add_argument(
            "-o", "--output",
            help="Output directory (for downloads or split results)"
        )
        parser.add_argument(
            "-b", "--batch",
            type=int,
            default=100,
            help="Accessions per batch in accession mode (default: 100)"
        )
        
        # Optional arguments
        parser.add_argument(
            "-k", "--api-key",
            help="NCBI API key for higher rate limits"
        )
        parser.add_argument(
            "-w", "--workers",
            type=int,
            default=2,
            help="Number of parallel workers (default: 2)"
        )
        parser.add_argument(
            "--temp-dir",
            help="Temporary directory for processing (default: system temp)"
        )
        parser.add_argument(
            "--include",
            default="genome",
            help="Data types to include (comma-separated, default: genome)"
        )
        parser.add_argument(
            "--assembly-source",
            choices=["refseq", "genbank", "all"],
            help="Assembly source filter"
        )
        parser.add_argument(
            "--datasets-exe",
            help="Path to datasets executable (default: datasets)"
        )
        parser.add_argument(
            "--additional-params",
            help="Additional parameters for datasets command (key=value,key2=value2)"
        )
        parser.add_argument(
            "--no-validate-resume-files",
            action="store_true",
            help="When resuming, trust the progress JSON state and skip output file existence validation"
        )
        parser.add_argument(
            "--download-timeout",
            type=int,
            default=1800,
            metavar="SECONDS",
            help="Timeout for datasets download commands in accession mode (default: 1800)"
        )
        parser.add_argument(
            "--rehydrate-timeout",
            type=int,
            default=7200,
            metavar="SECONDS",
            help="Timeout for datasets rehydrate commands in accession mode (default: 7200)"
        )
        parser.add_argument(
            "--keep-failed-temp",
            action="store_true",
            help="Preserve failed accession batch temporary directories for diagnosis"
        )
        
        # Disk space backoff parameters
        parser.add_argument(
            "--disk-warning-threshold",
            type=float,
            default=0.20,
            metavar="PERCENT",
            help="Disk space warning threshold as decimal (default: 0.20 = 20%%)"
        )
        parser.add_argument(
            "--disk-critical-threshold",
            type=float,
            default=0.10,
            metavar="PERCENT",
            help="Disk space critical threshold as decimal (default: 0.10 = 10%%)"
        )
        parser.add_argument(
            "--disk-minimum-threshold",
            type=float,
            default=0.05,
            metavar="PERCENT",
            help="Disk space minimum threshold as decimal (default: 0.05 = 5%%)"
        )
        parser.add_argument(
            "--disk-warning-bytes",
            type=self._parse_bytes_size,
            default=10 * 1024 * 1024 * 1024,
            metavar="SIZE",
            help="Minimum free bytes for warning level (default: 10GB, supports K/M/G/T suffixes)"
        )
        parser.add_argument(
            "--disk-critical-bytes",
            type=self._parse_bytes_size,
            default=5 * 1024 * 1024 * 1024,
            metavar="SIZE",
            help="Minimum free bytes for critical level (default: 5GB, supports K/M/G/T suffixes)"
        )
        parser.add_argument(
            "--disk-minimum-bytes",
            type=self._parse_bytes_size,
            default=1 * 1024 * 1024 * 1024,
            metavar="SIZE",
            help="Minimum free bytes before pausing (default: 1GB, supports K/M/G/T suffixes)"
        )
        parser.add_argument(
            "--disk-check-interval",
            type=float,
            default=30.0,
            metavar="SECONDS",
            help="Disk space check interval in seconds (default: 30)"
        )
        parser.add_argument(
            "--disable-disk-backoff",
            action="store_true",
            help="Disable automatic disk space backoff (not recommended)"
        )
        
        return parser
    
    def _parse_bytes_size(self, size_str: str) -> int:
        """
        Parse a size string with optional K/M/G/T suffix to bytes.
        
        Args:
            size_str: Size string like "10G", "500M", "1T", or plain number
            
        Returns:
            Size in bytes
            
        Raises:
            argparse.ArgumentTypeError: If format is invalid
        """
        import argparse
        
        size_str = size_str.strip().upper()
        
        # Define multipliers
        multipliers = {
            'K': 1024,
            'KB': 1024,
            'M': 1024 * 1024,
            'MB': 1024 * 1024,
            'G': 1024 * 1024 * 1024,
            'GB': 1024 * 1024 * 1024,
            'T': 1024 * 1024 * 1024 * 1024,
            'TB': 1024 * 1024 * 1024 * 1024,
        }
        
        # Try to parse with suffix
        for suffix, multiplier in sorted(multipliers.items(), key=lambda x: -len(x[0])):
            if size_str.endswith(suffix):
                try:
                    number = float(size_str[:-len(suffix)])
                    return int(number * multiplier)
                except ValueError:
                    raise argparse.ArgumentTypeError(f"Invalid size format: {size_str}")
        
        # Try to parse as plain number
        try:
            return int(float(size_str))
        except ValueError:
            raise argparse.ArgumentTypeError(
                f"Invalid size format: {size_str}. Use a number with optional K/M/G/T suffix (e.g., 10G, 500M)"
            )
    
    def _parse_additional_params(self, param_string: Optional[str]) -> Dict[str, str]:
        """Parse additional parameters from string format."""
        if not param_string:
            return {}
        
        params = {}
        try:
            for pair in param_string.split(','):
                if '=' in pair:
                    key, value = pair.split('=', 1)
                    params[key.strip()] = value.strip()
        except Exception as e:
            raise ConfigurationError(f"Invalid additional parameters format: {e}")
        
        return params
    
    def _validate_config(self, config: DownloadConfig) -> None:
        """Validate configuration parameters."""
        errors = []
        
        # Validate input file
        if not os.path.exists(config.input_file):
            errors.append(f"Input file does not exist: {config.input_file}")
        elif not os.path.isfile(config.input_file):
            errors.append(f"Input path is not a file: {config.input_file}")
        
        # Validate output directory
        try:
            Path(config.output_dir).mkdir(parents=True, exist_ok=True)
        except Exception as e:
            errors.append(f"Cannot create output directory: {e}")
        
        # Validate temporary directory if specified
        if config.temp_dir:
            try:
                Path(config.temp_dir).mkdir(parents=True, exist_ok=True)
            except Exception as e:
                errors.append(f"Cannot create temporary directory: {e}")
        
        # Use DownloadConfig's parameter validation
        config_errors = config.validate_parameters()
        errors.extend(config_errors)
        
        # Validate datasets executable and version
        datasets_info = self._check_datasets_executable(config.datasets_executable)
        if not datasets_info['available']:
            import platform
            system = platform.system()
            if system == "Windows":
                install_msg = ("Please install NCBI datasets tool from: "
                             "https://www.ncbi.nlm.nih.gov/datasets/docs/v2/download-and-install/ "
                             "Make sure datasets.exe is in your PATH or specify the full path with --datasets-exe")
            else:
                install_msg = ("Please install NCBI datasets tool from: "
                             "https://www.ncbi.nlm.nih.gov/datasets/docs/v2/download-and-install/ "
                             "Make sure datasets is in your PATH or specify the full path with --datasets-exe")
            
            errors.append(f"datasets executable not found or not working: {config.datasets_executable}\n{install_msg}")
        elif not datasets_info['version_compatible']:
            errors.append(f"datasets version {datasets_info['version']} is not compatible. "
                         f"Required minimum version: {config.datasets_version}")
        else:
            # Update config with the working executable name
            if 'executable_used' in datasets_info:
                config.datasets_executable = datasets_info['executable_used']
        
        if errors:
            raise ConfigurationError("Configuration validation failed:\n" + "\n".join(f"  - {error}" for error in errors))
    
    def _check_datasets_executable(self, executable: str) -> Dict[str, Any]:
        """Check if datasets executable is available and working."""
        result = {
            'available': False,
            'version': None,
            'version_compatible': False
        }
        
        # Try different executable names based on platform
        executables_to_try = []
        
        if executable == "datasets":
            # Default case - try platform-appropriate names
            import platform
            if platform.system() == "Windows":
                executables_to_try = ["datasets.exe", "datasets"]
            else:
                executables_to_try = ["datasets", "datasets.exe"]
        else:
            # User specified a specific executable
            executables_to_try = [executable]
        
        for exec_name in executables_to_try:
            try:
                import subprocess
                proc_result = subprocess.run(
                    [exec_name, "--version"],
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                
                if proc_result.returncode == 0:
                    result['available'] = True
                    result['executable_used'] = exec_name
                    
                    # Extract version from output
                    version_output = proc_result.stdout.strip()
                    if 'version:' in version_output:
                        version_part = version_output.split('version:')[1].strip().split()[0]
                        result['version'] = version_part
                        
                        # Simple version comparison (assumes semantic versioning)
                        try:
                            current_version = tuple(map(int, version_part.split('.')))
                            required_version = tuple(map(int, "16.0.0".split('.')))
                            result['version_compatible'] = current_version >= required_version
                        except ValueError:
                            # If version parsing fails, assume compatible
                            result['version_compatible'] = True
                    else:
                        # If we can't parse version, assume compatible
                        result['version_compatible'] = True
                    
                    # Update config to use the working executable
                    if hasattr(self, 'config') and self.config:
                        self.config.datasets_executable = exec_name
                    
                    break  # Found working executable, stop trying
                        
            except Exception:
                continue  # Try next executable
        
        return result
    
    def _detect_api_key(self) -> Optional[str]:
        """Detect API key from environment variables."""
        # Check common environment variable names for NCBI API key
        env_vars = ['NCBI_API_KEY', 'DATASETS_API_KEY', 'NCBI_DATASETS_API_KEY']
        
        for var in env_vars:
            api_key = os.environ.get(var)
            if api_key and api_key.strip():
                return api_key.strip()
        
        return None
    
    def _create_standalone_autofix_config(self, parsed_args) -> DownloadConfig:
        """
        Create configuration for standalone auto-fix mode.
        
        This mode reads a failed file list and performs auto-fix without
        running MD5 verification first.
        
        Args:
            parsed_args: Parsed command-line arguments
            
        Returns:
            DownloadConfig configured for standalone auto-fix mode
        """
        # Get the failed file path (default or user-specified)
        failed_file = parsed_args.md5sum_auto_fix
        failed_file_path = Path(failed_file)
        
        # Validate that the failed file exists
        if not failed_file_path.exists():
            raise ConfigurationError(
                f"Failed file list not found: {failed_file}\n"
                f"Please ensure the file exists or run --md5sum first to generate it."
            )
        
        if not failed_file_path.is_file():
            raise ConfigurationError(
                f"Failed file list path is not a file: {failed_file}"
            )
        
        # Create config for standalone auto-fix mode
        config = DownloadConfig(
            input_file="",  # Not used in standalone auto-fix mode
            output_dir="",  # Will be determined from failed file list
            temp_dir=None,
            api_key=parsed_args.api_key or self._detect_api_key(),
            include_params=parsed_args.include.split(',') if parsed_args.include else ["genome"],
            assembly_source=None,
            additional_params={},
            max_workers=parsed_args.workers,
            datasets_executable=parsed_args.datasets_exe or "datasets"
        )
        
        # Store standalone auto-fix mode info in config
        config._standalone_autofix_mode = True
        config._md5_failed_file = str(failed_file_path.resolve())
        config._batch_size = parsed_args.batch  # Store batch size for auto-fix
        
        return config
    
    def load_taxa_from_file(self, filepath: str) -> List[str]:
        """Load taxonomy names or TaxIDs from input file.
        
        Supports two formats:
        1. Simple format: One taxon name or TaxID per line
        2. Split format: Species with descendants (includes comments and blank lines)
           # Species: Name (TaxID)
           Name<TAB>TaxID
           Descendant1<TAB>TaxID1
           ...
        
        For split format, only extracts the taxon names/TaxIDs (second column).
        Skips comment lines (starting with #) and blank lines.
        
        Args:
            filepath: Path to the input file
            
        Returns:
            List of taxonomy names or TaxIDs
            
        Raises:
            ConfigurationError: If file cannot be read or contains no valid taxa
        """
        try:
            taxa = []
            with open(filepath, 'r', encoding='utf-8') as f:
                for line in f:
                    stripped = line.strip()
                    
                    # Skip empty lines and comment lines
                    if not stripped or stripped.startswith('#'):
                        continue
                    
                    # Check if line contains tab (split format)
                    if '\t' in stripped:
                        # Split format: taxon_name<TAB>tax_id
                        # Extract the second column (TaxID)
                        parts = stripped.split('\t')
                        if len(parts) >= 2 and parts[1].strip():
                            # Valid split format with non-empty TaxID
                            tax_id = parts[1].strip()
                            taxa.append(tax_id)
                        # If TaxID is empty, skip this line
                    else:
                        # Simple format: just the taxon name or TaxID
                        taxa.append(stripped)
            
            if not taxa:
                raise ConfigurationError(f"No valid taxonomy names found in {filepath}")
            
            return taxa
        except Exception as e:
            raise ConfigurationError(f"Failed to read taxonomy file: {e}")
