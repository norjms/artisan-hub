# Security policy

## Reporting a vulnerability

Please do not report security problems in public issues, pull requests or discussions.

Report them privately through GitHub: open the repository's **Security** tab and choose **Report a
vulnerability**. This uses GitHub's private vulnerability reporting, so only the maintainer sees the report.

Please include what you found, how to reproduce it, the version or commit you tested, and the impact you
expect. You will get an answer as soon as possible. Once a fix is released, the advisory is published and you
are credited if you wish.

## Supported versions

Security fixes go into the latest release and the `main` branch.

## Scope

In scope: the code in this repository, the published container image and the default configuration in
`compose.example.yml`. Problems in the services the hub connects to (Spoolman, Manyfold, InvenTree,
WooCommerce, print farms, sign-in providers) belong with those projects.
