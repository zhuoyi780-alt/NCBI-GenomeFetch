"""File parser for taxonomy and assembly data files."""

import csv
import json
from pathlib import Path
from typing import Dict, List

from .split_models import GenomeRecord, TaxonomyNode


class FileParser:
    """Parser for taxonomy and assembly report files."""
    
    def parse_assembly_report(self, filepath: Path) -> List[GenomeRecord]:
        """Parse the Assembly Report TSV file.
        
        Args:
            filepath: Path to the {taxon}.tsv file
            
        Returns:
            List of GenomeRecord objects
            
        Raises:
            FileNotFoundError: If the file doesn't exist
            ValueError: If the file format is invalid
        """
        if not filepath.exists():
            raise FileNotFoundError(f"Assembly report file not found: {filepath}")
        
        genomes = []
        
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f, delimiter='\t')
                
                for line_num, row in enumerate(reader, start=2):  # Start at 2 (header is line 1)
                    try:
                        # Extract required fields
                        accession = row.get('Assembly Accession', '').strip()
                        organism_name = row.get('Organism Name', '').strip()
                        tax_id_str = row.get('Organism Taxonomic ID', '').strip()
                        seq_length_str = row.get('Assembly Stats Total Sequence Length', '').strip()
                        
                        # Validate required fields
                        if not accession:
                            continue  # Skip rows without accession
                        
                        if not tax_id_str or not seq_length_str:
                            continue  # Skip rows with missing data
                        
                        # Parse numeric fields
                        tax_id = int(tax_id_str)
                        sequence_length = int(seq_length_str)
                        
                        genome = GenomeRecord(
                            accession=accession,
                            organism_name=organism_name,
                            tax_id=tax_id,
                            sequence_length=sequence_length
                        )
                        genomes.append(genome)
                        
                    except (ValueError, KeyError) as e:
                        raise ValueError(
                            f"Error parsing line {line_num} in {filepath}: {e}"
                        ) from e
                        
        except Exception as e:
            if isinstance(e, (FileNotFoundError, ValueError)):
                raise
            raise ValueError(f"Error reading file {filepath}: {e}") from e
        
        return genomes
    
    def parse_taxonomy_report(self, filepath: Path) -> Dict[int, TaxonomyNode]:
        """Parse the Taxonomy Report JSONL file.
        
        Args:
            filepath: Path to the taxonomy_report.jsonl file
            
        Returns:
            Dictionary mapping taxId to TaxonomyNode
            
        Raises:
            FileNotFoundError: If the file doesn't exist
            ValueError: If the file format is invalid
        """
        if not filepath.exists():
            raise FileNotFoundError(f"Taxonomy report file not found: {filepath}")
        
        nodes = {}
        
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                for line_num, line in enumerate(f, start=1):
                    line = line.strip()
                    if not line:
                        continue  # Skip empty lines
                    
                    try:
                        data = json.loads(line)
                        taxonomy = data.get('taxonomy', {})
                        
                        # Extract required fields
                        tax_id = taxonomy.get('taxId')
                        rank = taxonomy.get('rank', '').upper()
                        
                        # Get scientific name
                        current_name = taxonomy.get('currentScientificName', {})
                        name = current_name.get('name', '') if isinstance(current_name, dict) else ''
                        
                        # Get classification hierarchy
                        classification_raw = taxonomy.get('classification', {})
                        classification = {}
                        for level, info in classification_raw.items():
                            if isinstance(info, dict) and 'name' in info and 'id' in info:
                                classification[level.lower()] = (info['name'], info['id'])
                        
                        # Get parent and child IDs
                        parent_ids = taxonomy.get('parents', [])
                        child_ids = taxonomy.get('children', [])
                        
                        if tax_id is None:
                            continue  # Skip entries without taxId
                        
                        node = TaxonomyNode(
                            tax_id=tax_id,
                            name=name,
                            rank=rank,
                            classification=classification,
                            parent_ids=parent_ids,
                            child_ids=child_ids
                        )
                        nodes[tax_id] = node
                        
                    except (json.JSONDecodeError, KeyError, TypeError) as e:
                        raise ValueError(
                            f"Error parsing line {line_num} in {filepath}: {e}"
                        ) from e
                        
        except Exception as e:
            if isinstance(e, (FileNotFoundError, ValueError)):
                raise
            raise ValueError(f"Error reading file {filepath}: {e}") from e
        
        return nodes
    
    def parse_taxonomy_summary(self, filepath: Path) -> Dict[int, TaxonomyNode]:
        """Parse the Taxonomy Summary TSV file (backup method).
        
        This is a fallback parser if taxonomy_report.jsonl is not available.
        
        Args:
            filepath: Path to the taxonomy_summary.tsv file
            
        Returns:
            Dictionary mapping taxId to TaxonomyNode
            
        Raises:
            FileNotFoundError: If the file doesn't exist
            ValueError: If the file format is invalid
        """
        if not filepath.exists():
            raise FileNotFoundError(f"Taxonomy summary file not found: {filepath}")
        
        nodes = {}
        
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f, delimiter='\t')
                
                for line_num, row in enumerate(reader, start=2):
                    try:
                        # This is a simplified parser - taxonomy_summary.tsv
                        # doesn't have as much detail as taxonomy_report.jsonl
                        # Implementation would depend on actual file format
                        pass
                        
                    except (ValueError, KeyError) as e:
                        raise ValueError(
                            f"Error parsing line {line_num} in {filepath}: {e}"
                        ) from e
                        
        except Exception as e:
            if isinstance(e, (FileNotFoundError, ValueError)):
                raise
            raise ValueError(f"Error reading file {filepath}: {e}") from e
        
        return nodes
