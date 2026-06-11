"""
Progress management and resume functionality for the taxonomy downloader.
"""

import json
import os
import time
from pathlib import Path
from typing import Set, List, Optional
from .models import ProgressState
from .logging_config import get_logger
from .file_type_utils import detect_file_type

logger = get_logger(__name__)


class ProgressManager:
    """Manages download progress state and resume functionality."""
    
    def __init__(self, state_file: str):
        """
        Initialize progress manager with state file path.
        
        Args:
            state_file: Path to the JSON file storing progress state
        """
        self.state_file = Path(state_file)
        self.state: Optional[ProgressState] = None
        self._ensure_state_directory()
    
    def _ensure_state_directory(self) -> None:
        """Ensure the directory for the state file exists."""
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
    
    def load_state(self) -> Set[str]:
        """
        Load progress state from file.
        
        Returns:
            Set of completed taxa names
        """
        if not self.state_file.exists():
            logger.info(f"No existing progress state found at {self.state_file}")
            self.state = ProgressState(
                completed_taxa=set(),
                failed_taxa=set(),
                start_time=time.time(),
                last_update=time.time()
            )
            return set()
        
        try:
            with open(self.state_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            # Convert lists back to sets
            completed_taxa = set(data.get('completed_taxa', []))
            failed_taxa = set(data.get('failed_taxa', []))
            
            self.state = ProgressState(
                completed_taxa=completed_taxa,
                failed_taxa=failed_taxa,
                start_time=data.get('start_time', time.time()),
                last_update=data.get('last_update', time.time())
            )
            
            logger.info(f"Loaded progress state: {len(completed_taxa)} completed, "
                       f"{len(failed_taxa)} failed taxa")
            
            return completed_taxa
            
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning(f"Failed to load progress state from {self.state_file}: {e}")
            logger.info("Starting with fresh progress state")
            self.state = ProgressState(
                completed_taxa=set(),
                failed_taxa=set(),
                start_time=time.time(),
                last_update=time.time()
            )
            return set()
    
    def save_completed_taxon(self, taxon: str) -> None:
        """
        Mark a taxon as completed and save state.
        
        Args:
            taxon: Name of the successfully processed taxon
        """
        if self.state is None:
            self.load_state()
        
        self.state.completed_taxa.add(taxon)
        # Remove from failed if it was previously failed
        self.state.failed_taxa.discard(taxon)
        self.state.last_update = time.time()
        
        self._save_state()
        logger.debug(f"Marked taxon '{taxon}' as completed")
    
    def save_failed_taxon(self, taxon: str) -> None:
        """
        Mark a taxon as failed and save state.
        
        Args:
            taxon: Name of the failed taxon
        """
        if self.state is None:
            self.load_state()
        
        self.state.failed_taxa.add(taxon)
        self.state.last_update = time.time()
        
        self._save_state()
        logger.debug(f"Marked taxon '{taxon}' as failed")
    
    def _save_state(self) -> None:
        """Save current state to file."""
        if self.state is None:
            return
        
        try:
            # Convert sets to lists for JSON serialization
            state_data = {
                'completed_taxa': list(self.state.completed_taxa),
                'failed_taxa': list(self.state.failed_taxa),
                'start_time': self.state.start_time,
                'last_update': self.state.last_update
            }
            
            # Write to temporary file first, then rename for atomicity
            temp_file = self.state_file.with_suffix('.tmp')
            with open(temp_file, 'w', encoding='utf-8') as f:
                json.dump(state_data, f, indent=2)
            
            # Atomic rename
            temp_file.replace(self.state_file)
            
        except (OSError, TypeError) as e:
            logger.error(f"Failed to save progress state to {self.state_file}: {e}")
    
    def get_remaining_taxa(self, all_taxa: List[str]) -> List[str]:
        """
        Get list of taxa that still need to be processed.
        
        Args:
            all_taxa: Complete list of taxa to process
            
        Returns:
            List of taxa that haven't been completed yet
        """
        if self.state is None:
            self.load_state()
        
        remaining = [taxon for taxon in all_taxa 
                    if taxon not in self.state.completed_taxa]
        
        logger.info(f"Found {len(remaining)} remaining taxa out of {len(all_taxa)} total")
        
        return remaining
    
    def validate_existing_files(
        self,
        output_dir: str,
        completed_taxa: Set[str],
        include_params: Optional[List[str]] = None,
    ) -> Set[str]:
        """
        Validate that output files exist for completed taxa.
        
        Args:
            output_dir: Base output directory
            completed_taxa: Set of taxa marked as completed
            include_params: Requested include types for this run
            
        Returns:
            Set of taxa that still have valid output files
        """
        output_path = Path(output_dir)
        valid_completed = set()
        
        for taxon in completed_taxa:
            # Sanitize taxon name for directory (same logic as FileOrganizer)
            sanitized_name = self._sanitize_taxon_name(taxon)
            taxon_dir = output_path / sanitized_name
            
            if taxon_dir.exists() and taxon_dir.is_dir():
                # Check if directory has any files matching the requested include types
                data_files = [
                    file_path
                    for file_path in taxon_dir.iterdir()
                    if file_path.is_file()
                    and detect_file_type(file_path.name, include_params or ["genome"])
                ]
                if data_files:
                    valid_completed.add(taxon)
                    logger.debug(f"Validated existing files for taxon '{taxon}'")
                else:
                    logger.warning(
                        f"No files matching include types {include_params or ['genome']} "
                        f"found for completed taxon '{taxon}', will re-process"
                    )
            else:
                logger.warning(f"Output directory missing for completed taxon '{taxon}', "
                             f"will re-process")
        
        # Update state to remove invalid completed taxa
        if self.state and len(valid_completed) < len(completed_taxa):
            invalid_taxa = completed_taxa - valid_completed
            logger.info(f"Removing {len(invalid_taxa)} invalid completed taxa from state")
            self.state.completed_taxa = valid_completed
            self._save_state()
        
        return valid_completed
    
    def _sanitize_taxon_name(self, taxon: str) -> str:
        """
        Sanitize taxon name for filesystem compatibility.
        Same logic as FileOrganizer to ensure consistency.
        
        Args:
            taxon: Original taxon name
            
        Returns:
            Sanitized name safe for filesystem use
        """
        # Replace spaces with underscores
        sanitized = taxon.replace(' ', '_')
        
        # Remove or replace problematic characters
        problematic_chars = '<>:"/\\|?*'
        for char in problematic_chars:
            sanitized = sanitized.replace(char, '_')
        
        # Remove multiple consecutive underscores
        while '__' in sanitized:
            sanitized = sanitized.replace('__', '_')
        
        # Remove leading/trailing underscores
        sanitized = sanitized.strip('_')
        
        # Ensure not empty
        if not sanitized:
            sanitized = 'unknown_taxon'
        
        return sanitized
    
    def get_progress_summary(self) -> dict:
        """
        Get current progress summary.
        
        Returns:
            Dictionary with progress statistics
        """
        if self.state is None:
            self.load_state()
        
        return {
            'completed_count': len(self.state.completed_taxa),
            'failed_count': len(self.state.failed_taxa),
            'start_time': self.state.start_time,
            'last_update': self.state.last_update,
            'elapsed_time': time.time() - self.state.start_time
        }
    
    def cleanup(self) -> None:
        """
        Clean up progress state file after successful completion.
        """
        try:
            if self.state_file.exists():
                self.state_file.unlink()
                logger.info(f"Cleaned up progress state file: {self.state_file}")
        except OSError as e:
            logger.warning(f"Failed to clean up progress state file: {e}")
    
    def reset_state(self) -> None:
        """Reset progress state (useful for testing or forced restart)."""
        self.state = ProgressState(
            completed_taxa=set(),
            failed_taxa=set(),
            start_time=time.time(),
            last_update=time.time()
        )
        self._save_state()
        logger.info("Reset progress state")
