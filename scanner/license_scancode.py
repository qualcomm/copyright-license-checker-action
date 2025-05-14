import json
import tempfile
import subprocess
import warnings
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

    def detect_license_with_scancode(self, content: str) -> list:
        """
        Detect licenses using scancode.

        Args:
            content (str): The content to check.

        Returns:
            list: A list of licenses.
        """
        with tempfile.NamedTemporaryFile(mode='w+', suffix='.txt', delete=False) as tmp_file:
            tmp_file.write(content)
            tmp_path = tmp_file.name

        output_file = tmp_path + '_scancode.json'
        subprocess.run([
            'scancode',
            '--license',
            '--strip-root',
            '--quiet',
            '--json-pp', output_file,
            tmp_path
        ], check=True)

        with open(output_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
            licenses = []
            for result in data.get('files', []):
                if len(result['license_detections']):
                    licenses = result['license_detections'][0]['license_expression_spdx']
                return licenses

    def detect_license(self, content: str) -> tuple:
        """
        Detect licenses in the content.

        Args:
            content (str): The content to check.

        Returns:
            tuple: A tuple of added and deleted licenses.
        """
        added_lines = []
        deleted_lines = []

        # Check if content is None
        if content is None:
            return [], []

        # Separate added and deleted lines
        for line in content.split('\n'):
            if line.startswith('+'):
                added_lines.append(line[1:])
            elif line.startswith('-'):
                deleted_lines.append(line[1:])

        # Join added and deleted lines as-is
        added_text = "\n".join(added_lines)
        deleted_text = "\n".join(deleted_lines)

        added_licenses = []
        deleted_licenses = []

        # Check for licenses in added lines
        added_licenses = self.detect_license_with_scancode(added_text)

        # Check for licenses in deleted lines
        deleted_licenses = self.detect_license_with_scancode(deleted_text)

        return added_licenses, deleted_licenses

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

        for change in source_files:
            added_licenses, deleted_licenses = self.detect_license(change['content'])
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
