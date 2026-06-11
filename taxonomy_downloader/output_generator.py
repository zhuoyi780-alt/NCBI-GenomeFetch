"""Output generator for task splitter.

This module handles writing split results to output files.
"""

from pathlib import Path
from typing import List, Optional, Tuple


class OutputGenerator:
    """Generates output files for split results."""
    
    def __init__(self, output_dir: Path):
        """Initialize output generator.
        
        Args:
            output_dir: Directory where output files will be written
        """
        self._output_dir = Path(output_dir)
        
    def write_group_files(self, groups: List[List[Tuple[str, int, int]]], 
                         mapper=None, tree=None, data_source: str = 'all') -> List[Path]:
        """Write group files (group1.txt, group2.txt, etc.).
        
        Each file contains species-level taxon names and TaxIDs, followed by
        all descendant nodes (strains, etc.) that have genome data.
        
        Format:
        # Species: species_name (tax_id)
        species_name\ttax_id
        strain_name_1\tstrain_tax_id_1
        strain_name_2\tstrain_tax_id_2
        ...
        
        Args:
            groups: List of groups, where each group is a list of (taxon_name, tax_id, size_bytes) tuples
            mapper: GenomeMapper instance (optional, for listing descendants with genomes)
            tree: TaxonomyTree instance (optional, for getting descendant nodes)
            data_source: Data source filter ('refseq', 'genbank', 'all')
            
        Returns:
            List of paths to created group files
        """
        self._output_dir.mkdir(parents=True, exist_ok=True)
        
        created_files = []
        for i, group in enumerate(groups, start=1):
            filepath = self._output_dir / f"group{i}.txt"
            with open(filepath, 'w', encoding='utf-8') as f:
                for taxon_name, tax_id, _ in group:
                    # Write species-level header
                    f.write(f"# Species: {taxon_name} ({tax_id})\n")
                    
                    # Write species-level entry
                    f.write(f"{taxon_name}\t{tax_id}\n")
                    
                    # If mapper and tree are provided, list all descendants with genomes
                    if mapper and tree:
                        descendant_ids = tree.get_all_descendant_ids(tax_id)
                        descendant_ids.discard(tax_id)  # Remove the species itself
                        
                        # Get nodes for descendants and check if they have genomes
                        descendants_with_genomes = []
                        for desc_id in descendant_ids:
                            if mapper.has_direct_genomes(desc_id, data_source):
                                # Get the node to get its name
                                if desc_id in tree._nodes:
                                    desc_node = tree._nodes[desc_id]
                                    descendants_with_genomes.append((desc_node.name, desc_id))
                        
                        # Sort by name for consistent output
                        descendants_with_genomes.sort(key=lambda x: x[0])
                        
                        # Write descendant entries
                        for desc_name, desc_id in descendants_with_genomes:
                            f.write(f"{desc_name}\t{desc_id}\n")
                    
                    # Add blank line between species groups
                    f.write("\n")
            
            created_files.append(filepath)
            
        return created_files
        
    def write_exceeded_file(self, exceeded: List[Tuple[str, int, int]]) -> Optional[Path]:
        """Write exceeded.txt file containing oversized taxons.
        
        File format: taxon_name\ttax_id\tsize_bytes
        
        Args:
            exceeded: List of (taxon_name, tax_id, size_bytes) tuples
            
        Returns:
            Path to created file, or None if exceeded list is empty
        """
        if not exceeded:
            return None
            
        self._output_dir.mkdir(parents=True, exist_ok=True)
        
        filepath = self._output_dir / "exceeded.txt"
        with open(filepath, 'w', encoding='utf-8') as f:
            for taxon_name, tax_id, size_bytes in exceeded:
                f.write(f"{taxon_name}\t{tax_id}\t{size_bytes}\n")
                
        return filepath
        
    def write_secondary_split_files(self, taxon_name: str, 
                                    accession_groups: List[List[str]]) -> List[Path]:
        """Write secondary split files for an exceeded taxon.
        
        Files are named {taxon_name}_1.txt, {taxon_name}_2.txt, etc.
        Each file contains accession numbers, one per line.
        
        Args:
            taxon_name: Name of the exceeded taxon
            accession_groups: List of groups, where each group is a list of accessions
            
        Returns:
            List of paths to created files
        """
        self._output_dir.mkdir(parents=True, exist_ok=True)
        
        created_files = []
        for i, accessions in enumerate(accession_groups, start=1):
            # Sanitize taxon name for filename (replace spaces and special chars)
            safe_name = taxon_name.replace(' ', '_').replace('/', '_')
            filepath = self._output_dir / f"{safe_name}_{i}.txt"
            with open(filepath, 'w', encoding='utf-8') as f:
                for accession in accessions:
                    f.write(f"{accession}\n")
            created_files.append(filepath)
            
        return created_files
    
    def write_readme(self, taxon: str, split_size_gb: float, 
                    num_groups: int, num_exceeded: int,
                    taxonomy_level: str, name_filter: bool, 
                    data_source: str,
                    total_species: int = 0,
                    total_genomes: int = 0,
                    total_size_gb: float = 0.0,
                    group_stats: Optional[List[Tuple[int, int, float]]] = None,
                    exceeded_stats: Optional[Tuple[int, float]] = None) -> Path:
        """Write README.md file explaining the split results.
        
        Args:
            taxon: Taxon name
            split_size_gb: Split size threshold in Gb
            num_groups: Number of groups created
            num_exceeded: Number of exceeded taxons
            taxonomy_level: Selected taxonomy level
            name_filter: Whether binomial name filter was enabled
            data_source: Data source (refseq/genbank/all)
            total_species: Total number of species across all groups
            total_genomes: Total number of genomes across all groups
            total_size_gb: Total size in Gb across all groups
            group_stats: List of (species_count, genome_count, size_gb) for each group
            exceeded_stats: Tuple of (total_genomes, total_size_gb) for exceeded taxons
            
        Returns:
            Path to created README file
        """
        self._output_dir.mkdir(parents=True, exist_ok=True)
        
        filepath = self._output_dir / "README.md"
        
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(f"# Task Split Results: {taxon}\n\n")
            
            f.write("## Configuration\n\n")
            f.write(f"- **Taxon**: {taxon}\n")
            f.write(f"- **Taxonomy Level**: {taxonomy_level}\n")
            f.write(f"- **Binomial Name Filter**: {'Enabled' if name_filter else 'Disabled'}\n")
            f.write(f"- **Data Source**: {data_source}\n")
            f.write(f"- **Split Size**: {split_size_gb} Gb (gigabases)\n\n")
            
            # Overall Statistics section
            f.write("## Overall Statistics\n\n")
            f.write(f"| Metric | Value |\n")
            f.write(f"|--------|-------|\n")
            f.write(f"| Total Species | {total_species:,} |\n")
            f.write(f"| Total Genomes | {total_genomes:,} |\n")
            f.write(f"| Total Size | {total_size_gb:.2f} Gb |\n\n")
            
            f.write("### Estimated Disk Space\n\n")
            f.write(f"| Format | Estimated Disk Space |\n")
            f.write(f"|--------|----------------------|\n")
            f.write(f"| **FASTA.GZ (compressed)** | **~{total_size_gb * 0.3:.0f} GB** |\n")
            f.write(f"| FASTA (uncompressed) | ~{total_size_gb * 1.05:.0f} GB |\n")
            f.write(f"| GenBank (with annotations) | ~{total_size_gb * 2.5:.0f} GB |\n\n")
            
            # Group Details section
            if group_stats and len(group_stats) > 0:
                f.write("## Group Details\n\n")
                f.write(f"| Group | Species | Genomes | Size (Gb) | FASTA.GZ | FASTA | GenBank |\n")
                f.write(f"|-------|---------|---------|-----------|----------|-------|----------|\n")
                for i, (species_count, genome_count, size_gb) in enumerate(group_stats, start=1):
                    fasta_gz = size_gb * 0.3
                    fasta = size_gb * 1.05
                    genbank = size_gb * 2.5
                    f.write(f"| group{i}.txt | {species_count:,} | {genome_count:,} | {size_gb:.2f} | ~{fasta_gz:.0f} GB | ~{fasta:.0f} GB | ~{genbank:.0f} GB |\n")
                f.write("\n")
            
            # Exceeded Taxons section
            if num_exceeded > 0 and exceeded_stats:
                exceeded_genomes, exceeded_size_gb = exceeded_stats
                f.write("## Exceeded Taxons\n\n")
                f.write(f"| Metric | Value |\n")
                f.write(f"|--------|-------|\n")
                f.write(f"| Exceeded Taxons | {num_exceeded} |\n")
                f.write(f"| Total Genomes | {exceeded_genomes:,} |\n")
                f.write(f"| Total Size | {exceeded_size_gb:.2f} Gb |\n\n")
            
            f.write("## Understanding Size Units\n\n")
            f.write("**IMPORTANT**: The split size is measured in **Gb (gigabases)**, ")
            f.write("which represents sequence length (10^9 base pairs), **NOT disk space**.\n\n")
            
            f.write("## Output Files\n\n")
            f.write(f"- **Main Groups**: {num_groups} files (group1.txt - group{num_groups}.txt)\n")
            f.write(f"- **Exceeded Taxons**: {num_exceeded} taxons in exceeded.txt\n")
            if num_exceeded > 0:
                f.write(f"- **Secondary Splits**: Individual files for each exceeded taxon\n")
            f.write("\n")
            
            f.write("### File Formats\n\n")
            f.write("**Main Group Files** (group*.txt):\n")
            f.write("```\n")
            f.write("# Species: Species Name 1 (TaxID)\n")
            f.write("Species Name 1<TAB>TaxID\n")
            f.write("Strain Name 1a<TAB>Strain TaxID 1a\n")
            f.write("Strain Name 1b<TAB>Strain TaxID 1b\n")
            f.write("\n")
            f.write("# Species: Species Name 2 (TaxID)\n")
            f.write("Species Name 2<TAB>TaxID\n")
            f.write("...\n")
            f.write("```\n\n")
            f.write("**Note**: Each species entry includes the species itself and all its descendant nodes ")
            f.write("(strains, substrains, etc.) that have genome data.\n\n")
            
            f.write("**Exceeded File** (exceeded.txt):\n")
            f.write("```\n")
            f.write("Taxon Name<TAB>TaxID<TAB>Size in bytes\n")
            f.write("```\n\n")
            
            f.write("**Secondary Split Files** (TaxonName_*.txt):\n")
            f.write("```\n")
            f.write("GCA_000001234.1\n")
            f.write("GCA_000005678.2\n")
            f.write("...\n")
            f.write("```\n\n")
            f.write("## Notes\n\n")
            f.write("- Actual file sizes depend on download format and compression\n")
            f.write("- FASTA.GZ (compressed) is recommended to save disk space\n")
            f.write("- Decompressed files will be significantly larger\n")
            f.write("- Compression ratio typically ranges from 3-4x for genomic data\n")
            
        return filepath
