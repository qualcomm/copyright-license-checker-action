import subprocess
from scanner.ignore_config import IgnoreConfig

"""
Module to enumerate the tracked source files of a working tree for a full-repo
scan.

Unlike scanner.patch.Patch -- which parses a git diff into added/deleted lines
for the pull-request path -- this module lists the *whole* files that currently
exist in the repository so they can be scanned in their entirety. This is what
lets a full-repo scan catch legacy files that were committed before the checker
was enabled and therefore never appeared in any scanned PR diff.
"""

# Source file extensions the full-repo scan covers. Kept in sync with
# LicenseChecker.is_source_file so both paths agree on what "source" means.
SOURCE_FILE_EXTENSIONS = (
    '.c', '.cpp', '.h', '.hpp', '.java', '.py', '.js', '.ts',
    '.rb', '.go', '.swift', '.kt', '.kts', '.sh'
)

# Extensions excluded from the checks, mirroring scanner.patch.Patch's hardcoded
# exclusions so the full-repo and PR paths skip the same non-source files.
EXCLUDED_EXTENSIONS = ('.patch', '.bb', '.md', '.json', '.yml')


class RepoScan:
    """
    Class to represent the set of source files in a working tree to scan.

    By default this is the git-tracked source files; with include_untracked it
    also covers untracked-but-not-ignored files (see __init__).
    """

    def __init__(self, root: str = ".", include_untracked: bool = False) -> None:
        """
        Initialize the RepoScan object by listing the repository's files under
        root.

        Args:
            root (str): Path to the repository working tree. Defaults to the
                current directory (where the action checks out the repo).
            include_untracked (bool): When False (the default), only git-tracked
                files are listed (`git ls-files`). When True, untracked files
                are included as well -- still honoring .gitignore -- via
                `git ls-files --cached --others --exclude-standard`, so files
                that were added but not yet committed are also scanned. Ignored
                files (build artifacts, virtualenvs, etc.) are never listed in
                either mode.
        """
        self.root = root
        self.ignore_config = IgnoreConfig()

        # git ls-files lists tracked files, respecting .gitignore, without
        # walking untracked build artifacts. Adding --others --exclude-standard
        # additionally picks up untracked-but-not-ignored files (e.g. added but
        # not yet committed) while still skipping anything .gitignore excludes.
        ls_files_cmd = ['git', 'ls-files']
        if include_untracked:
            ls_files_cmd += ['--cached', '--others', '--exclude-standard']
        result = subprocess.run(
            ls_files_cmd,
            cwd=self.root,
            check=True,
            capture_output=True,
            text=True,
        )
        listed_files = [line for line in result.stdout.splitlines() if line]

        # Build the list of files the full-repo scan should cover.
        self.files = []
        for path_name in listed_files:
            # Skip files that match hardcoded exclusions or config-based exclusions
            if path_name.endswith(EXCLUDED_EXTENSIONS):
                continue

            if self.ignore_config.is_excluded(path_name):
                continue

            if not path_name.endswith(SOURCE_FILE_EXTENSIONS):
                continue

            self.files.append(path_name)

    def get_files(self) -> list:
        """
        Get the list of tracked source file paths to scan.

        Returns:
            list: A list of repository-relative file paths.
        """
        return self.files
