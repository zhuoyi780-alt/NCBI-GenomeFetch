import subprocess
import sys
import unittest

import taxonomy_downloader
from taxonomy_downloader.datasets_interface import DatasetsInterface
from taxonomy_downloader.accession_batch_processor import AccessionBatchProcessor
from taxonomy_downloader.models import DownloadConfig


class SmokeTests(unittest.TestCase):
    def test_package_version_is_exposed(self):
        self.assertEqual(taxonomy_downloader.__version__, "1.0.1")

    def test_cli_help_runs(self):
        result = subprocess.run(
            [sys.executable, "-m", "taxonomy_downloader.cli", "--help"],
            check=False,
            capture_output=True,
            text=True,
        )

        self.assertEqual(result.returncode, 0)
        self.assertIn("Download genome data from NCBI", result.stdout)
        self.assertNotIn("RuntimeWarning", result.stderr)

    def test_taxon_argument_is_not_shell_quoted(self):
        interface = DatasetsInterface.__new__(DatasetsInterface)
        interface._resolved_executable = "datasets"
        interface.config = DownloadConfig(
            input_file="taxa.txt",
            output_dir="out",
            api_key="abc123456789",
        )

        cmd = interface.build_download_command("Escherichia coli", "out.zip")

        self.assertIn("Escherichia coli", cmd)
        self.assertNotIn("'Escherichia coli'", cmd)
        self.assertNotIn('"Escherichia coli"', cmd)

    def test_api_key_is_redacted_for_logs(self):
        cmd = ["datasets", "rehydrate", "--api-key", "abc123456789"]

        self.assertEqual(
            DatasetsInterface._redact_command(cmd),
            ["datasets", "rehydrate", "--api-key", "***REDACTED***"],
        )

    def test_accession_mode_keeps_datasets_path_as_single_argument(self):
        processor = AccessionBatchProcessor.__new__(AccessionBatchProcessor)
        processor.datasets_exe = r"C:\Program Files\NCBI\datasets.exe"

        self.assertEqual(
            processor._datasets_command_prefix(),
            [r"C:\Program Files\NCBI\datasets.exe"],
        )


if __name__ == "__main__":
    unittest.main()
