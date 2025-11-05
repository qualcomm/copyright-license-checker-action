"""
Module to load optional .licenseignore configuration.
"""
import pathspec
from pathlib import Path

class IgnoreConfig:
    """
    Handles loading and matching of .licenseignore patterns.
    """

    def __init__(self, ignore_path: str = ".licenseignore"):
        """
        Load patterns from ignore file.

        Args:
            ignore_path (str): Path to the ignore file. Defaults to '.licenseignore'
        """
        path = Path(ignore_path)

        if path.exists():
            lines = path.read_text(encoding='utf-8').splitlines()
            self.patterns = [line.strip() for line in lines
                             if line.strip() and not line.strip().startswith('#')]
            self.spec = pathspec.PathSpec.from_lines('gitwildmatch', self.patterns)
        else:
            self.patterns = []
            self.spec = None

    def is_excluded(self, file_path: str) -> bool:
        """
        Check if a file path should be excluded.

        Args:
            file_path (str): The file path to check

        Returns:
            bool: True if the file should be excluded, False otherwise
        """
        if not self.spec:
            return False
        return self.spec.match_file(file_path)