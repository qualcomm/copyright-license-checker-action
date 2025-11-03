import json
import tempfile
import subprocess
import warnings
import os
from pathlib import Path
from scanner.patch import Patch

warnings.filterwarnings("ignore", message="Libmagic magic database not found")

"""
Module to check for licenses in a patch file using scancode.
"""

class LicenseChecker:
    """
    Class to check for licenses in a patch file.
    """

    def __init__(self, patch: Patch, repo: str, permissive_licenses: list) -> None:
        """
        Initialize the LicenseChecker object.

        Args:
            patch (Patch): The patch file to check.
            repo (str): The repository name.
            permissive_licenses (list): A list of permissive licenses.
        """
        self.patch = patch
        self.repo = repo
        self.permissive_licenses = permissive_licenses

    def is_license_permissive(self, scancode_license: str) -> bool:
        """
        Check if a license is permissive.

        Args:
            scancode_license (str): The license to check.

        Returns:
            bool: True if the license is permissive, False otherwise.
        """
        # Split the scancode license string by 'AND' and 'OR'
        licenses = [lic.strip() for lic in scancode_license.replace('AND', 'OR').split('OR')]
        # Check if any license is not in the permissive licenses list
        for lic in licenses:
            if lic not in self.permissive_licenses:
                return False
        return True

    def detect_licenses_batch(self, changes: list) -> dict:
        """
        Detect licenses for multiple changes in a single scancode run.
        Args:
            changes (list): List of changes to check.
        Returns:
            dict: Dictionary mapping (change_index, content_type) -> licenses.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            file_map = {}

            for idx, change in enumerate(changes):
                content = change['content']
                # Check if content is None
                if not content:
                    continue

                added_lines = []
                deleted_lines = []
                # Separate added and deleted lines
                for line in content.split('\n'):
                    if line.startswith('+'):
                        added_lines.append(line[1:])
                    elif line.startswith('-'):
                        deleted_lines.append(line[1:])

                # Join added and deleted lines as-is
                if added_lines:
                    added_file = f"{idx}_added.txt"
                    Path(tmpdir, added_file).write_text("\n".join(added_lines))
                    file_map[added_file] = (idx, 'added')

                if deleted_lines:
                    deleted_file = f"{idx}_deleted.txt"
                    Path(tmpdir, deleted_file).write_text("\n".join(deleted_lines))
                    file_map[deleted_file] = (idx, 'deleted')

            if not file_map:
                return {}

            output_file = os.path.join(tmpdir, 'scancode_results.json')
            subprocess.run([
                'scancode',
                '--license',
                '--strip-root',
                '--quiet',
                '--json-pp', output_file,
                tmpdir
            ], check=True)

            with open(output_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

            results = {}
            for file_result in data.get('files', []):
                if file_result['type'] != 'file':
                    continue

                filename = os.path.basename(file_result['path'])
                if filename not in file_map:
                    continue

                licenses = []
                if len(file_result.get('license_detections', [])):
                    licenses = file_result['license_detections'][0]['license_expression_spdx']

                change_idx, content_type = file_map[filename]
                results[(change_idx, content_type)] = licenses

            return results

    def is_source_file(self, file_name: str) -> bool:
        """
        Check if a file is a source file.

        Args:
            file_name (str): The file name.

        Returns:
            bool: True if the file is a source file, False otherwise.
        """
        # Define common source file extensions
        source_file_extensions = [
            '.c', '.cpp', '.h', '.hpp', '.java', '.py', '.js', '.ts', '.rb', '.go', '.swift', '.kt', '.kts', '.sh'
        ]

        # Check if the file extension is in the list of source file extensions
        for ext in source_file_extensions:
            if file_name.endswith(ext):
                return True
        return False

    def run(self) -> dict:
        """
        Run the license checker.

        Returns:
            dict: A dictionary of flagged files.
        """
        source_files = [change for change in self.patch.changes
                        if change['file_type'] == 'source']

        flagged_files = {}
        if not source_files:
            return flagged_files

        license_results = self.detect_licenses_batch(source_files)

        for idx, change in enumerate(source_files):
            added_licenses = license_results.get((idx, 'added'), [])
            deleted_licenses = license_results.get((idx, 'deleted'), [])

            issues = []
            if change['change_type'] == 'MODIFIED' or change['change_type'] == 'ADDED':
                if added_licenses and deleted_licenses and set(added_licenses) != set(deleted_licenses):
                    issues.append(f"License deleted: {deleted_licenses} and license added: {added_licenses}")
                if added_licenses and not self.is_license_permissive(added_licenses):
                    issues.append(f"Incompatible license added: {added_licenses}")
                if deleted_licenses and not added_licenses:
                    issues.append(f"License deleted: {deleted_licenses}")
                if issues:
                    flagged_files[change['path_name']] = issues
                else:
                    pass
            if change['change_type'] == 'ADDED':
                if not added_licenses and self.is_source_file(change['path_name']):
                    issues.append(f"No license added for source file: {change['path_name']}")
                    if issues:
                        flagged_files[change['path_name']] = issues
        return flagged_files
