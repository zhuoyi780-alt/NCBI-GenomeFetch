"""Interactive workflow for task splitting.

This module provides an interactive command-line workflow that guides users
through the process of splitting genome download tasks.
"""

from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

from .file_parser import FileParser
from .genome_mapper import GenomeMapper
from .output_generator import OutputGenerator
from .split_models import SplitConfig, SplitResult
from .task_splitter import TaskSplitter
from .taxonomy_tree import TaxonomyTree


class SplitWorkflow:
    """Interactive workflow for task splitting.
    
    Guides users through selecting taxonomy level, name filtering,
    data source, and split size, then executes the splitting operation.
    """
    
    def __init__(self, taxon: str, output_dir: Optional[Path] = None):
        """Initialize the workflow.
        
        Args:
            taxon: Name of the taxon, or path to a taxon data folder.
                   If a folder path is provided (e.g., 'Archaea/' or './data/Archaea'),
                   the folder is used directly and the taxon name is derived from
                   the folder's basename.
            output_dir: Optional output directory. If None, creates default directory
        """
        self._taxon_dir, self._taxon_name = self._resolve_taxon(taxon)
        self._output_dir = output_dir or self._create_default_output_dir()
        self._config: Optional[SplitConfig] = None
    
    @staticmethod
    def _resolve_taxon(taxon: str) -> Tuple[Path, str]:
        """Resolve taxon input to (directory_path, taxon_name).
        
        If `taxon` is a path to an existing directory, use it directly and
        derive the name from the directory basename.
        Otherwise treat it as a plain taxon name (legacy behaviour).
        
        Args:
            taxon: User-supplied taxon string (name or folder path)
            
        Returns:
            Tuple of (taxon_directory, taxon_name)
        """
        taxon_path = Path(taxon)
        
        # If the path points to an existing directory, use it directly
        if taxon_path.is_dir():
            # Resolve to remove trailing slashes and normalise
            resolved = taxon_path.resolve()
            taxon_name = resolved.name  # e.g. 'Archaea'
            return resolved, taxon_name
        
        # Legacy: treat as a plain taxon name, directory = ./name
        return Path(taxon), taxon
        
    def _create_default_output_dir(self) -> Path:
        """创建默认输出目录: {taxon_dir}/split_results_{timestamp}
        
        Returns:
            Path to the default output directory
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return self._taxon_dir / f"split_results_{timestamp}"
    
    def run(self) -> int:
        """执行完整的交互式工作流
        
        Returns:
            Exit code (0 for success, non-zero for error)
        """
        print(f"\n=== Task Splitter for {self._taxon_name} ===")
        if self._taxon_dir.resolve() != Path(self._taxon_name).resolve():
            print(f"    Data folder: {self._taxon_dir}")
        print()
        
        # Step 1: Check and load input files
        print("Step 1: Loading input files...")
        try:
            parser, tree, mapper = self._load_data()
        except FileNotFoundError as e:
            print(f"Error: {e}")
            return 1
        except Exception as e:
            print(f"Error loading data: {e}")
            return 1
        
        print("✓ Data loaded successfully\n")
        
        # Step 2: Get available taxonomy ranks
        available_ranks = tree.get_available_ranks()
        if not available_ranks:
            print("Error: No taxonomy ranks found in the data")
            return 1
        
        # Step 3: Interactive configuration
        print("Step 2: Configuration\n")
        
        taxonomy_level = self._prompt_taxonomy_level(available_ranks)
        self._display_progress(taxonomy_level=taxonomy_level)
        
        enable_name_filter = self._prompt_name_filter()
        self._display_progress(taxonomy_level=taxonomy_level, 
                              name_filter=enable_name_filter)
        
        data_source = self._prompt_data_source()
        self._display_progress(taxonomy_level=taxonomy_level,
                              name_filter=enable_name_filter,
                              data_source=data_source)
        
        split_size_gb = self._prompt_split_size()
        self._display_progress(taxonomy_level=taxonomy_level,
                              name_filter=enable_name_filter,
                              data_source=data_source,
                              split_size=split_size_gb)
        
        # Create configuration
        self._config = SplitConfig(
            taxonomy_level=taxonomy_level,
            enable_name_filter=enable_name_filter,
            data_source=data_source,
            split_size_gb=split_size_gb
        )
        
        # Step 4: Execute splitting
        print("\nStep 3: Executing split...")
        splitter = TaskSplitter(mapper, tree)
        result = splitter.split_by_taxon(self._config)
        
        # Step 5: Write output files
        print("Step 4: Writing output files...")
        generator = OutputGenerator(self._output_dir)
        
        group_files = generator.write_group_files(
            result.groups, 
            mapper=mapper, 
            tree=tree, 
            data_source=self._config.data_source
        )
        print(f"✓ Created {len(group_files)} group files")
        
        exceeded_file = generator.write_exceeded_file(result.exceeded)
        if exceeded_file:
            print(f"✓ Created exceeded file with {len(result.exceeded)} taxons")
            
            # Step 6: Secondary splitting for exceeded taxons
            print("\nStep 5: Performing secondary split for exceeded taxons...")
            self._perform_secondary_splits(result, tree, splitter, generator)
        
        # Step 7: Calculate statistics (before writing README)
        print("\n=== Split Complete ===")
        print(f"Output directory: {self._output_dir}")
        print(f"Total groups: {len(result.groups)}")
        print(f"Exceeded taxons: {len(result.exceeded)}")
        
        # 计算总体统计（包含 groups 和 exceeded）
        total_species = sum(len(group) for group in result.groups) + len(result.exceeded)
        total_genomes = 0
        total_size_bytes = 0
        
        # 统计 groups 中的数据，同时收集每组统计信息
        group_stats = []  # List of (species_count, genome_count, size_gb)
        for group in result.groups:
            species_count = len(group)
            group_size_bytes = sum(size for _, _, size in group)
            group_genomes = 0
            for taxon_name, tax_id, size in group:
                total_size_bytes += size
                genomes = mapper.get_genomes_for_taxon(tax_id, self._config.data_source)
                total_genomes += len(genomes)
                group_genomes += len(genomes)
            group_stats.append((species_count, group_genomes, group_size_bytes / 1_000_000_000))
        
        # 统计 exceeded 中的数据
        exceeded_genomes = 0
        exceeded_size_bytes = 0
        for taxon_name, tax_id, size in result.exceeded:
            total_size_bytes += size
            exceeded_size_bytes += size
            genomes = mapper.get_genomes_for_taxon(tax_id, self._config.data_source)
            total_genomes += len(genomes)
            exceeded_genomes += len(genomes)
        
        total_size_gb = total_size_bytes / 1_000_000_000
        exceeded_size_gb = exceeded_size_bytes / 1_000_000_000
        exceeded_stats = (exceeded_genomes, exceeded_size_gb) if result.exceeded else None
        
        # Write README file with statistics
        readme_file = generator.write_readme(
            taxon=self._taxon_name,
            split_size_gb=self._config.split_size_gb,
            num_groups=len(result.groups),
            num_exceeded=len(result.exceeded),
            taxonomy_level=self._config.taxonomy_level,
            name_filter=self._config.enable_name_filter,
            data_source=self._config.data_source,
            total_species=total_species,
            total_genomes=total_genomes,
            total_size_gb=total_size_gb,
            group_stats=group_stats,
            exceeded_stats=exceeded_stats
        )
        print(f"✓ Created README file: {readme_file.name}")
        
        # 显示总体统计
        print(f"\n--- Overall Statistics ---")
        print(f"Total species: {total_species:,}")
        print(f"Total genomes: {total_genomes:,}")
        print(f"Total size: {total_size_gb:.2f} Gb")
        print(f"Estimated disk space:")
        print(f"  • FASTA.GZ: ~{total_size_gb * 0.3:.0f} GB")
        print(f"  • FASTA: ~{total_size_gb * 1.05:.0f} GB")
        print(f"  • GenBank: ~{total_size_gb * 2.5:.0f} GB")
        
        # 显示每组的详细信息
        if result.groups:
            print(f"\n--- Group Details ---")
            for i, (species_count, group_genomes, group_size_gb) in enumerate(group_stats, start=1):
                print(f"  group{i}.txt:")
                print(f"    Species: {species_count:,}")
                print(f"    Genomes: {group_genomes:,}")
                print(f"    Size: {group_size_gb:.2f} Gb")
                print(f"    Estimated disk space:")
                print(f"      • FASTA.GZ: ~{group_size_gb * 0.3:.0f} GB")
                print(f"      • FASTA: ~{group_size_gb * 1.05:.0f} GB")
                print(f"      • GenBank: ~{group_size_gb * 2.5:.0f} GB")
        
        # 显示超限 taxon 信息
        if result.exceeded:
            print(f"\n--- Exceeded Taxons ---")
            print(f"Total exceeded taxons: {len(result.exceeded)}")
            print(f"Total genomes: {exceeded_genomes:,}")
            print(f"Total size: {exceeded_size_gb:.2f} Gb")
        
        return 0
    
    def _load_data(self) -> Tuple[FileParser, TaxonomyTree, GenomeMapper]:
        """Load and parse input files.
        
        Uses self._taxon_dir as the data directory and self._taxon_name
        to locate the assembly report TSV.
        
        Returns:
            Tuple of (FileParser, TaxonomyTree, GenomeMapper)
            
        Raises:
            FileNotFoundError: If required files are missing
        """
        taxon_dir = self._taxon_dir
        
        # Check if taxon directory exists
        if not taxon_dir.exists():
            raise FileNotFoundError(
                f"Taxon folder '{taxon_dir}' does not exist.\n"
                f"Please ensure you have downloaded the taxonomy data first."
            )
        
        # Define required files (use taxon_name for the .tsv filename)
        assembly_report = taxon_dir / f"{self._taxon_name}.tsv"
        taxonomy_report = taxon_dir / "taxonomy_report.jsonl"
        
        # Check for missing files and provide specific error messages
        missing_files = []
        if not assembly_report.exists():
            missing_files.append(f"Assembly Report: {assembly_report}")
        if not taxonomy_report.exists():
            missing_files.append(f"Taxonomy Report: {taxonomy_report}")
        
        if missing_files:
            error_msg = "Required files are missing:\n"
            for file in missing_files:
                error_msg += f"  - {file}\n"
            error_msg += "\nPlease ensure you have downloaded the complete taxonomy data."
            raise FileNotFoundError(error_msg)
        
        # Parse files
        parser = FileParser()
        genomes = parser.parse_assembly_report(assembly_report)
        nodes = parser.parse_taxonomy_report(taxonomy_report)
        
        # Build tree and mapper
        tree = TaxonomyTree(nodes)
        tree.build_tree()
        
        mapper = GenomeMapper(genomes, tree)
        mapper.build_mapping()
        
        return parser, tree, mapper
    
    def _prompt_taxonomy_level(self, available_ranks: List[Tuple[str, int]]) -> str:
        """提示用户选择分类水平
        
        Displays available taxonomy levels sorted by hierarchy with node counts.
        
        Args:
            available_ranks: List of (rank_name, node_count) tuples
            
        Returns:
            Selected taxonomy level (uppercase)
        """
        print("Available taxonomy levels:")
        for i, (rank, count) in enumerate(available_ranks, start=1):
            print(f"  {i}. {rank.capitalize()} ({count} nodes)")
        
        while True:
            response = input("\nSelect taxonomy level (or press Enter to see options): ").strip()
            
            if not response:
                # Show options again
                print("\nAvailable taxonomy levels:")
                for i, (rank, count) in enumerate(available_ranks, start=1):
                    print(f"  {i}. {rank.capitalize()} ({count} nodes)")
                continue
            
            # Check if it's a valid rank name
            response_upper = response.upper()
            valid_ranks = [rank.upper() for rank, _ in available_ranks]
            
            if response_upper in valid_ranks:
                return response_upper
            
            # Check if it's a valid number
            try:
                index = int(response) - 1
                if 0 <= index < len(available_ranks):
                    return available_ranks[index][0].upper()
            except ValueError:
                pass
            
            print(f"Invalid input. Please enter a valid taxonomy level or number.")
    
    def _prompt_name_filter(self) -> bool:
        """提示用户是否启用名称过滤
        
        Returns:
            True if name filtering should be enabled, False otherwise
        """
        while True:
            response = input("\nEnable binomial name filter? (y/n): ").strip().lower()
            
            if response in ['y', 'yes']:
                return True
            elif response in ['n', 'no']:
                return False
            else:
                print("Please enter y/yes or n/no")
    
    def _prompt_data_source(self) -> str:
        """提示用户选择数据来源
        
        Returns:
            Data source: 'refseq', 'genbank', or 'all'
        """
        while True:
            response = input("\nSelect data source (refseq/genbank/all): ").strip().lower()
            
            if response in ['refseq', 'genbank', 'all']:
                return response
            else:
                print("Please enter refseq, genbank, or all")
    
    def _prompt_split_size(self) -> float:
        """提示分割大小（明确说明单位为 Gb 碱基数）
        
        Returns:
            Split size in gigabases (Gb)
        """
        print("\n" + "=" * 70)
        print("IMPORTANT: Understanding Size Units")
        print("=" * 70)
        print("\nSize is measured in Gb (gigabases, 10^9 base pairs).")
        print("This is the SEQUENCE LENGTH, not disk space.")
        
        print("\nEstimated disk space for common formats:")
        print("  • FASTA.GZ (compressed):  ~30% of sequence length")
        print("  • FASTA (uncompressed):   ~105% of sequence length")
        print("  • GenBank (with annotations): ~250% of sequence length")
        
        print("\nExamples:")
        print("  • 900 Gb sequence ≈ 270 GB disk space (FASTA.GZ)")
        print("  • 1000 Gb sequence ≈ 300 GB disk space (FASTA.GZ)")
        print("=" * 70)
        
        while True:
            response = input("\nEnter split size in Gb: ").strip()
            
            try:
                size = float(response)
                if size <= 0:
                    print("Please enter a positive number")
                    continue
                
                # 显示估算的硬盘空间
                fasta_gz = size * 0.3
                fasta = size * 1.05
                genbank = size * 2.5
                
                print(f"\nEstimated disk space for {size} Gb:")
                print(f"  • FASTA.GZ: ~{fasta_gz:.0f} GB")
                print(f"  • FASTA: ~{fasta:.0f} GB")
                print(f"  • GenBank: ~{genbank:.0f} GB")
                
                confirm = input("\nConfirm this split size? (y/n): ").strip().lower()
                if confirm in ['y', 'yes']:
                    return size
                else:
                    print("\nLet's try a different size...")
                    
            except ValueError:
                print("Please enter a valid number")
    
    def _display_progress(self, 
                         taxonomy_level: Optional[str] = None,
                         name_filter: Optional[bool] = None,
                         data_source: Optional[str] = None,
                         split_size: Optional[float] = None) -> None:
        """显示当前配置进度
        
        Args:
            taxonomy_level: Selected taxonomy level
            name_filter: Whether name filtering is enabled
            data_source: Selected data source
            split_size: Split size in Gb
        """
        print("\n--- Current Configuration ---")
        print(f"Taxon: {self._taxon_name}")
        if self._taxon_dir.resolve() != Path(self._taxon_name).resolve():
            print(f"Data Folder: {self._taxon_dir}")
        
        if taxonomy_level:
            print(f"Taxonomy Level: {taxonomy_level.capitalize()}")
        if name_filter is not None:
            print(f"Name Filter: {'Enabled' if name_filter else 'Disabled'}")
        if data_source:
            print(f"Data Source: {data_source}")
        if split_size is not None:
            print(f"Split Size: {split_size} Gb")
        
        print("----------------------------\n")
    
    def _perform_secondary_splits(self,
                                  result: SplitResult,
                                  tree: TaxonomyTree,
                                  splitter: TaskSplitter,
                                  generator: OutputGenerator) -> None:
        """Perform secondary splitting for exceeded taxons.
        
        Args:
            result: Primary split result containing exceeded taxons
            tree: TaxonomyTree instance
            splitter: TaskSplitter instance
            generator: OutputGenerator instance
        """
        split_size_bytes = int(self._config.split_size_gb * 1_000_000_000)
        
        for taxon_name, tax_id, size_bytes in result.exceeded:
            # Perform secondary split
            accession_groups = splitter.split_exceeded_by_accession(
                taxon_name,
                tax_id,
                split_size_bytes,
                self._config.data_source
            )
            
            # Write secondary split files
            files = generator.write_secondary_split_files(taxon_name, accession_groups)
            print(f"  ✓ Split {taxon_name} into {len(files)} files")
