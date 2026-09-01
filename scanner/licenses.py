"""Shared license lists and SPDX-expression classification helpers."""

PERMISSIVE_LICENSES = [
    "BSD-3-Clause",
    "MIT",
    "Apache-1.0",
    "Apache-1.1",
    "Apache-2.0",
    "BSD-3-Clause-Clear",
    "FreeBSD-DOC",
    "Zlib",
    "BSD-1-Clause",
    "BSD-2-Clause",
    "BSD-2-Clause-first-lines",
    "BSD-2-Clause-Views",
    "BSD-3-Clause-Sun",
    "BSD-4-Clause-Shortened",
    "BSD-3-Clause-Attribution",
    "BSD-4-Clause",
    "ISC",
    "CC0-1.0",
    "ICU",
    "LicenseRef-scancode-unicode",
    "Apache-2.0 WITH LLVM-exception",
    "Apache-2.0 WITH LLVM-exception AND Apache-2.0 AND LLVM-exception",
]

COPYLEFT_LICENSES = [
    "GPL-1.0-only",
    "GPL-1.0-or-later",
    "GPL-2.0-only",
    "GPL-2.0-or-later",
    "GPL-3.0-only",
    "GPL-3.0",
    "GPL-3.0-or-later",
    "AGPL-3.0",
    "LGPL-3.0",
    "GPL-2.0",
    "GPL-2.0+",
    "GPL-2.0-only WITH Linux-syscall-note",
    "AGPL-1.0-only",
    "AGPL-1.0-or-later",
    "LicenseRef-scancode-agpl-2.0",
    "AGPL-3.0-only",
    "AGPL-3.0-or-later",
]

PROPRIETARY_LICENSE = "LicenseRef-scancode-proprietary-license"


def split_license_components(expression: str) -> list:
    """Split an SPDX expression into individual license identifiers."""
    if not expression:
        return []

    components = []
    for part in expression.replace("(", "").replace(")", "").split(" AND "):
        for license_id in part.split(" OR "):
            license_id = license_id.strip()
            if license_id:
                components.append(license_id)
    return components


def is_uncertain_expression(expression: str) -> bool:
    """Return whether every component is an unrecognized ScanCode reference."""
    licenses = split_license_components(expression)
    if not licenses:
        return False
    if len(licenses) == 1 and licenses[0] == PROPRIETARY_LICENSE:
        return False
    return all(
        license_id.startswith("LicenseRef-scancode-") and license_id not in PERMISSIVE_LICENSES
        for license_id in licenses
    )


def is_copyleft(license_id: str) -> bool:
    """Return whether a license identifier is in the canonical copyleft list."""
    return license_id in COPYLEFT_LICENSES


# TODO: SPDX expression evaluation exceeds the configured complexity limits.
# The leading-OR special case is existing behavior and is fixed separately.
def is_license_allowed(expression: str, allowed_licenses: list) -> bool:  # noqa: C901
    """Return whether an SPDX expression is compatible with an allowed list."""
    # pylint: disable=too-many-branches,too-many-nested-blocks
    expression = expression.strip()

    if expression.startswith("(") and " OR " in expression.split(")")[0]:
        or_part = expression.split(")")[0] + ")"
        or_licenses = [license_id.strip() for license_id in or_part.strip("()").split(" OR ")]
        return any(license_id in allowed_licenses for license_id in or_licenses)

    and_groups = [group.strip() for group in expression.split(" AND ")]
    for and_group in and_groups:
        if " OR " in and_group:
            or_licenses = [license_id.strip() for license_id in and_group.strip("()").split(" OR ")]
            if not any(license_id in allowed_licenses for license_id in or_licenses):
                return False
            continue

        license_id = and_group.strip("()")
        if license_id in allowed_licenses:
            continue

        compatible = False
        for allowed_license in allowed_licenses:
            if "-or-later" in allowed_license:
                base_license = allowed_license.replace("-or-later", "")
                if license_id in (
                    allowed_license,
                    f"{base_license}-only",
                    base_license,
                ):
                    compatible = True
                    break
        if not compatible:
            return False

    return True
