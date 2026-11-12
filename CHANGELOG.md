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
  one sidebar grouped as Print, Library, Shop and Admin. The Legacy farm, OpenPrintHQ and Classic skins and the light
  and dark modes are replaced by named themes, with Glass dark the only one for now, and a "Reduce transparency"
  choice. Saved skins move to Glass dark.
- People brings together Team and the owner's People and teams; Devices brings together the browser add-on,
  Connect a TV and the phone app; printer profiles are a tab of Slicing. The old addresses redirect.
- The sidebar is served as data at `/api/nav/v1` (`docs/nav-api.md`), so ArtisanHub Farm draws the same one.
- The sidebar has a Farm group after Print with the pages only the farm has (Files, Slicer, Projects, Statistics,
  Print reports, Timelapses, Farm settings), shown when the hub links to an installed ArtisanHub Farm.
- Forgot your password: with logins in LLDAP and email set up, `/account/password` emails a one-time link (30
  minutes, works once, a new one cancels older ones) to choose a new password. The answer is the same whether or not
  the login exists, and requests are limited per client and per login. The hub's owner can make such a link for
  anyone from People.
- OrcaSlicer in the browser: on an installation that runs it behind Authelia (`HUB_ORCASLICER_GROUP`), the hub's
  owner turns it on or off per person on People (Whole hub). The person joins or leaves that LLDAP group; hub
  owners can always open it.
- Send to hub from OrcaSlicer in the browser (`HUB_ORCASLICER_OUTBOX`): files saved to "Send to hub" in the desktop
  become draft models in the library, credited to the desktop or to the person whose desktop sent them. Files the
  hub does not keep move to "Not sent" beside them with a note saying why.
- OrcaSlicer in the browser, a desktop for each person: when the installation gives everyone their own
  (`HUB_ORCASLICER_DESKTOPS=per-person`), People (Whole hub) lists the desktops' files under "Desktop files", with
  who has one open now and who has left the hub, and the owner removes a desktop's files there after confirming.
