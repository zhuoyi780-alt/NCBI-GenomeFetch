"""
Rebuild accession MD5 manifests from an existing dehydrated datasets package.
"""

import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple, Union

from .accession_manifest import AccessionManifest, ArtifactRecord
from .accession_parser import load_accessions
from .file_type_utils import (
    detect_file_type,
    extract_accession_from_path,
    standardize_filename,
)


@dataclass
class RebuildMD5Result:
    """Summary of an accession MD5 rebuild operation."""

    committed_artifacts: int = 0
    missing_outputs: List[str] = field(default_factory=list)
    skipped_unrequested: int = 0
    skipped_unrecognized: int = 0


class AccessionMD5Rebuilder:
    """Use MD5 values from a dehydrated package as trusted expected checksums."""

    def __init__(
        self,
        accession_file: Union[str, Path],
        output_dir: Union[str, Path],
        dehydrated_package: Union[str, Path],
        include_params: Optional[Sequence[str]] = None,
    ):
        self.accession_file = Path(accession_file)
        self.output_dir = Path(output_dir)
        self.dehydrated_package = Path(dehydrated_package)
        self.include_params = list(include_params or ["genome"])

    def rebuild(self) -> RebuildMD5Result:
        accessions, _ = load_accessions(str(self.accession_file))
        requested_accessions = set(accessions)
        md5_entries = self._read_package_md5_entries()
        records: List[ArtifactRecord] = []
        missing_outputs: List[str] = []
        skipped_unrequested = 0
        skipped_unrecognized = 0

        for md5_hash, package_path in md5_entries:
            accession = extract_accession_from_path(Path(package_path))
            if not accession:
                skipped_unrecognized += 1
                continue
            if accession not in requested_accessions:
                skipped_unrequested += 1
                continue

            include_type = detect_file_type(Path(package_path).name, self.include_params)
            if not include_type:
                skipped_unrecognized += 1
                continue

            filename = standardize_filename(Path(package_path), accession, include_type)
            output_path = self.output_dir / filename
            if not output_path.exists():
                missing_outputs.append(filename)
                continue

            records.append(
                ArtifactRecord(
                    accession=accession,
                    include_type=include_type,
                    filename=filename,
                    expected_md5=md5_hash,
                    checksum_source=(
                        f"dehydrated_package_md5:{self.dehydrated_package.name}"
                    ),
                )
            )

        manifest = AccessionManifest(self.output_dir)
        manifest.load()
        manifest.import_existing_md5()
        before_count = len(manifest.artifacts)
        manifest.commit_artifacts(records)

        return RebuildMD5Result(
            committed_artifacts=len(manifest.artifacts) - before_count,
            missing_outputs=sorted(set(missing_outputs)),
            skipped_unrequested=skipped_unrequested,
            skipped_unrecognized=skipped_unrecognized,
        )

    def _read_package_md5_entries(self) -> List[Tuple[str, str]]:
        md5_text = self._read_package_md5_text()
        entries = []
        for line in md5_text.splitlines():
            parsed = self._parse_md5_line(line)
            if parsed:
                entries.append(parsed)
        return entries

    def _read_package_md5_text(self) -> str:
        with zipfile.ZipFile(self.dehydrated_package, "r") as zip_ref:
            md5_name = self._find_md5_member(zip_ref.namelist())
            if not md5_name:
                raise ValueError(
                    f"Dehydrated package has no md5sum.txt: {self.dehydrated_package}"
                )
            return zip_ref.read(md5_name).decode("utf-8")

    def _find_md5_member(self, names: Iterable[str]) -> Optional[str]:
        names = list(names)
        if "md5sum.txt" in names:
            return "md5sum.txt"
        candidates = sorted(name for name in names if name.endswith("/md5sum.txt"))
        return candidates[0] if candidates else None

    def _parse_md5_line(self, line: str) -> Optional[Tuple[str, str]]:
        line = line.strip()
        if not line or line.startswith("#"):
            return None
        parts = line.split(None, 1)
        if len(parts) != 2:
            return None
        md5_hash, package_path = parts
        if len(md5_hash) != 32:
            return None
        if package_path.startswith("*"):
            package_path = package_path[1:]
        return md5_hash, package_path.replace("\\", "/")
