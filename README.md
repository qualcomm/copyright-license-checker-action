# copyright-license-checker-action

## About

This action is a GitHub Action that checks for copyright and license issues in a repository. It uses the scancode library to detect licenses and the patch library to parse patch files.

## Usage

To use this action, you need to create a GitHub Action workflow file in your repository. Here's an example of how to use this action:

```yml
name: Copyright and License Checker

on:
  push:
    branches:
      - main

jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout code
        uses: actions/checkout@v2
      - name: Run copyright and license checker
        uses: ./.github/actions/copyright-license-checker-action
        with:
          repo: 'your-repo-name'
          patch: '${{ github.event.push.patch }}'


```

## Copyright and License

```text
Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
SPDX-License-Identifier: BSD-3-Clause
```


