# Changelog

Notable changes to Artisan Hub. Versions follow the tags of this repository.

## Unreleased: first public release

- First public release of Artisan Hub under the GNU AGPL-3.0.
- Configuration reference in `docs/configuration.md`. Every setting defaults to a neutral value, so a new hub
  connects to nothing it was not told about.
- `compose.example.yml` for self-hosting with Docker Compose.
- First-run setup at `/setup` and through the setup API (`docs/setup-api.md`).
- The image is published to GitHub Container Registry by CI on GitHub-hosted runners.
