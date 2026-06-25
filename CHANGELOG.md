# Changelog

All notable changes to NCBI-GenomeFetch are documented in this file.

## [1.0.1] - 2026-06-23

### Changed
- Updated package metadata to version `1.0.1` in `pyproject.toml`, `setup.py`, and `taxonomy_downloader/__init__.py`.
- Added current-version release documentation for the `ncbi-genomefetch` CLI.
- Prepared a minimal release layout containing core source code, install configuration, license, README, quick start, and changelog.

### Package Scope
- Included: `taxonomy_downloader/*.py`, `README.md`, `QUICKSTART.md`, `CHANGELOG.md`, `LICENSE`, `MANIFEST.in`, `pyproject.toml`, `setup.py`, and `requirements.txt`.
- Excluded: tests, examples, generated data, cached bytecode, bundled datasets binaries, historical release folders, and extra documentation folders.

### Runtime Notes
- Python requirement: `3.8+`.
- Python runtime dependency: `requests>=2.28.0`.
- External dependency: NCBI `datasets` CLI `16.0.0+`.

## [1.0.0] - 2026-03-11

### Added
- Taxon-based genome download workflow using NCBI `datasets`.
- Accession-based batch download workflow with progress state.
- Split workflow for large taxonomy download planning.
- MD5 verification and auto-fix workflow.
- Multiple include types: `genome`, `protein`, `rna`, `cds`, `gff3`, `gtf`, `gbff`, and `seq-report`.
- Disk-space monitoring and dynamic backoff.
- Cross-platform file organization and MD5 path normalization.
