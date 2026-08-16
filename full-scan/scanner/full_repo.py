import subprocess
from scanner.ignore_config import IgnoreConfig

"""
Module to enumerate the source files of a working tree for a full-repo scan.

Unlike scanner.patch.Patch -- which parses a git diff into added/deleted lines
for the pull-request path -- this module lists the *whole* files that currently
exist in the repository so they can be scanned in their entirety. This is what
lets a full-repo scan catch legacy files that were committed before the checker
was enabled and therefore never appeared in any scanned PR diff.

Two tiers of files are listed: fully-checked source files (SOURCE_FILE_EXTENSIONS)
and "license-optional" build-system files (LICENSE_OPTIONAL_EXTENSIONS, e.g.
.mk/.bp) that are scanned for an incompatible license but are not required to
carry a license header or copyright. See FullScanner.run for the relaxed handling.
"""

# Source file extensions the full-repo scan covers. Kept in sync with
# LicenseChecker.is_source_file so both paths agree on what "source" means.
SOURCE_FILE_EXTENSIONS = (
    '.c', '.cpp', '.cc', '.h', '.hpp', '.java', '.py', '.js', '.ts',
    '.rb', '.go', '.swift', '.kt', '.kts', '.sh', '.rs', '.S')

# Build-system files scanned under a relaxed "license-optional" tier: a MISSING
# license header or MISSING copyright is NOT flagged (these files routinely have
# neither), but a present-but-incompatible license is STILL a blocking error and
# an uncertain license still warns -- classify_license runs normally whenever a
# license is actually detected. See FullScanner.run.
LICENSE_OPTIONAL_EXTENSIONS = ('.mk', '.bp')

# Extensions excluded from the checks, mirroring scanner.patch.Patch's hardcoded
# exclusions so the full-repo and PR paths skip the same non-source files.
# NOTE: BitBake files (.bb/.bbclass/.bbappend) are excluded outright -- they are
# recipe/class metadata rather than shipped source, so the full-repo scan skips
# them entirely (it does NOT scan them for an incompatible license). The PR path
# (scanner/patch.py) likewise excludes .bb.
EXCLUDED_EXTENSIONS = ('.patch', '.md', '.json', '.yml', '.bb', '.bbclass', '.bbappend')


class RepoScan:
    """
    Class to represent the set of files in a working tree to scan: fully-checked
    source files plus license-optional build files (.mk/.bp).

    By default this is the git-tracked files; with include_untracked it also
    covers untracked-but-not-ignored files (see __init__).
    """

    def __init__(self, root: str = ".", include_untracked: bool = False,
                 include_licenseignore: bool = False) -> None:
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
            include_licenseignore (bool): When False (the default), files matched
                by the repo's .licenseignore are skipped. When True, those files
                are scanned anyway (the .licenseignore is ignored). Files skipped
                by .licenseignore are always recorded in get_ignored_files()
                regardless of this flag, so callers can report why a file was
                skipped.
        """
        self.root = root
        self.include_licenseignore = include_licenseignore
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

        # Build the list of files the full-repo scan should cover. Files matched
        # by .licenseignore are also collected separately (self.ignored) so a
        # caller can tell "skipped by .licenseignore" apart from "untracked".
        self.files = []
        self.ignored = []
        for path_name in listed_files:
            # Skip files that match hardcoded exclusions.
            if path_name.endswith(EXCLUDED_EXTENSIONS):
                continue

            # Keep only fully-checked source files AND relaxed license-optional
            # build files; drop everything else. (str.endswith accepts a tuple of
            # suffixes, so the concatenated tuple matches either tier.)
            if not path_name.endswith(SOURCE_FILE_EXTENSIONS + LICENSE_OPTIONAL_EXTENSIONS):
                continue

            # .licenseignore exclusion: record it either way, and skip it unless
            # the caller opted in with include_licenseignore.
            if self.ignore_config.is_excluded(path_name):
                self.ignored.append(path_name)
                if not self.include_licenseignore:
                    continue

            self.files.append(path_name)

    def get_files(self) -> list:
        """
        Get the list of file paths to scan (source files and license-optional
        build files).

        Returns:
            list: A list of repository-relative file paths.
        """
        return self.files

    def get_ignored_files(self) -> list:
        """
        Get the source/license-optional files that .licenseignore excluded.

        These are always recorded, even when include_licenseignore is True (in
        which case they are also scanned). Lets callers distinguish a file
        skipped by .licenseignore from one that is simply untracked.

        Returns:
            list: A list of repository-relative file paths.
        """
        return self.ignored

    def is_license_optional(self, path_name: str) -> bool:
        """
        Report whether a path is a "license-optional" build file (.mk/.bp).

        These files are scanned, but a missing license header or missing
        copyright is not flagged; only a present-but-incompatible (or uncertain)
        license is reported. See FullScanner.run for the relaxed handling.

        Args:
            path_name (str): A repository-relative file path.

        Returns:
            bool: True if the path is in the license-optional tier.
        """
        return path_name.endswith(LICENSE_OPTIONAL_EXTENSIONS)
