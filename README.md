# NCBI-GenomeFetch v1.0.0

NCBI-GenomeFetch is a command-line tool for batch downloading genome data from
NCBI. It supports three workflows:

- Taxon mode: download genomes by taxonomy name or TaxID.
- Accession mode: download genomes by accession list with resumable batches.
- Split mode: split large taxonomy download jobs into smaller task files.

## Contents

- [Installation](#installation)
- [Quick Start](#quick-start)
- [Workflows](#workflows)
- [Command Line Reference](#command-line-reference)
- [Resume Support](#resume-support)
- [Input File Formats](#input-file-formats)
- [Output Layout](#output-layout)
- [Performance](#performance)
- [Troubleshooting](#troubleshooting)
- [License](#license)

## Installation

### Requirements

- Python 3.8 or later
- NCBI Datasets CLI 16.0.0 or later

### Install the NCBI Datasets CLI

```bash
# Windows
curl -o datasets.exe https://ftp.ncbi.nlm.nih.gov/pub/datasets/command-line/v2/win64/datasets.exe

# Linux
curl -o datasets https://ftp.ncbi.nlm.nih.gov/pub/datasets/command-line/v2/linux-amd64/datasets
chmod +x datasets

# macOS
curl -o datasets https://ftp.ncbi.nlm.nih.gov/pub/datasets/command-line/v2/mac/datasets
chmod +x datasets

# Conda
conda install -c conda-forge ncbi-datasets-cli
```

### Install NCBI-GenomeFetch

From the repository root:

```bash
pip install .
```

### Verify the installation

```bash
datasets --version
python --version
ncbi-genomefetch --help
```

## Quick Start

### Taxon mode

```bash
# Create an input file. TaxIDs are recommended.
echo "562" > taxa.txt
echo "1423" >> taxa.txt

# Download genomes.
ncbi-genomefetch -i taxa.txt -o genomes/

# Use an NCBI API key and more workers.
ncbi-genomefetch -i taxa.txt -o genomes/ -k YOUR_API_KEY -w 8

# Download multiple data types.
ncbi-genomefetch -i taxa.txt -o genomes/ --include genome,protein,gff3
```

### Accession mode

```bash
# Create an accession file.
echo "GCF_000005845.2" > accessions.txt
echo "GCF_000009045.1" >> accessions.txt

# Download by accession list.
ncbi-genomefetch -a accessions.txt -o genomes/

# Tune batch size and workers.
ncbi-genomefetch -a accessions.txt -o genomes/ -b 50 -w 4

# Resume after interruption by rerunning the same command.
ncbi-genomefetch -a accessions.txt -o genomes/
```

### Split mode

```bash
# Split a large taxon into smaller task files.
ncbi-genomefetch -s Bacteria -o Bacteria_split/

# Use the split results as taxon-mode inputs.
ncbi-genomefetch -i Bacteria_split/group1.txt -o downloads/group1/
```

## Workflows

### Taxon mode: `-i/--input`

Taxon mode downloads genome data by taxonomy name or TaxID through the NCBI
Datasets dehydration and rehydration workflow.

Main behavior:

- Accepts taxonomy names and TaxIDs.
- Supports multiple data types, including genome, protein, gff3, cds, gbff,
  and sequence reports.
- Tracks progress in `.progress_state.json`.
- Can validate existing output before skipping completed taxa.
- Generates taxonomy reports and MD5 checksum files.

Example:

```bash
ncbi-genomefetch -i taxa.txt -o genomes/ --include genome,protein,gff3
```

### Accession mode: `-a/--accession`

Accession mode downloads genome data for a list of GCA or GCF accessions.

Main behavior:

- Processes accessions in batches.
- Saves progress in `.accession_progress_state.json`.
- Skips completed accessions when rerun.
- Standardizes output file names as `{accession}.{extension}`.
- Rebuilds merged MD5 metadata for downloaded files.

Example:

```bash
ncbi-genomefetch -a accessions.txt -o genomes/ -b 100 -w 8
```

### Split mode: `-s/--split`

Split mode partitions large taxonomy datasets into smaller task files for
distributed or staged downloads.

Input data folder requirements:

- `{taxon}.tsv`: genome assembly report
- `taxonomy_report.jsonl`: taxonomy report

Example:

```bash
ncbi-genomefetch -s Bacteria -o Bacteria_split/
```

## Command Line Reference

### Mode selection

| Option | Description | Example |
| --- | --- | --- |
| `-i FILE`, `--input FILE` | Taxon mode input file | `-i taxa.txt` |
| `-a FILE`, `--accession FILE` | Accession mode input file | `-a accessions.txt` |
| `-s TAXON`, `--split TAXON` | Split mode for a taxon or taxon folder | `-s Bacteria` |
| `--md5sum DIRECTORY` | Verify MD5 checksums in a directory | `--md5sum genomes/` |
| `--md5sum-auto-fix [FILE]` | Redownload and repair failed MD5 entries | `--md5sum-auto-fix` |
| `--rebuild-md5` | Rebuild accession MD5 metadata from a dehydrated package | `--rebuild-md5` |

### Common options

| Option | Description | Default |
| --- | --- | --- |
| `-o DIR`, `--output DIR` | Output directory | Required for download modes |
| `-k KEY`, `--api-key KEY` | NCBI API key | Not set |
| `-w N`, `--workers N` | Number of worker threads | `2` |
| `-b N`, `--batch N` | Accessions per batch | `100` |
| `--temp-dir DIR` | Temporary directory | System default |
| `--datasets-exe PATH` | Path to the NCBI Datasets executable | `datasets` |
| `--include TYPES` | Comma-separated data types | `genome` |
| `--assembly-source SRC` | Assembly source filter | Not set |
| `--additional-params PARAMS` | Extra Datasets CLI filters | Not set |
| `--no-validate-resume-files` | Trust progress state without checking output files | Disabled |

### Data type options

`--include` accepts:

- `genome`
- `protein`
- `rna`
- `cds`
- `gff3`
- `gtf`
- `gbff`
- `seq-report`
- `none`

Examples:

```bash
ncbi-genomefetch -i taxa.txt -o genomes/ --include genome,protein,gff3
ncbi-genomefetch -i taxa.txt -o genomes/ --include genome,protein,rna,cds,gff3,gtf,gbff,seq-report
```

### Assembly source options

`--assembly-source` accepts:

- `refseq`
- `genbank`
- `all`

Examples:

```bash
ncbi-genomefetch -i taxa.txt -o genomes/ --assembly-source refseq
ncbi-genomefetch -i taxa.txt -o genomes/ --assembly-source genbank
```

### Additional Datasets filters

Use `--additional-params` for extra key-value filters passed to the Datasets
command.

```bash
ncbi-genomefetch -i taxa.txt -o genomes/ --additional-params reference=true
ncbi-genomefetch -i taxa.txt -o genomes/ --additional-params assembly-level=complete,annotated=true
```

### Disk space backoff

| Option | Description | Default |
| --- | --- | --- |
| `--disable-disk-backoff` | Disable dynamic disk-space backoff | Disabled |
| `--disk-warning-threshold PERCENT` | Warning free-space percentage threshold | `0.20` |
| `--disk-critical-threshold PERCENT` | Critical free-space percentage threshold | `0.10` |
| `--disk-minimum-threshold PERCENT` | Pause threshold as free-space percentage | `0.05` |
| `--disk-warning-bytes SIZE` | Warning free-space byte threshold | `10GB` |
| `--disk-critical-bytes SIZE` | Critical free-space byte threshold | `5GB` |
| `--disk-minimum-bytes SIZE` | Pause threshold as free bytes | `1GB` |
| `--disk-check-interval SECONDS` | Disk check interval | `30` |

Examples:

```bash
ncbi-genomefetch -i taxa.txt -o genomes/ --disk-warning-bytes 20GB
ncbi-genomefetch -i taxa.txt -o genomes/ --disk-minimum-bytes 5GB
ncbi-genomefetch -i taxa.txt -o genomes/ --disable-disk-backoff
```

## Resume Support

### Taxon mode

Taxon mode stores progress in:

```text
{output_dir}/.progress_state.json
```

Rerun the same command to continue from saved progress:

```bash
ncbi-genomefetch -i taxa.txt -o genomes/
```

Use this option to trust the progress file without validating output files:

```bash
ncbi-genomefetch -i taxa.txt -o genomes/ --no-validate-resume-files
```

### Accession mode

Accession mode stores progress in:

```text
{output_dir}/.accession_progress_state.json
```

Rerun the same command to continue remaining accessions:

```bash
ncbi-genomefetch -a accessions.txt -o genomes/
```

## MD5 Verification and Repair

### Verify checksums

```bash
ncbi-genomefetch --md5sum genomes/
```

The command writes a verification report and a failed-file list when failures
are found.

### Repair from a failed-file list

```bash
ncbi-genomefetch --md5sum-auto-fix
ncbi-genomefetch --md5sum-auto-fix custom_failed_files.txt
```

### Verify and repair in one run

```bash
ncbi-genomefetch --md5sum genomes/ --md5sum-auto-fix
ncbi-genomefetch --md5sum genomes/ --md5sum-auto-fix custom_failed_files.txt
```

Failed-file list format:

```text
STATUS | FILE_PATH | EXPECTED_HASH | COMPUTED_HASH | ERROR_MESSAGE
FAIL | Bacteria/Escherichia_coli/GCF_000005845.2/genome.fna | abc123... | def456... |
MISSING | Bacteria/Salmonella/GCF_000006945.2/protein.faa | xyz789... | N/A | File not found
ERROR | Archaea/Methanococcus/GCF_000007845.1/genome.fna | 123abc... | N/A | Permission denied
```

## Input File Formats

### Taxon input

Taxon mode supports TaxIDs:

```text
562
1423
```

It also supports name and TaxID pairs separated by a tab:

```text
Escherichia coli    562
Bacillus subtilis   1423
```

Split-mode output files can also be used as taxon-mode input:

```text
# Species: Escherichia coli (562)
Escherichia coli    562
Escherichia coli K-12   83333
Escherichia coli O157:H7    83334
```

Lines beginning with `#` and blank lines are ignored.

### Accession input

Accession mode expects one accession per line:

```text
GCF_000005845.2
GCF_000009045.1
GCA_000001405.29
```

Duplicate accessions are removed while preserving input order.

## Output Layout

### Taxon mode

```text
genomes/
|-- Escherichia_coli_562/
|   |-- GCF_000005845.2.fna
|   |-- GCF_000005845.2.faa
|   |-- GCF_000005845.2.gff
|   `-- ...
|-- Bacillus_subtilis_1423/
|   `-- ...
|-- md5sum.txt
|-- taxonomy_summary.tsv
|-- taxonomy_report.jsonl
`-- .progress_state.json
```

### Accession mode

```text
genomes/
|-- GCF_000005845.2.fna
|-- GCF_000009045.1.fna
|-- GCA_000001405.29.fna
|-- md5sum.txt
`-- .accession_progress_state.json
```

Accession output files use the standardized format:

```text
{accession}.{extension}
```

Common extensions include:

- `.fna` for genome
- `.faa` for protein
- `.gff` for GFF3
- `.cds.fna` for CDS
- `.gbff` for GenBank files

## Performance

Use an NCBI API key for higher rate limits:

```bash
ncbi-genomefetch -i taxa.txt -o genomes/ -k YOUR_API_KEY
```

Suggested starting points:

| Scenario | Suggested settings |
| --- | --- |
| Small download, fewer than 10 entries | `-w 2 -b 50` |
| Medium download, 10 to 100 entries | `-w 4 -b 100 -k API_KEY` |
| Large download, more than 100 entries | `-w 8 -b 150 -k API_KEY` |
| Very large download, more than 1000 entries | Use split mode and run groups separately |

## Troubleshooting

### `datasets` command not found

Add the Datasets CLI to `PATH` or pass its full path:

```bash
ncbi-genomefetch -i taxa.txt -o genomes/ --datasets-exe /path/to/datasets
```

On Windows:

```powershell
ncbi-genomefetch -i taxa.txt -o genomes/ --datasets-exe C:\path\to\datasets.exe
```

### Downloads are slow

Use an NCBI API key and tune worker count:

```bash
ncbi-genomefetch -i taxa.txt -o genomes/ -k YOUR_API_KEY -w 8
```

### Disk space is low

Use a different output directory, clean disk space, or adjust backoff thresholds:

```bash
ncbi-genomefetch -i taxa.txt -o /other/path/genomes/
ncbi-genomefetch -i taxa.txt -o genomes/ --disk-minimum-bytes 5GB
```

### A download was interrupted

Rerun the same command. Progress state files are used to skip completed work.

```bash
ncbi-genomefetch -a accessions.txt -o genomes/
ncbi-genomefetch -i taxa.txt -o genomes/
```

### Files were manually downloaded

If you manually downloaded and rehydrated missing data, verify the file names,
directory structure, and `md5sum.txt` entries before merging the files into an
NCBI-GenomeFetch output directory. Rerunning the original command is preferred
because the tool can use its progress state and validation logic.

## Development

Run the smoke tests:

```bash
python -m unittest discover -s tests
```

Compile the package:

```bash
python -m compileall -q taxonomy_downloader tests
```

Build source and wheel distributions:

```bash
pyproject-build --no-isolation
```

## License

This project is released under the MIT License. See [LICENSE](LICENSE).

## Support

- Documentation: [README.md](README.md)
- Quick start: [QUICKSTART.md](QUICKSTART.md)
- Issues: [GitHub Issues](https://github.com/zhuoyi780-alt/NCBI-GenomeFetch/issues)

## Acknowledgements

NCBI-GenomeFetch depends on the NCBI Datasets CLI and NCBI data services.
