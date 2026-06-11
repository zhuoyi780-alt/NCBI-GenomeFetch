"""Data models for task splitting functionality."""

from dataclasses import dataclass, field
from typing import Dict, List, Tuple


@dataclass
class GenomeRecord:
    """Represents a genome assembly record from the Assembly Report.
    
    Attributes:
        accession: Assembly Accession (GCA_/GCF_)
        organism_name: Organism Name
        tax_id: Organism Taxonomic ID
        sequence_length: Total Sequence Length in base pairs (bp)
    """
    accession: str
    organism_name: str
    tax_id: int
    sequence_length: int


@dataclass
class TaxonomyNode:
    """Represents a node in the taxonomy tree.
    
    Attributes:
        tax_id: Taxonomic ID
        name: Taxon name
        rank: Taxonomic rank (KINGDOM, PHYLUM, CLASS, ORDER, FAMILY, GENUS, SPECIES)
        classification: Dictionary mapping rank level to (name, id) tuple
        parent_ids: List of parent taxonomic IDs
        child_ids: List of child taxonomic IDs
    """
    tax_id: int
    name: str
    rank: str
    classification: Dict[str, Tuple[str, int]] = field(default_factory=dict)
    parent_ids: List[int] = field(default_factory=list)
    child_ids: List[int] = field(default_factory=list)


@dataclass
class SplitConfig:
    """Configuration for task splitting.
    
    Attributes:
        taxonomy_level: Selected taxonomy level for splitting
        enable_name_filter: Whether to apply binomial name filtering
        data_source: Data source filter ('refseq', 'genbank', 'all')
        split_size_gb: Split size threshold in gigabases (Gb, 10^9 bp)
    """
    taxonomy_level: str
    enable_name_filter: bool
    data_source: str
    split_size_gb: float


@dataclass
class SplitResult:
    """Result of task splitting operation.
    
    Attributes:
        groups: List of groups, each containing (taxon_name, tax_id, size_bytes) tuples
        exceeded: List of exceeded taxons with (name, tax_id, size_bytes) tuples
    """
    groups: List[List[Tuple[str, int, int]]] = field(default_factory=list)
    exceeded: List[Tuple[str, int, int]] = field(default_factory=list)
