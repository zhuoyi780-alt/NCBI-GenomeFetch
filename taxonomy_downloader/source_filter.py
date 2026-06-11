"""
Data source filter module for filtering genomes by database origin.

This module provides functionality to filter genome records based on their
accession prefix (RefSeq vs GenBank).
"""

from typing import List
from taxonomy_downloader.split_models import GenomeRecord


class SourceFilter:
    """数据源过滤器
    
    Filters genome records based on their database source (RefSeq or GenBank).
    
    RefSeq genomes have accessions starting with GCF_
    GenBank genomes have accessions starting with GCA_
    """
    
    @staticmethod
    def filter_by_source(genomes: List[GenomeRecord], source: str) -> List[GenomeRecord]:
        """按accession前缀过滤基因组
        
        Filters genome records based on the specified data source.
        
        Args:
            genomes: List of genome records to filter
            source: Data source filter - 'refseq', 'genbank', or 'all'
                   - 'refseq': Only include genomes with GCF_ prefix
                   - 'genbank': Only include genomes with GCA_ prefix
                   - 'all': Include all genomes
                   
        Returns:
            Filtered list of genome records
            
        Raises:
            ValueError: If source is not one of 'refseq', 'genbank', or 'all'
        """
        source_lower = source.lower()
        
        if source_lower not in ['refseq', 'genbank', 'all']:
            raise ValueError(
                f"Invalid source '{source}'. Must be 'refseq', 'genbank', or 'all'"
            )
        
        if source_lower == 'all':
            return genomes
        
        if source_lower == 'refseq':
            return [g for g in genomes if g.accession.startswith('GCF_')]
        
        # source_lower == 'genbank'
        return [g for g in genomes if g.accession.startswith('GCA_')]
