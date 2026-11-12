# Changelog

Notable changes to Artisan Hub. Versions follow the tags of this repository.

## Unreleased: first public release

- First public release of Artisan Hub under the GNU AGPL-3.0.
- Configuration reference in `docs/configuration.md`. Every setting defaults to a neutral value, so a new hub
  connects to nothing it was not told about.
- `compose.example.yml` for self-hosting with Docker Compose.
- First-run setup at `/setup` and through the setup API (`docs/setup-api.md`).
- The image is published to GitHub Container Registry for amd64 and arm64 by the maintainer.
- A new look shared by every ArtisanHub app (the `artisanhub-ui` package): glass over a dark page, Manrope and Sora,
  one sidebar grouped as Print, Library, Shop and Admin. The Watchtower, OpenPrintHQ and Classic skins and the light
  and dark modes are replaced by named themes, with Glass dark the only one for now, and a "Reduce transparency"
  choice. Saved skins move to Glass dark.
- People brings together Team and the owner's People and teams; Devices brings together the browser add-on,
  Connect a TV and the phone app; printer profiles are a tab of Slicing. The old addresses redirect.
- The sidebar is served as data at `/api/nav/v1` (`docs/nav-api.md`), so ArtisanHub Farm draws the same one.
