"""
Persistent trusted MD5 manifest for accession downloads.
"""

import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Iterable, Union


class ManifestConflictError(Exception):
    """Raised when a filename is committed with a different trusted hash."""


@dataclass(frozen=True)
class ArtifactRecord:
    """Trusted checksum record for one accession output artifact."""

    accession: str
    include_type: str
    filename: str
    expected_md5: str
    checksum_source: str = "ncbi_datasets_package"
    committed_at: str = ""

    def to_dict(self) -> dict:
        data = asdict(self)
        if not data["committed_at"]:
            data["committed_at"] = _utc_timestamp()
        return data


def _utc_timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class AccessionManifest:
    """Manage .accession_manifest.json and its md5sum.txt projection."""

    schema_version = 1

    def __init__(self, output_dir: Union[str, Path]):
        self.output_dir = Path(output_dir)
        self.manifest_path = self.output_dir / ".accession_manifest.json"
        self.md5_path = self.output_dir / "md5sum.txt"
        self.artifacts: Dict[str, dict] = {}

    def load(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        if not self.manifest_path.exists():
            self.artifacts = {}
            return

        with open(self.manifest_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if data.get("schema_version") != self.schema_version:
            raise ValueError(
                f"Unsupported accession manifest schema: {data.get('schema_version')}"
            )

        self.artifacts = dict(data.get("artifacts", {}))

    def import_existing_md5(self) -> None:
        """Import legacy root md5sum.txt once, preserving trusted historical rows."""
        if self.artifacts or not self.md5_path.exists():
            return

        imported = {}
        with open(self.md5_path, "r", encoding="utf-8") as f:
            for line in f:
                parsed = self._parse_md5_line(line)
                if not parsed:
                    continue
                expected_md5, filename = parsed
                if not (self.output_dir / filename).exists():
                    continue
                accession = filename.rsplit(".", 1)[0]
                imported[filename] = ArtifactRecord(
                    accession=accession,
                    include_type=self._infer_include_type(filename),
                    filename=filename,
                    expected_md5=expected_md5,
                    checksum_source="legacy_md5sum_import",
                ).to_dict()

        if not imported:
            return

        backup_path = self.output_dir / "md5sum.txt.pre_manifest_migration.bak"
        if not backup_path.exists():
            backup_tmp = backup_path.with_suffix(backup_path.suffix + ".tmp")
            backup_tmp.write_text(self.md5_path.read_text(encoding="utf-8"), encoding="utf-8")
            os.replace(backup_tmp, backup_path)

        self.artifacts.update(imported)
        self._write_manifest_atomic()
        self.write_md5sum_atomic()

    def commit_artifacts(self, artifacts: Iterable[Union[ArtifactRecord, dict]]) -> None:
        changed = False
        for artifact in artifacts:
            record = self._coerce_record(artifact)
            existing = self.artifacts.get(record.filename)
            if existing:
                if existing.get("expected_md5") != record.expected_md5:
                    raise ManifestConflictError(
                        f"Conflicting MD5 for {record.filename}: "
                        f"{existing.get('expected_md5')} != {record.expected_md5}"
                    )
                continue

            self.artifacts[record.filename] = record.to_dict()
            changed = True

        if changed:
            self._write_manifest_atomic()
            self.write_md5sum_atomic()

    def has_trusted_artifact(self, filename: str, expected_md5: str = None) -> bool:
        artifact = self.artifacts.get(filename)
        if not artifact:
            return False
        if expected_md5 and artifact.get("expected_md5") != expected_md5:
            return False
        return bool(artifact.get("expected_md5"))

    def coverage(self) -> dict:
        output_files = [
            path
            for path in self.output_dir.iterdir()
            if path.is_file()
            and path.name not in {"md5sum.txt", ".accession_manifest.json"}
            and not path.name.startswith(".")
        ]
        tracked = [path for path in output_files if path.name in self.artifacts]
        return {
            "tracked_artifacts": len(tracked),
            "discovered_artifacts": len(output_files),
            "untracked_artifacts": len(output_files) - len(tracked),
        }

    def write_md5sum_atomic(self) -> None:
        tmp_path = self.md5_path.with_suffix(".txt.tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            for filename in sorted(self.artifacts):
                f.write(f"{self.artifacts[filename]['expected_md5']}  {filename}\n")
        os.replace(tmp_path, self.md5_path)

    def _write_manifest_atomic(self) -> None:
        tmp_path = self.manifest_path.with_suffix(".json.tmp")
        data = {
            "schema_version": self.schema_version,
            "generated_by": "ncbi-genomefetch",
            "artifacts": self.artifacts,
        }
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp_path, self.manifest_path)

    def _coerce_record(self, artifact: Union[ArtifactRecord, dict]) -> ArtifactRecord:
        if isinstance(artifact, ArtifactRecord):
            return artifact
        if hasattr(artifact, "accession"):
            return ArtifactRecord(
                accession=artifact.accession,
                include_type=artifact.include_type,
                filename=artifact.filename,
                expected_md5=artifact.expected_md5,
            )
        return ArtifactRecord(
            accession=artifact["accession"],
            include_type=artifact["include_type"],
            filename=artifact["filename"],
            expected_md5=artifact["expected_md5"],
            checksum_source=artifact.get("checksum_source", "ncbi_datasets_package"),
            committed_at=artifact.get("committed_at", ""),
        )

    def _parse_md5_line(self, line: str):
        line = line.strip()
        if not line or line.startswith("#"):
            return None
        parts = line.split(None, 1)
        if len(parts) != 2:
            return None
        expected_md5, filename = parts
        if filename.startswith("*"):
            filename = filename[1:]
        filename = filename.replace("\\", "/")
        if "/" in filename or len(expected_md5) != 32:
            return None
        return expected_md5, filename

    def _infer_include_type(self, filename: str) -> str:
        if filename.endswith(".faa"):
            return "protein"
        if filename.endswith(".cds"):
            return "cds"
        if filename.endswith(".rna.fna"):
            return "rna"
        if filename.endswith(".gff"):
            return "gff3"
        if filename.endswith(".gtf"):
            return "gtf"
        if filename.endswith(".gbff"):
            return "gbff"
        if filename.endswith(".jsonl"):
            return "seq-report"
        return "genome"
