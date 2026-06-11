"""Taxonomy tree builder for hierarchical taxonomy operations."""

from typing import Dict, List, Set, Tuple

from .split_models import TaxonomyNode


# Standard taxonomy rank hierarchy (from highest to lowest)
RANK_HIERARCHY = [
    'SUPERKINGDOM',
    'KINGDOM',
    'PHYLUM',
    'CLASS',
    'ORDER',
    'FAMILY',
    'GENUS',
    'SPECIES'
]


class TaxonomyTree:
    """Builds and queries a taxonomy tree structure.
    
    This class provides methods to navigate the taxonomy hierarchy,
    find nodes at specific ranks, and calculate descendant relationships.
    """
    
    def __init__(self, nodes: Dict[int, TaxonomyNode]):
        """Initialize the taxonomy tree.
        
        Args:
            nodes: Dictionary mapping taxId to TaxonomyNode
        """
        self._nodes = nodes
        self._children_map: Dict[int, Set[int]] = {}
        
    def build_tree(self) -> None:
        """Build parent-child relationship index.
        
        This method constructs a reverse mapping from parent IDs to their
        children, enabling efficient traversal down the tree.
        """
        self._children_map = {}
        
        for tax_id, node in self._nodes.items():
            # For each parent of this node, add this node as a child
            for parent_id in node.parent_ids:
                if parent_id not in self._children_map:
                    self._children_map[parent_id] = set()
                self._children_map[parent_id].add(tax_id)
    
    def get_nodes_at_rank(self, rank: str) -> List[TaxonomyNode]:
        """Get all nodes at the specified taxonomic rank.
        
        Uses strict rank matching - only returns nodes whose rank field
        exactly matches the specified level (case-insensitive).
        
        Args:
            rank: Taxonomic rank (e.g., 'GENUS', 'SPECIES')
            
        Returns:
            List of TaxonomyNode objects at the specified rank
        """
        rank_upper = rank.upper()
        return [
            node for node in self._nodes.values()
            if node.rank.upper() == rank_upper
        ]
    
    def get_all_descendant_ids(self, tax_id: int) -> Set[int]:
        """Get all descendant taxIds for a given node.
        
        This performs a recursive traversal down the tree to collect
        all descendants (children, grandchildren, etc.).
        
        Args:
            tax_id: The taxonomic ID to find descendants for
            
        Returns:
            Set of all descendant taxIds (including the node itself)
        """
        descendants = {tax_id}
        
        # Get direct children
        children = self._children_map.get(tax_id, set())
        
        # Recursively get descendants of each child
        for child_id in children:
            descendants.update(self.get_all_descendant_ids(child_id))
        
        return descendants
    
    def get_available_ranks(self) -> List[Tuple[str, int]]:
        """Get all available taxonomy ranks with node counts.
        
        Returns ranks sorted by hierarchy (Kingdom → Species) with the
        number of nodes at each rank.
        
        Returns:
            List of (rank_name, node_count) tuples, sorted by hierarchy
        """
        # Count nodes at each rank
        rank_counts: Dict[str, int] = {}
        for node in self._nodes.values():
            rank_upper = node.rank.upper()
            if rank_upper:  # Skip empty ranks
                rank_counts[rank_upper] = rank_counts.get(rank_upper, 0) + 1
        
        # Sort by hierarchy
        sorted_ranks = []
        for rank in RANK_HIERARCHY:
            if rank in rank_counts:
                sorted_ranks.append((rank, rank_counts[rank]))
        
        # Add any ranks not in the standard hierarchy (at the end)
        for rank, count in sorted(rank_counts.items()):
            if rank not in RANK_HIERARCHY:
                sorted_ranks.append((rank, count))
        
        return sorted_ranks
    
    def get_rank_for_taxid(self, tax_id: int, target_rank: str) -> Tuple[str, int]:
        """Get the taxon at the target rank for a given taxId.
        
        Traverses the classification hierarchy to find the taxon at the
        specified rank. If the rank is missing in the classification path,
        returns "unknown_{rank}" with taxId -1.
        
        Args:
            tax_id: The taxonomic ID to query
            target_rank: The target rank to find (e.g., 'genus', 'species')
            
        Returns:
            Tuple of (taxon_name, taxon_id) at the target rank,
            or ("unknown_{rank}", -1) if the rank is missing
        """
        if tax_id not in self._nodes:
            return (f"unknown_{target_rank.lower()}", -1)
        
        node = self._nodes[tax_id]
        target_rank_lower = target_rank.lower()
        
        # Check if the target rank exists in the classification
        if target_rank_lower in node.classification:
            name, rank_id = node.classification[target_rank_lower]
            return (name, rank_id)
        
        # Rank is missing - return unknown
        return (f"unknown_{target_rank_lower}", -1)
