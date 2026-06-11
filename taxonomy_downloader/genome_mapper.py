"""Genome mapper for building taxId to genome associations."""

from typing import Dict, List, Set

from .split_models import GenomeRecord
from .taxonomy_tree import TaxonomyTree


class GenomeMapper:
    """Maps taxonomy nodes to their associated genomes.
    
    This class builds and maintains the relationship between taxonomic IDs
    and genome records, supporting filtering by data source and size calculations.
    """
    
    def __init__(self, genomes: List[GenomeRecord], tree: TaxonomyTree):
        """Initialize the genome mapper.
        
        Args:
            genomes: List of GenomeRecord objects
            tree: TaxonomyTree instance with built tree structure
        """
        self._genomes = genomes
        self._tree = tree
        self._taxid_to_genomes: Dict[int, List[GenomeRecord]] = {}
    
    def build_mapping(self) -> None:
        """Build taxId to genome list mapping.
        
        Creates a dictionary that maps each taxonomic ID to the list of
        genomes directly associated with that taxId (not including descendants).
        """
        self._taxid_to_genomes = {}
        
        for genome in self._genomes:
            tax_id = genome.tax_id
            if tax_id not in self._taxid_to_genomes:
                self._taxid_to_genomes[tax_id] = []
            self._taxid_to_genomes[tax_id].append(genome)
    
    def get_genomes_for_taxon(self, tax_id: int, source_filter: str = 'all') -> List[GenomeRecord]:
        """Get all genomes for a taxon and its descendants.
        
        Retrieves genomes associated with the specified taxon and all of its
        descendant nodes in the taxonomy tree, with optional data source filtering.
        
        Args:
            tax_id: The taxonomic ID to query
            source_filter: Data source filter ('refseq', 'genbank', 'all')
            
        Returns:
            List of GenomeRecord objects matching the criteria
        """
        # Get all descendant taxIds (including the taxon itself)
        descendant_ids = self._tree.get_all_descendant_ids(tax_id)
        
        # Collect all genomes from these taxIds
        all_genomes = []
        for desc_id in descendant_ids:
            if desc_id in self._taxid_to_genomes:
                all_genomes.extend(self._taxid_to_genomes[desc_id])
        
        # Apply data source filter
        return self._apply_source_filter(all_genomes, source_filter)
    
    def calculate_total_size(self, tax_id: int, source_filter: str = 'all') -> int:
        """Calculate total genome size for a taxon.
        
        Aggregates genome sizes from the taxon and all its descendants,
        applying the specified data source filter.
        
        Args:
            tax_id: The taxonomic ID to calculate size for
            source_filter: Data source filter ('refseq', 'genbank', 'all')
            
        Returns:
            Total genome size in base pairs (bp)
        """
        genomes = self.get_genomes_for_taxon(tax_id, source_filter)
        return sum(genome.sequence_length for genome in genomes)
    
    def has_direct_genomes(self, tax_id: int, source_filter: str = 'all') -> bool:
        """Check if a taxon has direct genome data (not from descendants).
        
        This method checks whether the specified taxId has genome records
        directly associated with it, excluding any genomes from descendant nodes.
        
        Args:
            tax_id: The taxonomic ID to check
            source_filter: Data source filter ('refseq', 'genbank', 'all')
            
        Returns:
            True if the taxon has at least one direct genome record
        """
        if tax_id not in self._taxid_to_genomes:
            return False
        
        direct_genomes = self._taxid_to_genomes[tax_id]
        filtered = self._apply_source_filter(direct_genomes, source_filter)
        return len(filtered) > 0
    
    def has_strain_level_genomes(self, tax_id: int, source_filter: str = 'all') -> bool:
        """Check if a taxon has genome data at strain level (from descendants).
        
        This method checks whether the specified taxId has genome records
        from descendant nodes (e.g., strain level), excluding direct genomes.
        
        Args:
            tax_id: The taxonomic ID to check
            source_filter: Data source filter ('refseq', 'genbank', 'all')
            
        Returns:
            True if the taxon has at least one genome from descendant nodes
        """
        # Get all descendant taxIds (excluding the taxon itself)
        descendant_ids = self._tree.get_all_descendant_ids(tax_id)
        descendant_ids.discard(tax_id)  # Remove the taxon itself
        
        # Check if any descendant has genomes
        for desc_id in descendant_ids:
            if desc_id in self._taxid_to_genomes:
                desc_genomes = self._taxid_to_genomes[desc_id]
                filtered = self._apply_source_filter(desc_genomes, source_filter)
                if len(filtered) > 0:
                    return True
        
        return False
    
    def _apply_source_filter(self, genomes: List[GenomeRecord], source_filter: str) -> List[GenomeRecord]:
        """Apply data source filter to genome list.
        
        Filters genomes based on their accession prefix:
        - 'refseq': Only GCF_ prefixed accessions
        - 'genbank': Only GCA_ prefixed accessions
        - 'all': No filtering
        
        Args:
            genomes: List of GenomeRecord objects to filter
            source_filter: Filter type ('refseq', 'genbank', 'all')
            
        Returns:
            Filtered list of GenomeRecord objects
        """
        if source_filter == 'refseq':
            return [g for g in genomes if g.accession.startswith('GCF_')]
        elif source_filter == 'genbank':
            return [g for g in genomes if g.accession.startswith('GCA_')]
        else:  # 'all'
            return genomes
