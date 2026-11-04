# Publishing and distribution notes

## Packages
`python devtools/package.py` writes into `dist/`:
- `quadforge-vivaldi-<version>-all-platforms.zip` — every engine inside; "Install from Disk" on any computer.
- `quadforge-vivaldi-<version>-<platform>.zip` — one platform each (`windows-x64`, `linux-x64`, `macos-arm64`, `macos-x64`) with a matching `platforms` line in the manifest. This is the shape the Blender Extensions platform expects for extensions that bundle executables.

## Blender Extensions (extensions.blender.org)
- Bundled executables are allowed when the manifest lists `platforms`. Obfuscated code and extensions that need external downloads are not allowed. The engine source is included in `engine_src/` (GPL).
- Remove `__pycache__` before uploading (the packager does). Validate with `blender --command extension build` (needs a full Blender, not the `bpy` module) before submitting.

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
- Submission order: GitHub (source and releases), Blender Extensions (four per-platform zips + listing text), then Superhive.
