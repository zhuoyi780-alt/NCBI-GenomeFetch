# GitHub Release Checklist

## Repository scope

- Publish the `NCBI-GenomeFetch-v1.0.0/` directory as the repository root.
- Keep the outer software-copyright application materials out of the GitHub repository.
- Do not commit `datasets.exe`; document installation instead.
- Do not commit generated `__pycache__/`, download outputs, progress state files, or large split example outputs.

## Required before first push

- Confirm the final GitHub repository URL in `pyproject.toml` and `setup.py`.
- Decide whether the first public version is `1.0.0` or a later version, then keep `pyproject.toml`, `setup.py`, `taxonomy_downloader/__init__.py`, README, QUICKSTART, and CHANGELOG aligned.
- Run `python -m compileall -q taxonomy_downloader`.
- Run `python -m unittest discover -s tests`.
- Confirm README examples reference files that exist in the repository.

## Suggested first GitHub release

- Tag: `v1.0.0`
- Release title: `NCBI-GenomeFetch v1.0.0`
- Release notes: summarize taxon downloads, accession downloads, task splitting, MD5 verification/repair, resume support, and disk-space backoff.
- Attach no bundled NCBI binary; link users to the official NCBI Datasets CLI installation instructions.
