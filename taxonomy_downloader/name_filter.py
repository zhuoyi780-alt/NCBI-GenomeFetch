"""
Name filter module for filtering species names by binomial nomenclature rules.

This module provides functionality to filter species names to retain only those
that follow standard binomial nomenclature (genus + species epithet) according
to the International Code of Nomenclature of Prokaryotes (ICNP).

Standard binomial nomenclature rules:
1. Exactly two words: genus name + specific epithet
2. Genus name: First letter uppercase, rest lowercase, ≥3 characters, no hyphens
3. Specific epithet: All lowercase, ≥3 characters, may contain hyphens
4. No subspecies markers (subsp., var., str., serovar, pv., bv.)
5. No unidentified markers (sp., cf., aff.)
6. No generic terms (archaeon, bacterium, uncultured, candidatus, etc.)
7. No numbers or special characters (except hyphens in specific epithet)

Performance optimizations:
- Pre-compiled regular expressions for faster matching
- Efficient batch processing with optional multiprocessing support
"""

import re
from typing import List, Optional
from functools import lru_cache


class BinomialNameFilter:
    """拉丁双名法过滤器
    
    Filters species names to retain only those following standard binomial
    nomenclature rules according to ICNP (International Code of Nomenclature
    of Prokaryotes).
    
    A valid binomial name must:
    - Contain exactly two words (genus + specific epithet)
    - Genus: First letter uppercase, rest lowercase, ≥3 characters, no hyphens
    - Specific epithet: All lowercase, ≥3 characters, may contain hyphens
    - No subspecies/variety markers
    - No unidentified species markers
    - No generic terms
    - No numbers or special characters (except hyphens in epithet)
    
    Performance features:
    - Pre-compiled regular expressions for fast matching
    - LRU cache for repeated validations
    - Efficient batch processing
    
    Examples:
        Valid: Escherichia coli, Bacillus subtilis, Clostridium niger-alba
        Invalid: Escherichia sp., uncultured bacterium, Candidatus Liberibacter
    """
    
    # Subspecies and variety markers
    SUBSPECIES_MARKERS = [
        'subsp', 'subspecies',
        'var', 'variety',
        'forma', 'f.',
        'str', 'strain',
        'serovar', 'serotype',
        'pv', 'pathovar',
        'bv', 'biovar'
    ]
    
    # Unidentified species markers
    UNIDENTIFIED_MARKERS = [
        'sp', 'species',
        'cf', 'confer',
        'aff', 'affinis'
    ]
    
    # Generic terms that indicate non-standard names
    GENERIC_TERMS = [
        # Organism type terms
        'archaeon', 'archaea',
        'bacterium', 'bacteria',
        'organism',
        
        # Status terms
        'uncultured', 'cultured',
        'unidentified', 'identified',
        'unclassified', 'classified',
        'candidatus',
        
        # Relationship terms
        'endosymbiont', 'symbiont',
        'endosymbiotic', 'symbiotic',
        
        # Taxonomic grouping terms
        'group', 'groups',
        'division',
        'cluster', 'clusters',
        'clade', 'clades',
        'complex',
        
        # Sample/source terms
        'isolate', 'isolated',
        'clone', 'cloned',
        'strain', 'strains',
        
        # Environmental terms
        'environmental',
        'metagenome', 'metagenomic',
        'genomosp',
        'taxon', 'taxa'
    ]
    
    def __init__(self):
        """Initialize the filter with pre-compiled regular expressions."""
        # Pre-compile regular expressions for better performance
        
        # Genus name pattern: First letter uppercase, rest lowercase, ≥3 chars, no hyphens
        self._genus_pattern = re.compile(r'^[A-Z][a-z]{2,}$')
        
        # Specific epithet pattern: All lowercase, ≥3 chars, may contain hyphens
        # Format: lowercase letters, optionally followed by hyphen and more lowercase letters
        self._epithet_pattern = re.compile(r'^[a-z]{3,}(-[a-z]+)*$')
        
        # Number detection pattern
        self._number_pattern = re.compile(r'\d')
        
        # Valid characters pattern (letters, spaces, hyphens only)
        self._valid_chars_pattern = re.compile(r'^[A-Za-z]+([\s-][A-Za-z]+)*$')
        
        # Pre-compile generic term patterns with word boundaries
        self._generic_term_patterns = [
            re.compile(r'\b' + re.escape(term) + r'\b', re.IGNORECASE)
            for term in self.GENERIC_TERMS
        ]
        
        # Pre-compile subspecies marker patterns
        self._subspecies_patterns = [
            re.compile(r'\b' + re.escape(marker) + r'\.?\b', re.IGNORECASE)
            for marker in self.SUBSPECIES_MARKERS
        ]
        
        # Pre-compile unidentified marker patterns
        self._unidentified_patterns = [
            re.compile(r'\b' + re.escape(marker) + r'\.?\b', re.IGNORECASE)
            for marker in self.UNIDENTIFIED_MARKERS
        ]
    
    @lru_cache(maxsize=10000)
    def is_valid_binomial(self, name: str) -> bool:
        """检查名称是否符合标准双名法格式
        
        Validates a species name according to standard binomial nomenclature
        rules as defined by ICNP. Uses LRU cache for performance optimization
        on repeated validations.
        
        Validation steps:
        1. Basic format check (two words, valid characters)
        2. Genus name format (capitalization, length, no hyphens)
        3. Specific epithet format (lowercase, length, hyphens allowed)
        4. Exclusion of subspecies markers
        5. Exclusion of unidentified markers
        6. Exclusion of generic terms
        7. Exclusion of numbers
        
        Args:
            name: Species name to validate
            
        Returns:
            True if the name is a valid binomial name, False otherwise
            
        Examples:
            >>> filter = BinomialNameFilter()
            >>> filter.is_valid_binomial("Escherichia coli")
            True
            >>> filter.is_valid_binomial("Escherichia sp.")
            False
            >>> filter.is_valid_binomial("uncultured bacterium")
            False
            
        Note:
            This method is cached using LRU cache for performance.
            Cache size is 10,000 entries by default.
        """
        if not name or not isinstance(name, str):
            return False
        
        # Normalize whitespace
        name = ' '.join(name.split())
        
        # 1. Check for numbers (must not contain any digits)
        if self._number_pattern.search(name):
            return False
        
        # 2. Check for valid characters only
        # Allow: letters (A-Z, a-z), spaces, hyphens
        if not self._valid_chars_pattern.match(name):
            return False
        
        # 3. Split into words and check count
        words = name.split()
        if len(words) != 2:
            return False
        
        genus, epithet = words[0], words[1]
        
        # 4. Validate genus name format using pre-compiled pattern
        # Pattern: ^[A-Z][a-z]{2,}$ (uppercase first, lowercase rest, ≥3 chars, no hyphens)
        if not self._genus_pattern.match(genus):
            return False
        
        # 5. Validate specific epithet format using pre-compiled pattern
        # Pattern: ^[a-z]{3,}(-[a-z]+)*$ (lowercase, ≥3 chars, hyphens allowed)
        if not self._epithet_pattern.match(epithet):
            return False
        
        # 6. Check for subspecies/variety markers using pre-compiled patterns
        for pattern in self._subspecies_patterns:
            if pattern.search(name):
                return False
        
        # 7. Check for unidentified species markers using pre-compiled patterns
        for pattern in self._unidentified_patterns:
            if pattern.search(name):
                return False
        
        # 8. Check for generic terms using pre-compiled patterns
        for pattern in self._generic_term_patterns:
            if pattern.search(name):
                return False
        
        return True
    
    def filter_names(self, names: List[str]) -> List[str]:
        """过滤名称列表，仅保留符合双名法的名称
        
        Args:
            names: List of species names to filter
            
        Returns:
            List of names that pass the binomial nomenclature validation
            
        Examples:
            >>> filter = BinomialNameFilter()
            >>> names = ["Escherichia coli", "Bacillus sp.", "uncultured bacterium"]
            >>> filter.filter_names(names)
            ['Escherichia coli']
        """
        return [name for name in names if self.is_valid_binomial(name)]
    
    def get_rejection_reason(self, name: str) -> str:
        """获取名称被拒绝的原因
        
        Provides detailed feedback on why a name does not pass validation.
        Useful for debugging and understanding filtering results.
        
        Args:
            name: Species name to check
            
        Returns:
            String describing why the name was rejected, or "Valid" if accepted
            
        Examples:
            >>> filter = BinomialNameFilter()
            >>> filter.get_rejection_reason("Escherichia sp.")
            'Contains unidentified marker: sp'
            >>> filter.get_rejection_reason("Escherichia coli")
            'Valid'
        """
        if not name or not isinstance(name, str):
            return "Empty or invalid input"
        
        # Normalize whitespace
        name = ' '.join(name.split())
        
        # Check for numbers
        if self._number_pattern.search(name):
            return "Contains numbers"
        
        # Check for valid characters
        if not self._valid_chars_pattern.match(name):
            return "Contains invalid characters (only letters, spaces, hyphens allowed)"
        
        # Check word count
        words = name.split()
        if len(words) < 2:
            return f"Too few words ({len(words)}), need exactly 2"
        if len(words) > 2:
            return f"Too many words ({len(words)}), need exactly 2"
        
        genus, epithet = words[0], words[1]
        
        # Check genus format using pre-compiled pattern
        if not self._genus_pattern.match(genus):
            if not genus[0].isupper():
                return "Genus name must start with uppercase letter"
            if '-' in genus:
                return "Genus name must not contain hyphens"
            if not genus[1:].islower():
                return "Genus name must have lowercase letters after first"
            if len(genus) < 3:
                return f"Genus name too short ({len(genus)} chars), need ≥3"
            return "Genus name format invalid"
        
        # Check epithet format using pre-compiled pattern
        if not self._epithet_pattern.match(epithet):
            if not epithet.replace('-', '').islower():
                return "Specific epithet must be all lowercase"
            if len(epithet) < 3:
                return f"Specific epithet too short ({len(epithet)} chars), need ≥3"
            if epithet.startswith('-') or epithet.endswith('-'):
                return "Specific epithet cannot start or end with hyphen"
            if '--' in epithet:
                return "Specific epithet cannot have consecutive hyphens"
            return "Specific epithet format invalid"
        
        # Check for subspecies markers
        for i, pattern in enumerate(self._subspecies_patterns):
            if pattern.search(name):
                return f"Contains subspecies marker: {self.SUBSPECIES_MARKERS[i]}"
        
        # Check for unidentified markers
        for i, pattern in enumerate(self._unidentified_patterns):
            if pattern.search(name):
                return f"Contains unidentified marker: {self.UNIDENTIFIED_MARKERS[i]}"
        
        # Check for generic terms
        for i, pattern in enumerate(self._generic_term_patterns):
            if pattern.search(name):
                return f"Contains generic term: {self.GENERIC_TERMS[i]}"
        
        return "Valid"
    
    def validate_batch(self, names: List[str], verbose: bool = False, 
                      use_multiprocessing: bool = False, n_processes: Optional[int] = None) -> dict:
        """批量验证名称并返回统计信息
        
        Validates a list of names and provides statistics about the results.
        Supports multiprocessing for large datasets.
        
        Args:
            names: List of species names to validate
            verbose: If True, include lists of valid/invalid names
            use_multiprocessing: If True, use multiprocessing for large datasets
            n_processes: Number of processes to use (default: CPU count)
            
        Returns:
            Dictionary containing:
            - total: Total number of names
            - valid: Number of valid names
            - invalid: Number of invalid names
            - valid_rate: Percentage of valid names
            - valid_names: List of valid names (if verbose=True)
            - invalid_names: List of invalid names with reasons (if verbose=True)
            
        Examples:
            >>> filter = BinomialNameFilter()
            >>> names = ["Escherichia coli", "Bacillus sp.", "uncultured bacterium"]
            >>> result = filter.validate_batch(names)
            >>> result['valid']
            1
            >>> result['valid_rate']
            33.33
            
        Performance:
            For datasets > 10,000 names, consider using use_multiprocessing=True
            for better performance on multi-core systems.
        """
        if use_multiprocessing and len(names) > 1000:
            return self._validate_batch_parallel(names, verbose, n_processes)
        else:
            return self._validate_batch_sequential(names, verbose)
    
    def _validate_batch_sequential(self, names: List[str], verbose: bool) -> dict:
        """Sequential batch validation (internal method)."""
        valid_names = []
        invalid_names = []
        
        for name in names:
            if self.is_valid_binomial(name):
                valid_names.append(name)
            else:
                if verbose:
                    reason = self.get_rejection_reason(name)
                    invalid_names.append((name, reason))
                else:
                    invalid_names.append(name)
        
        total = len(names)
        valid = len(valid_names)
        invalid = len(invalid_names)
        valid_rate = (valid / total * 100) if total > 0 else 0
        
        result = {
            'total': total,
            'valid': valid,
            'invalid': invalid,
            'valid_rate': round(valid_rate, 2)
        }
        
        if verbose:
            result['valid_names'] = valid_names
            result['invalid_names'] = invalid_names
        
        return result
    
    def _validate_batch_parallel(self, names: List[str], verbose: bool, 
                                 n_processes: Optional[int]) -> dict:
        """Parallel batch validation using multiprocessing (internal method)."""
        from multiprocessing import Pool, cpu_count
        
        if n_processes is None:
            n_processes = cpu_count()
        
        # Split names into chunks for parallel processing
        chunk_size = max(1, len(names) // n_processes)
        
        with Pool(processes=n_processes) as pool:
            # Map validation function to chunks
            results = pool.map(self.is_valid_binomial, names)
        
        valid_names = []
        invalid_names = []
        
        for name, is_valid in zip(names, results):
            if is_valid:
                valid_names.append(name)
            else:
                if verbose:
                    reason = self.get_rejection_reason(name)
                    invalid_names.append((name, reason))
                else:
                    invalid_names.append(name)
        
        total = len(names)
        valid = len(valid_names)
        invalid = len(invalid_names)
        valid_rate = (valid / total * 100) if total > 0 else 0
        
        result = {
            'total': total,
            'valid': valid,
            'invalid': invalid,
            'valid_rate': round(valid_rate, 2)
        }
        
        if verbose:
            result['valid_names'] = valid_names
            result['invalid_names'] = invalid_names
        
        return result
    
    def clear_cache(self):
        """清除 LRU 缓存
        
        Clears the LRU cache used by is_valid_binomial().
        Useful when memory is a concern or when processing completely
        different datasets.
        
        Examples:
            >>> filter = BinomialNameFilter()
            >>> # Process first dataset
            >>> filter.validate_batch(dataset1)
            >>> # Clear cache before processing second dataset
            >>> filter.clear_cache()
            >>> filter.validate_batch(dataset2)
        """
        self.is_valid_binomial.cache_clear()
