"""Task splitter for dividing genome download tasks into manageable chunks.

This module implements the core splitting algorithms for dividing large
genome download tasks by taxonomy level and size constraints.
"""

from typing import List, Tuple

from .genome_mapper import GenomeMapper
from .name_filter import BinomialNameFilter
from .split_models import SplitConfig, SplitResult
from .taxonomy_tree import TaxonomyTree


class TaskSplitter:
    """Splits genome download tasks by taxonomy and size constraints.
    
    This class implements the primary splitting algorithm that groups taxons
    into bins based on size constraints, and handles secondary splitting of
    oversized taxons by individual genome accessions.
    """
    
    def __init__(self, mapper: GenomeMapper, tree: TaxonomyTree):
        """Initialize the task splitter.
        
        Args:
            mapper: GenomeMapper instance with built mapping
            tree: TaxonomyTree instance with built tree structure
        """
        self._mapper = mapper
        self._tree = tree
    
    def split_by_taxon(self, config: SplitConfig) -> SplitResult:
        """按taxon进行一次分割
        
        Performs primary splitting by grouping taxons at the specified
        taxonomy level into bins that don't exceed the size threshold.
        
        Now includes species that have no direct genomes but have strain-level genomes.
        
        Args:
            config: SplitConfig with taxonomy level, filters, and size threshold
            
        Returns:
            SplitResult containing groups and exceeded taxons
        """
        # Convert split size from Gb to bytes
        split_size_bytes = int(config.split_size_gb * 1_000_000_000)
        
        # Get all nodes at the target taxonomy level
        nodes = self._tree.get_nodes_at_rank(config.taxonomy_level)
        
        # Build list of (taxon_name, tax_id, size_bytes) tuples
        taxon_data: List[Tuple[str, int, int]] = []
        
        for node in nodes:
            # Check if taxon has direct genomes OR strain-level genomes
            has_direct = self._mapper.has_direct_genomes(node.tax_id, config.data_source)
            has_strain = self._mapper.has_strain_level_genomes(node.tax_id, config.data_source)
            
            # Skip taxons that have neither direct nor strain-level genome data
            if not has_direct and not has_strain:
                continue
            
            # Calculate total size for this taxon (including all descendants)
            # This includes both species-level and strain-level genomes
            total_size = self._mapper.calculate_total_size(
                node.tax_id, 
                config.data_source
            )
            
            # Only include taxons with genome data
            if total_size > 0:
                taxon_name = node.name
                
                # Apply name filter if enabled
                if config.enable_name_filter:
                    name_filter = BinomialNameFilter()
                    if not name_filter.is_valid_binomial(taxon_name):
                        continue
                
                taxon_data.append((taxon_name, node.tax_id, total_size))
        
        # Use bin packing algorithm to create groups
        groups, exceeded = self._bin_packing_with_metadata(taxon_data, split_size_bytes)
        
        return SplitResult(groups=groups, exceeded=exceeded)
    
    def split_exceeded_by_accession(
        self, 
        taxon_name: str, 
        tax_id: int,
        split_size_bytes: int,
        source_filter: str
    ) -> List[List[str]]:
        """对超限taxon按accession进行二次分割
        
        Performs secondary splitting for oversized taxons by grouping
        individual genome accessions into bins.
        
        Args:
            taxon_name: Name of the exceeded taxon
            tax_id: Taxonomic ID of the exceeded taxon
            split_size_bytes: Size threshold in bytes
            source_filter: Data source filter ('refseq', 'genbank', 'all')
            
        Returns:
            List of accession groups, each group is a list of accession strings
        """
        # Get all genomes for this taxon
        genomes = self._mapper.get_genomes_for_taxon(tax_id, source_filter)
        
        # Build list of (accession, size_bytes) tuples
        accession_sizes = [
            (genome.accession, genome.sequence_length)
            for genome in genomes
        ]
        
        # Use bin packing to create accession groups
        groups, exceeded = self._bin_packing(accession_sizes, split_size_bytes)
        
        # Note: Individual genomes that exceed the threshold are still included
        # in groups (as single-item groups) since we can't split further
        # The exceeded list from bin_packing represents items too large to fit,
        # but for accessions, we still need to include them
        if exceeded:
            # Add each exceeded accession as its own group
            for accession, _ in exceeded:
                groups.append([accession])
        
        return groups
    
    def _bin_packing(
        self, 
        items: List[Tuple[str, int]], 
        bin_capacity: int
    ) -> Tuple[List[List[str]], List[Tuple[str, int]]]:
        """贪心装箱算法
        
        Implements First Fit Decreasing bin packing algorithm to group
        items into bins that don't exceed the capacity.
        
        Args:
            items: List of (name, size_bytes) tuples to pack
            bin_capacity: Maximum capacity per bin in bytes
            
        Returns:
            Tuple of (groups, exceeded) where:
            - groups: List of bins, each containing item names
            - exceeded: List of items that exceed bin capacity
        """
        # Separate exceeded items (larger than bin capacity)
        exceeded = [(name, size) for name, size in items if size > bin_capacity]
        normal = [(name, size) for name, size in items if size <= bin_capacity]
        
        # Sort by size in descending order (First Fit Decreasing)
        normal.sort(key=lambda x: x[1], reverse=True)
        
        # Greedy bin packing
        bins: List[Tuple[List[str], int]] = []  # [(names, current_size), ...]
        
        for name, size in normal:
            placed = False
            
            # Try to fit into an existing bin
            for i, (names, current_size) in enumerate(bins):
                if current_size + size <= bin_capacity:
                    names.append(name)
                    bins[i] = (names, current_size + size)
                    placed = True
                    break
            
            # If doesn't fit anywhere, create a new bin
            if not placed:
                bins.append(([name], size))
        
        # Extract just the names from bins
        groups = [names for names, _ in bins]
        
        return groups, exceeded
    
    def _bin_packing_with_metadata(
        self, 
        items: List[Tuple[str, int, int]], 
        bin_capacity: int
    ) -> Tuple[List[List[Tuple[str, int, int]]], List[Tuple[str, int, int]]]:
        """贪心装箱算法（包含元数据）
        
        Implements First Fit Decreasing bin packing algorithm with metadata.
        
        Args:
            items: List of (name, tax_id, size_bytes) tuples to pack
            bin_capacity: Maximum capacity per bin in bytes
            
        Returns:
            Tuple of (groups, exceeded) where:
            - groups: List of bins, each containing (name, tax_id, size_bytes) tuples
            - exceeded: List of items that exceed bin capacity
        """
        # Separate exceeded items (larger than bin capacity)
        exceeded = [(name, tid, size) for name, tid, size in items if size > bin_capacity]
        normal = [(name, tid, size) for name, tid, size in items if size <= bin_capacity]
        
        # Sort by size in descending order (First Fit Decreasing)
        normal.sort(key=lambda x: x[2], reverse=True)
        
        # Greedy bin packing
        bins: List[Tuple[List[Tuple[str, int, int]], int]] = []  # [(items, current_size), ...]
        
        for name, tid, size in normal:
            placed = False
            
            # Try to fit into an existing bin
            for i, (bin_items, current_size) in enumerate(bins):
                if current_size + size <= bin_capacity:
                    bin_items.append((name, tid, size))
                    bins[i] = (bin_items, current_size + size)
                    placed = True
                    break
            
            # If doesn't fit anywhere, create a new bin
            if not placed:
                bins.append(([(name, tid, size)], size))
        
        # Extract just the items from bins
        groups = [bin_items for bin_items, _ in bins]
        
        return groups, exceeded

