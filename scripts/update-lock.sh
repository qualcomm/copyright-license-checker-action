#!/usr/bin/env bash

#
# Copyright Qualcomm Technologies, Inc.
# SPDX-License-Identifier: BSD-3-Clause
#

set -euo pipefail

readonly REQUIRED_UV_VERSION="0.11.16"
readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly REPOSITORY_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

if ! command -v uv >/dev/null 2>&1; then
    printf 'uv %s is required but was not found in PATH.\n' "${REQUIRED_UV_VERSION}" >&2
    exit 1
fi

installed_uv_version="$(uv --version | awk '{print $2}')"
if [[ "${installed_uv_version}" != "${REQUIRED_UV_VERSION}" ]]; then
    printf 'uv %s is required; found uv %s.\n' "${REQUIRED_UV_VERSION}" "${installed_uv_version}" >&2
    exit 1
fi

cd "${REPOSITORY_ROOT}"

uv pip compile \
    --quiet \
    --system-certs \
    --generate-hashes \
    --python 3.12 \
    --python-platform x86_64-unknown-linux-gnu \
    --custom-compile-command './scripts/update-lock.sh' \
    --output-file requirements.lock \
    "$@" \
    requirements.txt
