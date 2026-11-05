# Publishing and distribution notes

## Packages
`python devtools/package.py` writes into `dist/`:
- `quadforge-vivaldi-<version>-all-platforms.zip` — every engine inside; "Install from Disk" on any computer.
- `quadforge-vivaldi-<version>-<platform>.zip` — one platform each (`windows-x64`, `linux-x64`, `macos-arm64`, `macos-x64`) with a matching `platforms` line in the manifest. This is the shape the Blender Extensions platform expects for extensions that bundle executables.

## Blender Extensions (extensions.blender.org): an open question
The platform's Terms of Service (checked November 2026) say that the source code of an extension "must be fully written in
Python" (3.6), that "depending on external, pre-compiled packages is not allowed" (3.6), and that binary files included in
an extension "must be licensed as Public Domain (CC0)" (1.2). FORGE is a compiled C++ program bundled inside the add-on
and licensed GPL, so a literal reading says the add-on **may be rejected**. An earlier version of this note said that
bundled executables are allowed when the manifest lists `platforms`; that was an assumption and it is not supported by
the Terms of Service (the `platforms` field exists mainly for Python wheels).
The sensible path is to ask the extension moderators before uploading (Blender Chat channel #extension-moderators, or the
Extensions Platform thread on devtalk.blender.org) and to keep GitHub releases and Superhive as the main channels.
Other rules that apply either way: no obfuscated code, no auto-updater, no dependence on external servers, the engine
source is included in `engine_src/` (GPL), and the manifest permissions must be correct (`files`, because the engine runs
from temporary files). Remove `__pycache__` before uploading (the packager does) and validate with
`blender --command extension validate <zip>`.

## Superhive (Blender Market)
- GPL add-ons may be sold; the buyer may redistribute and modify; the source must be available. Income comes from support, maintenance and keeping up with new Blender versions.

## macOS: signing is postponed
Status today: the Apple-silicon binary carries the ad-hoc signature that Apple silicon requires; the Intel binary is unsigned. Neither is signed with a Developer ID or notarized, so macOS may show a warning the first time. The add-on tries to clear the quarantine flag itself (`xattr -d com.apple.quarantine`). Users can also run that command by hand or allow the file in System Settings > Privacy & Security.
Future work (needs an Apple Developer account): sign with a Developer ID certificate, enable the hardened runtime, notarize, and staple the ticket, ideally in a CI job.

## Continuous integration (future)
Build the engine natively on GitHub Actions runners for Windows, Linux and macOS, run `devtools/engine_tests` and the Blender tests with the `bpy` module, and attach the per-platform zips to a release.

## Status
- Release 0.10.31. All four per-platform zips and the all-platforms zip pass the Blender validator (`python <blender>/scripts/addons_core/bl_pkg/cli/blender_ext.py validate <zip>`, or `blender --command extension validate <zip>`). The manifest tagline must be 64 characters or fewer.
- Public name: GregCarreno3D (the legal name appears only in the manifest).
- Submission order: GitHub (source and releases) is done; ask the Blender Extensions moderators whether a bundled C++ engine is acceptable; Superhive.
