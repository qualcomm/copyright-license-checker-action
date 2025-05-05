# copyright-license-checker-action

## About

This action is a GitHub Action that checks for copyright and license issues in a repository. It uses the scancode library to detect licenses and the patch library to parse patch files.

## Usage

To use this action, you need to create a GitHub Action workflow file in your repository. Here's an example of how to use this action:

```yml
name: Run Copyright and License Check
on:
  pull_request_target:
    types: [opened, synchronize]

jobs:
  prepare-and-call:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout repository
        uses: actions/checkout@v3
        with:
          ref: ${{ github.event.pull_request.head.ref }}
          repository: ${{ github.event.pull_request.head.repo.full_name }}

      - name: Fetch and decode PR patch
        run: |
          curl -H "Accept: application/vnd.github.v3.patch" -sL ${{ github.event.pull_request.url }} > pr.patch
          head -n 100 pr.patch
      
      - name: Run copyright/license detector
        uses: targoy-qti/copyright-license-checker-action@main
        with:
          patch_file: pr.patch
          repo_name: ${{ github.repository }}


```

## Copyright and License

```text
Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
SPDX-License-Identifier: BSD-3-Clause
```


