"""
Report generation for MD5 verification results.

This module provides functionality to generate human-readable reports of MD5
verification results, supporting both console output and file-based reports.
"""

from pathlib import Path
from typing import TextIO
from taxonomy_downloader.md5_models import VerificationResult, FileVerificationResult, VerificationStatus


class MD5ReportGenerator:
    """Generates verification reports for MD5 checksum validation.
    
    This class formats verification results into human-readable reports that can
    be displayed to the console or written to files. Reports include summary
    statistics and detailed results for each verified file.
    
    Examples:
        >>> generator = MD5ReportGenerator()
        >>> generator.generate_console_report(verification_result)
        >>> generator.generate_file_report(verification_result, Path("report.txt"))
    """
    
    def generate_console_report(self, result: VerificationResult) -> None:
        """
        Display verification results to console.
        
        Outputs a formatted report to stdout showing summary statistics only.
        Individual file verification results are not displayed to console
        but are saved to the file report.
        
        Args:
            result: Verification results to display
        
        Examples:
            >>> generator = MD5ReportGenerator()
            >>> result = VerificationResult(10, 8, 1, 1, 0, [], 5.2)
            >>> generator.generate_console_report(result)
            ============================================================
            MD5 VERIFICATION REPORT
            ============================================================
            Total Files: 10
            Passed: 8
            Failed: 1
            Missing: 1
            Errors: 0
            Processing Time: 5.20 seconds
            ============================================================
        """
        print(self._format_summary(result))
        print("=" * 60)
        
        # Final status message
        if result.is_success():
            print("✓ All files verified successfully!")
        else:
            print("✗ Verification completed with issues.")
            print("  See detailed report file for individual file results.")
        print("=" * 60)
    
    def generate_file_report(self, result: VerificationResult, output_path: Path) -> None:
        """
        Write verification results to file.
        
        Creates a text file containing the complete verification report with
        summary statistics and detailed results for each file. The report format
        is identical to the console output but without color codes.
        
        Args:
            result: Verification results to write
            output_path: Path to output report file
        
        Raises:
            IOError: If file cannot be written
            PermissionError: If output path is not writable
        
        Examples:
            >>> generator = MD5ReportGenerator()
            >>> result = VerificationResult(10, 8, 1, 1, 0, [], 5.2)
            >>> generator.generate_file_report(result, Path("verification_report.txt"))
        """
        try:
            with open(output_path, 'w', encoding='utf-8') as f:
                self._write_report(f, result)
        except (IOError, PermissionError) as e:
            raise IOError(f"Failed to write report to {output_path}: {e}")
    
    def _write_report(self, file: TextIO, result: VerificationResult) -> None:
        """
        Write formatted report to file handle.
        
        Args:
            file: File handle to write to
            result: Verification results to write
        """
        file.write(self._format_summary(result))
        file.write("\n")
        
        if result.file_results:
            file.write("\nDETAILED RESULTS:\n")
            file.write("-" * 60 + "\n")
            for file_result in result.file_results:
                file.write(self._format_file_result(file_result))
                file.write("\n")
        
        file.write("=" * 60 + "\n")
        
        # Final status message
        if result.is_success():
            file.write("✓ All files verified successfully!\n")
        else:
            file.write("✗ Verification completed with issues. See details above.\n")
        file.write("=" * 60 + "\n")
    
    def _format_summary(self, result: VerificationResult) -> str:
        """
        Format summary statistics.
        
        Creates a formatted string containing overall verification statistics
        including total files, pass/fail/missing/error counts, and processing time.
        
        Args:
            result: Verification results to summarize
        
        Returns:
            Formatted summary string with statistics
        
        Examples:
            >>> generator = MD5ReportGenerator()
            >>> result = VerificationResult(10, 8, 1, 1, 0, [], 5.2)
            >>> summary = generator._format_summary(result)
            >>> "Total Files: 10" in summary
            True
            >>> "Passed: 8" in summary
            True
        """
        lines = []
        lines.append("=" * 60)
        lines.append("MD5 VERIFICATION REPORT")
        lines.append("=" * 60)
        lines.append(f"Total Files: {result.total_files}")
        lines.append(f"Passed: {result.passed}")
        lines.append(f"Failed: {result.failed}")
        lines.append(f"Missing: {result.missing}")
        lines.append(f"Errors: {result.errors}")
        lines.append(f"Processing Time: {result.processing_time:.2f} seconds")
        
        # Calculate success rate if there are files
        if result.total_files > 0:
            success_rate = (result.passed / result.total_files * 100)
            lines.append(f"Success Rate: {success_rate:.1f}%")
        else:
            lines.append("Success Rate: N/A (no files processed)")
        
        lines.append("=" * 60)
        
        return "\n".join(lines)
    
    def _format_file_result(self, file_result: FileVerificationResult) -> str:
        """
        Format individual file result.
        
        Creates a formatted string for a single file's verification result,
        including file path, status, and hash information. For failed files,
        includes both expected and computed hashes. For missing or error files,
        includes appropriate error messages.
        
        Args:
            file_result: Single file verification result to format
        
        Returns:
            Formatted result string for the file
        
        Examples:
            >>> generator = MD5ReportGenerator()
            >>> result = FileVerificationResult(
            ...     "genome.fna",
            ...     "d41d8cd98f00b204e9800998ecf8427e",
            ...     "d41d8cd98f00b204e9800998ecf8427e",
            ...     VerificationStatus.PASS
            ... )
            >>> formatted = generator._format_file_result(result)
            >>> "genome.fna" in formatted
            True
            >>> "PASS" in formatted
            True
        """
        status_symbol = self._get_status_symbol(file_result.status)
        status_text = file_result.status.value.upper()
        
        # Base line with file path and status
        line = f"{status_symbol} [{status_text}] {file_result.file_path}"
        
        # Add additional details based on status
        if file_result.status == VerificationStatus.PASS:
            line += f"\n  Hash: {file_result.expected_hash}"
        
        elif file_result.status == VerificationStatus.FAIL:
            line += f"\n  Expected: {file_result.expected_hash}"
            line += f"\n  Computed: {file_result.computed_hash}"
        
        elif file_result.status == VerificationStatus.MISSING:
            line += f"\n  Expected hash: {file_result.expected_hash}"
            line += "\n  File not found"
        
        elif file_result.status == VerificationStatus.ERROR:
            line += f"\n  Expected hash: {file_result.expected_hash}"
            if file_result.error_message:
                line += f"\n  Error: {file_result.error_message}"
        
        return line
    
    def _get_status_symbol(self, status: VerificationStatus) -> str:
        """
        Get visual symbol for verification status.
        
        Returns an appropriate symbol to visually indicate the verification
        status in reports (checkmark for pass, X for fail, etc.).
        
        Args:
            status: Verification status
        
        Returns:
            Symbol character for the status
        
        Examples:
            >>> generator = MD5ReportGenerator()
            >>> generator._get_status_symbol(VerificationStatus.PASS)
            '✓'
            >>> generator._get_status_symbol(VerificationStatus.FAIL)
            '✗'
        """
        symbols = {
            VerificationStatus.PASS: "✓",
            VerificationStatus.FAIL: "✗",
            VerificationStatus.MISSING: "?",
            VerificationStatus.ERROR: "!",
        }
        return symbols.get(status, "?")
