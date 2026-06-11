"""
Interface for NCBI datasets command-line tool.
"""

import subprocess
import shutil
import platform
import os
from typing import List, Dict, Optional, Any, Tuple
from .models import DownloadConfig
from .logging_config import get_logger
from .rate_limiter import RateLimiter, RetryHandler


class DatasetsVersionError(Exception):
    """Raised when datasets version is incompatible."""
    pass


class DatasetsInterface:
    """Interface for executing NCBI datasets commands."""
    
    def __init__(self, config: DownloadConfig):
        self.config = config
        self.logger = get_logger("datasets_interface")
        
        # Initialize rate limiter
        self.rate_limiter = RateLimiter(config.rate_limit_per_second)
        
        # Initialize retry handler
        self.retry_handler = RetryHandler(max_retries=3, base_delay=1.0, max_delay=30.0)
        
        # Resolve executable to absolute path for cross-platform compatibility
        self._resolved_executable = self._resolve_executable_path(config.datasets_executable)
        
        # Validate version compatibility
        self._validate_version()
    
    def _resolve_executable_path(self, executable: str) -> str:
        """
        Resolve executable to absolute path with cross-platform detection.
        
        This is critical for Windows compatibility where subprocess.run() with shell=False
        may not find executables in PATH without absolute path resolution.
        
        Args:
            executable: Executable name or path
            
        Returns:
            Absolute path to executable
            
        Raises:
            FileNotFoundError: If executable cannot be found
        """
        # If it's already an absolute path, validate it exists
        if os.path.isabs(executable):
            if os.path.isfile(executable):
                return executable
            else:
                raise FileNotFoundError(f"Datasets executable not found at: {executable}")
        
        # Try cross-platform executable detection
        candidates = self._get_executable_candidates(executable)
        
        for candidate in candidates:
            resolved_path = shutil.which(candidate)
            if resolved_path:
                self.logger.debug(f"Resolved executable '{executable}' to '{resolved_path}'")
                return resolved_path
        
        # If not found, raise error
        raise FileNotFoundError(f"Datasets executable '{executable}' not found in PATH. "
                              f"Tried candidates: {candidates}")
    
    def _get_executable_candidates(self, executable: str) -> List[str]:
        """
        Get list of executable candidates for cross-platform detection.
        
        Args:
            executable: Base executable name
            
        Returns:
            List of candidates to try
        """
        candidates = [executable]
        
        # On Windows, try .exe extension if not already present
        if platform.system() == "Windows":
            if not executable.lower().endswith('.exe'):
                candidates.append(f"{executable}.exe")
        
        return candidates
    
    def _validate_version(self) -> None:
        """
        Validate that the datasets executable version is compatible.
        
        Raises:
            DatasetsVersionError: If version is incompatible
        """
        try:
            version_info = self.get_version_info()
            
            if not version_info['available']:
                raise DatasetsVersionError(f"Datasets executable is not working: {self._resolved_executable}")
            
            if not version_info['version_compatible']:
                raise DatasetsVersionError(
                    f"Datasets version {version_info['version']} is not compatible. "
                    f"Required minimum version: {self.config.datasets_version}"
                )
            
            # Check for required features
            if not self._check_required_features():
                raise DatasetsVersionError(
                    "Datasets version does not support required features (--dehydrated, rehydrate)"
                )
                
            self.logger.info(f"Datasets version {version_info['version']} validated successfully")
            
        except subprocess.TimeoutExpired:
            raise DatasetsVersionError("Datasets executable timed out during version check")
        except Exception as e:
            raise DatasetsVersionError(f"Failed to validate datasets version: {e}")
    
    def get_version_info(self) -> Dict[str, Any]:
        """
        Get version information from datasets executable.
        
        Returns:
            Dictionary with version information
        """
        result = {
            'available': False,
            'version': None,
            'version_compatible': False,
            'raw_output': None
        }
        
        try:
            proc_result = subprocess.run(
                [self._resolved_executable, "--version"],
                capture_output=True,
                text=True,
                timeout=10,
                shell=False  # Use shell=False for security
            )
            
            result['raw_output'] = proc_result.stdout.strip()
            
            if proc_result.returncode == 0:
                result['available'] = True
                
                # Extract version from output
                version_output = proc_result.stdout.strip()
                if 'version:' in version_output:
                    version_part = version_output.split('version:')[1].strip().split()[0]
                    result['version'] = version_part
                    
                    # Simple version comparison (assumes semantic versioning)
                    try:
                        current_version = tuple(map(int, version_part.split('.')))
                        required_version = tuple(map(int, self.config.datasets_version.split('.')))
                        result['version_compatible'] = current_version >= required_version
                    except ValueError:
                        # If version parsing fails, assume compatible
                        result['version_compatible'] = True
                        self.logger.warning(f"Could not parse version '{version_part}', assuming compatible")
                else:
                    # If we can't parse version, assume compatible
                    result['version_compatible'] = True
                    self.logger.warning("Could not parse version from datasets output, assuming compatible")
                    
        except Exception as e:
            self.logger.error(f"Failed to get version info: {e}")
        
        return result
    
    def _check_required_features(self) -> bool:
        """
        Check if datasets executable supports required features.
        
        Returns:
            True if all required features are supported
        """
        try:
            # Check if --dehydrated flag is supported in genome download command
            help_result = subprocess.run(
                [self._resolved_executable, "download", "genome", "--help"],
                capture_output=True,
                text=True,
                timeout=10,
                shell=False
            )
            
            if help_result.returncode == 0:
                help_text = help_result.stdout.lower()
                has_dehydrated = '--dehydrated' in help_text
                
                # Check if rehydrate command exists
                rehydrate_result = subprocess.run(
                    [self._resolved_executable, "rehydrate", "--help"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    shell=False
                )
                
                has_rehydrate = rehydrate_result.returncode == 0
                
                return has_dehydrated and has_rehydrate
            
        except Exception as e:
            self.logger.warning(f"Could not check required features: {e}")
        
        return False
    
    def build_download_command(self, taxon: str, output_file: str, dehydrated: bool = True) -> List[str]:
        """
        Build datasets download command for a taxon.
        
        Args:
            taxon: Taxonomy name or TaxID
            output_file: Output filename for the download
            dehydrated: Whether to download dehydrated package
            
        Returns:
            Command as list of strings
        """
        # Base command with resolved executable path - use genome instead of taxonomy for genome downloads
        cmd = [self._resolved_executable, "download", "genome", "taxon"]
        
        # shell=False passes each list element as a single argument, so quoting
        # here would make quotes part of the taxon value.
        cmd.append(taxon)
        
        # Add filename parameter
        cmd.extend(["--filename", output_file])
        
        # Add dehydrated flag if requested
        if dehydrated:
            cmd.append("--dehydrated")
        
        # Add configuration parameters
        params = self.config.format_datasets_params()
        for key, value in params.items():
            cmd.extend([f"--{key}", value])
        
        return cmd
    
    def build_rehydrate_command(self, directory: str) -> List[str]:
        """
        Build datasets rehydrate command.
        
        Args:
            directory: Directory containing dehydrated package
            
        Returns:
            Command as list of strings
        """
        cmd = [self._resolved_executable, "rehydrate", "--directory", directory]
        
        # Add API key if available
        if self.config.api_key:
            cmd.extend(["--api-key", self.config.api_key])
        
        return cmd
    
    def execute_command(self, cmd: List[str], timeout: Optional[int] = None, use_rate_limit: bool = True) -> subprocess.CompletedProcess:
        """
        Execute a datasets command with platform-specific subprocess handling, rate limiting, and retry logic.
        
        Args:
            cmd: Command to execute as list of strings
            timeout: Timeout in seconds (None for no timeout)
            use_rate_limit: Whether to apply rate limiting
            
        Returns:
            CompletedProcess result
        """
        def _execute_once():
            # Apply rate limiting if requested
            if use_rate_limit:
                # Use 60s timeout for rate limiter acquisition, independent of command timeout
                if not self.rate_limiter.acquire(timeout=60):
                    raise TimeoutError("Rate limit acquisition timeout exceeded: 60s")
            
            safe_cmd = self._redact_command(cmd)
            self.logger.debug(f"Executing command: {' '.join(safe_cmd)}")
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,  # None means no timeout
                shell=False,  # Use shell=False for security
                check=False   # Don't raise exception on non-zero exit
            )
            
            # Check for rate limit errors in output (check both stdout and stderr)
            if result.returncode != 0:
                # Combine stdout and stderr for error analysis
                combined_output = (result.stdout + result.stderr).lower()
                
                # Only treat as rate limit error if it's actually about rate limiting
                # Exclude false positives like "no genome data" errors
                is_rate_limit_error = (
                    any(keyword in combined_output for keyword in ['rate limit', 'too many requests', '429']) and
                    not any(exclude in combined_output for exclude in [
                        'no genome data',
                        'is valid for',
                        'please use a subspecies',
                        'no annotations matching'
                    ])
                )
                
                if is_rate_limit_error:
                    raise Exception(f"Rate limit error: {result.stderr or result.stdout}")
                
                self.logger.warning(f"Command failed with exit code {result.returncode}: {result.stderr or result.stdout}")
            else:
                self.logger.debug(f"Command succeeded: {result.stdout}")
            
            return result
        
        try:
            # Execute with retry logic for transient errors
            return self.retry_handler.execute_with_retry(_execute_once)
            
        except subprocess.TimeoutExpired as e:
            timeout_msg = f"{timeout} seconds" if timeout else "unlimited"
            safe_cmd = self._redact_command(cmd)
            self.logger.error(f"Command timed out after {timeout_msg}: {' '.join(safe_cmd)}")
            raise
        except FileNotFoundError as e:
            self.logger.error(f"Executable not found: {cmd[0]}")
            raise
        except Exception as e:
            self.logger.error(f"Command execution failed: {e}")
            raise

    @staticmethod
    def _redact_command(cmd: List[str]) -> List[str]:
        """Return a command list with sensitive values redacted for logging."""
        redacted = []
        hide_next = False

        for part in cmd:
            if hide_next:
                redacted.append("***REDACTED***")
                hide_next = False
                continue

            if part.startswith("--api-key="):
                redacted.append("--api-key=***REDACTED***")
                continue

            redacted.append(part)
            if part == "--api-key":
                hide_next = True

        return redacted
    
    def download_taxonomy_dehydrated(self, taxon: str, output_file: str) -> bool:
        """
        Download dehydrated package for a taxon with rate limiting and retry logic.
        
        Args:
            taxon: Taxonomy name or TaxID
            output_file: Output filename
            
        Returns:
            True if successful, False otherwise
        """
        cmd = self.build_download_command(taxon, output_file, dehydrated=True)
        
        try:
            result = self.execute_command(cmd, use_rate_limit=True)
            success = result.returncode == 0
            
            if success:
                self.logger.info(f"Successfully downloaded dehydrated package for '{taxon}'")
            else:
                self.logger.error(f"Failed to download dehydrated package for '{taxon}': {result.stderr}")
            
            return success
        except Exception as e:
            self.logger.error(f"Exception during download of '{taxon}': {e}")
            return False
    
    def rehydrate_package(self, directory: str) -> bool:
        """
        Rehydrate a dehydrated package with rate limiting and retry logic.
        
        Args:
            directory: Directory containing dehydrated package
            
        Returns:
            True if successful, False otherwise
        """
        cmd = self.build_rehydrate_command(directory)
        
        try:
            result = self.execute_command(cmd, use_rate_limit=True)
            success = result.returncode == 0
            
            if success:
                self.logger.info(f"Successfully rehydrated package in '{directory}'")
            else:
                self.logger.error(f"Failed to rehydrate package in '{directory}': {result.stderr}")
            
            return success
        except Exception as e:
            self.logger.error(f"Exception during rehydration of '{directory}': {e}")
            return False
    
    def supports_taxid(self, taxon: str) -> bool:
        """
        Check if the provided taxon appears to be a TaxID.
        
        Args:
            taxon: Taxonomy name or TaxID
            
        Returns:
            True if taxon appears to be a TaxID (numeric)
        """
        return taxon.strip().isdigit()
    
    def update_rate_limit(self, requests_per_second: float) -> None:
        """
        Update the rate limit for API requests.
        
        Args:
            requests_per_second: New rate limit
        """
        self.rate_limiter.update_rate(requests_per_second)
        self.config.rate_limit_per_second = requests_per_second
        self.logger.info(f"Updated rate limit to {requests_per_second} requests/second")
    
    def get_rate_limit_info(self) -> Dict[str, Any]:
        """
        Get current rate limiting information.
        
        Returns:
            Dictionary with rate limit info
        """
        return {
            'requests_per_second': self.rate_limiter.requests_per_second,
            'interval': self.rate_limiter.interval,
            'has_api_key': self.config.api_key is not None
        }
