# copyright-license-checker-action

## About

This action is a GitHub Action that checks for copyright and license issues in a repository. It uses the scancode library to detect licenses and the patch library to parse patch files.

## Usage

To use this action, you need to create a GitHub Action workflow file in your repository. Here's an example of how to use this action:

```yml
name: Run Copyright and license check
 
on:
  pull_request_target:
    types: [opened, synchronize, reopened]
 
jobs:
  copyright-license-check:
    runs-on: ubuntu-latest
 
    steps:
      - name: Checkout PR code
        uses: actions/checkout@v4
        with:
          fetch-depth: 0  # Needed to access base commit in all cases
 
      - name: Fetch base branch
        run: |
          git fetch origin ${{ github.event.pull_request.base.ref }}
 
      - name: Generate patch
        run: |
          git diff origin/${{ github.event.pull_request.base.ref }}...HEAD > pr.patch
          echo "Patch generated:"
          head -n 20 pr.patch
      
      - name: Get repository name
        run: echo "REPO_NAME=${{ github.repository }}" >> $GITHUB_ENV
 
      - name: Run copyright/license detector
        uses: targoy-qti/copyright-license-checker-action@main
        with:
          patch_file: pr.patch
          repo_name: ${{ env.REPO_NAME }}

```

## Copyright and License

```text
Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
SPDX-License-Identifier: BSD-3-Clause
```


