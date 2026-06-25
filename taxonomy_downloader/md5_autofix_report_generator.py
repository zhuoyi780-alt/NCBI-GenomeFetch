"""
Report generation module for MD5 auto-fix functionality.

This module provides functionality to generate detailed reports about the
auto-fix process, including statistics and file lists.
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import List

from taxonomy_downloader.md5_autofix_models import (
    AutoFixResult,
    FailedFile,
    OrganizeResult,
    VerificationResult
)
from taxonomy_downloader.md5_autofix_state import AutoFixState

logger = logging.getLogger(__name__)


class ReportGenerator:
    """Generates detailed reports for the MD5 auto-fix process.
    
    This class creates a comprehensive report file (redownload_report.txt)
    that includes statistics, file lists, and processing information.
    
    Attributes:
        verification_dir: Directory where the report will be saved
        report_filename: Name of the report file (default: redownload_report.txt)
    
    Examples:
        >>> generator = ReportGenerator(Path("/data"))
        >>> result = AutoFixResult(10, 8, 7, 1, 2)
        >>> report_path = generator.generate_report(
        ...     failed_files=[],
        ...     auto_fix_result=result,
        ...     organize_result=OrganizeResult(8, 8, 0),
        ...     verification_result=VerificationResult(8, 7, 1),
        ...     start_time=datetime.now(),
        ...     end_time=datetime.now()
        ... )
        >>> report_path.exists()
        True
    """
    
    def __init__(self, verification_dir: Path, report_filename: str = "redownload_report.txt"):
        """Initialize the report generator.
        
        Args:
            verification_dir: Directory where the report will be saved
            report_filename: Name of the report file (default: redownload_report.txt)
        """
        self.verification_dir = Path(verification_dir)
        self.report_filename = report_filename
    
    def generate_report(
        self,
        failed_files: List[FailedFile],
        auto_fix_result: AutoFixResult,
        organize_result: OrganizeResult,
        verification_result: VerificationResult,
        start_time: datetime,
        end_time: datetime
    ) -> Path:
        """Generate a comprehensive auto-fix report.
        
        Args:
            failed_files: List of files that initially failed verification
            auto_fix_result: Result of the auto-fix process
            organize_result: Result of file organization
            verification_result: Result of re-verification
            start_time: Process start time
            end_time: Process end time
        
        Returns:
            Path to the generated report file
        
        Examples:
            >>> generator = ReportGenerator(Path("/data"))
            >>> result = AutoFixResult(10, 8, 7, 1, 2)
            >>> report_path = generator.generate_report(
            ...     failed_files=[],
            ...     auto_fix_result=result,
            ...     organize_result=OrganizeResult(8, 8, 0),
            ...     verification_result=VerificationResult(8, 7, 1),
            ...     start_time=datetime.now(),
            ...     end_time=datetime.now()
            ... )
            >>> report_path.name
            'redownload_report.txt'
        """
        report_path = self.verification_dir / self.report_filename
        
        logger.info(f"Generating auto-fix report: {report_path}")
        
        # Calculate processing duration
        duration = self._calculate_duration(start_time, end_time)
        
        # Build report content
        report_lines = []
        
        # Header
        report_lines.extend(self._generate_header(start_time, end_time, duration))
        
        # Summary statistics
        report_lines.extend(self._generate_summary(
            auto_fix_result,
            organize_result,
            verification_result
        ))
        
        # Failed files section
        report_lines.extend(self._generate_failed_files_section(failed_files))
        
        # Successfully fixed files section
        report_lines.extend(self._generate_fixed_files_section(
            verification_result.passed_files
        ))
        
        # Still failed files section
        report_lines.extend(self._generate_still_failed_section(
            verification_result.failed_files
        ))
        
        # Skipped files section
        report_lines.extend(self._generate_skipped_files_section(
            auto_fix_result.skipped_files
        ))
        
        # Footer
        report_lines.extend(self._generate_footer())
        
        # Write report to file
        try:
            with open(report_path, 'w', encoding='utf-8') as f:
                f.write('\n'.join(report_lines))
            
            logger.info(f"Report generated successfully: {report_path}")
            return report_path
        
        except Exception as e:
            logger.error(f"Error writing report to {report_path}: {e}")
            raise

    def generate_resume_report(self, state: AutoFixState) -> Path:
        """Generate a state-aware report for resumable auto-fix runs."""
        reports_dir = self.verification_dir / ".md5_autofix_state" / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        report_path = reports_dir / f"redownload_report.{state.run_id}.txt"

        lines = [
            "=" * 80,
            "MD5 VERIFICATION AUTO-FIX RESUME REPORT",
            "=" * 80,
            "",
            f"Report Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"Run ID: {state.run_id}",
            f"Verification Directory: {state.verification_dir}",
            f"State File: {self.verification_dir / '.md5_autofix_state' / 'autofix_state.json'}",
            "",
            "SUMMARY",
            "-" * 80,
        ]

        for key in sorted(state.summary):
            lines.append(f"{key}: {state.summary[key]}")

        lines.extend(["", "TASKS", "-" * 80])
        for task in sorted(state.tasks.values(), key=lambda item: item.original_path):
            lines.append(f"{task.status.value} | {task.original_path}")
            if task.accession_id:
                lines.append(f"  accession: {task.accession_id}")
            if task.cached_file:
                lines.append(f"  cached_file: {task.cached_file}")
            if task.backup_path:
                lines.append(f"  backup_path: {task.backup_path}")
            if task.last_error:
                lines.append(f"  error: {task.last_error}")

        lines.extend(
            [
                "",
                "NEXT RUN",
                "-" * 80,
                f"ncbi-genomefetch --md5sum-auto-fix",
                "",
            ]
        )

        report_path.write_text("\n".join(lines), encoding="utf-8")
        logger.info("State-aware auto-fix report generated: %s", report_path)
        return report_path
    
    def _generate_header(
        self,
        start_time: datetime,
        end_time: datetime,
        duration: str
    ) -> List[str]:
        """Generate report header section.
        
        Args:
            start_time: Process start time
            end_time: Process end time
            duration: Formatted duration string
        
        Returns:
            List of header lines
        """
        return [
            "=" * 80,
            "MD5 VERIFICATION AUTO-FIX REPORT",
            "=" * 80,
            "",
            f"Report Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"Verification Directory: {self.verification_dir}",
            "",
            f"Process Start Time: {start_time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"Process End Time: {end_time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"Total Processing Time: {duration}",
            ""
        ]
    
    def _generate_summary(
        self,
        auto_fix_result: AutoFixResult,
        organize_result: OrganizeResult,
        verification_result: VerificationResult
    ) -> List[str]:
        """Generate summary statistics section.
        
        Args:
            auto_fix_result: Result of the auto-fix process
            organize_result: Result of file organization
            verification_result: Result of re-verification
        
        Returns:
            List of summary lines
        """
        success_rate = auto_fix_result.get_success_rate()
        
        return [
            "=" * 80,
            "SUMMARY",
            "=" * 80,
            "",
            f"Total Failed Files: {auto_fix_result.total_failed}",
            f"Files Redownloaded: {auto_fix_result.redownloaded}",
            f"Files Organized: {organize_result.organized}",
            f"Files Successfully Fixed: {auto_fix_result.successfully_fixed}",
            f"Files Still Failed: {auto_fix_result.still_failed}",
            f"Files Skipped: {auto_fix_result.skipped}",
            f"Success Rate: {success_rate:.1f}%",
            ""
        ]
    
    def _generate_failed_files_section(self, failed_files: List[FailedFile]) -> List[str]:
        """Generate failed files section.
        
        Args:
            failed_files: List of files that initially failed
        
        Returns:
            List of section lines
        """
        lines = [
            "=" * 80,
            f"INITIALLY FAILED FILES ({len(failed_files)} files)",
            "=" * 80,
            ""
        ]
        
        if not failed_files:
            lines.append("No files failed verification.")
        else:
            for i, failed_file in enumerate(failed_files, 1):
                lines.append(
                    f"{i}. {failed_file.original_path} "
                    f"[{failed_file.status.value.upper()}]"
                )
                if failed_file.accession_id:
                    lines.append(f"   Accession ID: {failed_file.accession_id}")
                if failed_file.error_message:
                    lines.append(f"   Error: {failed_file.error_message}")
        
        lines.append("")
        return lines
    
    def _generate_fixed_files_section(self, fixed_files: List[str]) -> List[str]:
        """Generate successfully fixed files section.
        
        Args:
            fixed_files: List of successfully fixed file paths
        
        Returns:
            List of section lines
        """
        lines = [
            "=" * 80,
            f"SUCCESSFULLY FIXED FILES ({len(fixed_files)} files)",
            "=" * 80,
            ""
        ]
        
        if not fixed_files:
            lines.append("No files were successfully fixed.")
        else:
            for i, file_path in enumerate(fixed_files, 1):
                lines.append(f"{i}. {file_path}")
        
        lines.append("")
        return lines
    
    def _generate_still_failed_section(self, still_failed_files: List[str]) -> List[str]:
        """Generate still failed files section.
        
        Args:
            still_failed_files: List of file paths that still fail
        
        Returns:
            List of section lines
        """
        lines = [
            "=" * 80,
            f"STILL FAILED FILES ({len(still_failed_files)} files)",
            "=" * 80,
            ""
        ]
        
        if not still_failed_files:
            lines.append("All redownloaded files passed verification.")
        else:
            lines.append("The following files still fail verification after redownload:")
            lines.append("")
            for i, file_path in enumerate(still_failed_files, 1):
                lines.append(f"{i}. {file_path}")
        
        lines.append("")
        return lines
    
    def _generate_skipped_files_section(self, skipped_files: List[str]) -> List[str]:
        """Generate skipped files section.
        
        Args:
            skipped_files: List of file paths that were skipped
        
        Returns:
            List of section lines
        """
        lines = [
            "=" * 80,
            f"SKIPPED FILES ({len(skipped_files)} files)",
            "=" * 80,
            ""
        ]
        
        if not skipped_files:
            lines.append("No files were skipped.")
        else:
            lines.append("The following files were skipped (no valid Accession ID):")
            lines.append("")
            for i, file_path in enumerate(skipped_files, 1):
                lines.append(f"{i}. {file_path}")
        
        lines.append("")
        return lines
    
    def _generate_footer(self) -> List[str]:
        """Generate report footer.
        
        Returns:
            List of footer lines
        """
        return [
            "=" * 80,
            "END OF REPORT",
            "=" * 80
        ]
    
    def _calculate_duration(self, start_time: datetime, end_time: datetime) -> str:
        """Calculate and format processing duration.
        
        Args:
            start_time: Process start time
            end_time: Process end time
        
        Returns:
            Formatted duration string (e.g., "2m 30s" or "45s")
        
        Examples:
            >>> generator = ReportGenerator(Path("/data"))
            >>> start = datetime(2024, 1, 1, 12, 0, 0)
            >>> end = datetime(2024, 1, 1, 12, 2, 30)
            >>> generator._calculate_duration(start, end)
            '2m 30s'
        """
        duration_seconds = (end_time - start_time).total_seconds()
        
        if duration_seconds < 60:
            return f"{int(duration_seconds)}s"
        
        minutes = int(duration_seconds // 60)
        seconds = int(duration_seconds % 60)
        
        if minutes < 60:
            return f"{minutes}m {seconds}s"
        
        hours = minutes // 60
        minutes = minutes % 60
        return f"{hours}h {minutes}m {seconds}s"
