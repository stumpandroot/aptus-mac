# Aptus: Resize & Frame Grab

Aptus fits photos and video frames to wherever they're going: website heroes, Instagram, LinkedIn, YouTube thumbnails, and your own sizes. This folder holds the engine, the interface, and everything needed to build the signed Mac app.

## What's here

| Path | What it is |
| --- | --- |
| `presets.json` | The built-in destinations and upload limits. Edit sizes here; no code changes needed. |
| `aptus_engine/` | The engine: presets (`presets.py`), crops and compression (`images.py`), file names (`naming.py`), video frames (`video.py`) |
| `app.py` | The local engine server the interface talks to |
| `static/index.html` | The interface. The same file runs in the Mac app and as the browser preview. |
| `greenlaunch_license.py` | The GreenLaunch license check (a copy of the one in the license service; keep them in sync) |
| `mac/` | The Mac app window (`main.py`), app recipe (`Aptus.spec`), ffmpeg build, and signing script |
| `.github/workflows/build.yml` | Builds, tests, signs and notarizes the app on GitHub's Macs |
| `DESIGN.md` | How Aptus applies the family design system: accent, components, Apti, icon |
| `tests/` | 62 tests for the engine, the server, and the new features |
| `Aptus.command` | Double-click to run from source in your browser |

## Run it from source

You need a Mac with Python 3.12 and ffmpeg (`brew install ffmpeg`).

- **In your browser:** right-click `Aptus.command` and choose **Open**. The first run takes about a minute to set up, then opens `http://127.0.0.1:8765`.
- **In the app window:** `pip3 install -r requirements.txt -r mac/requirements-mac.txt`, then `python3 mac/main.py`.

Running from source counts as Pro, so every feature is on for testing. Set `APTUS_PRO=0` to see the Free version.

## What it does

- **Batches:** drop several photos, a whole folder, or videos. Click a thumbnail to frame that file; files you don't touch use a centered crop. `[` and `]` step through them.
- **Crops you place:** every exact-size destination draws its own frame. Drag a frame to move it, drag a corner to crop tighter, or tap the subject to move every frame at once.
- **Size targets:** encodes at the preset quality and, if the file is over its target, finds the highest quality that fits (lowest allowed: 30). If even that's too big, it says so.
- **Responsive size set:** one WebP each at 640, 1024, 1600 and 2560 wide for a `srcset`. Widths wider than the photo are skipped, never enlarged.
- **SEO file names:** fill in location, what's in it, client, and shot type to get names like `lancaster-pa-barn-studio-smith-builders-wide-01-hero.webp`, following the Stump & Root SEO image naming guide. Aptus also suggests alt text under 125 characters.
- **Upload limits:** warns when a file is too big for Webflow's 4 MB image limit (listed under `limits` in `presets.json`).
- **Check at 100%:** opens any export at actual pixels. Switch to **Original** (or press B) to compare it with an uncompressed render of the same crop.
- **My presets:** add your own sizes on the Presets page. They're saved on your Mac.
- **Preset updates:** when Aptus opens, it downloads a newer `presets.json` if one is posted, and keeps the current list if the download fails or doesn't pass its checks.
- **Clean output:** applies camera rotation (photos and phone videos), converts color to sRGB, strips metadata such as GPS, and never overwrites a file or touches the original.
- **Video:** scrub to a moment, find the sharpest nearby frames, grab a frame every N seconds, or make a 12-frame contact sheet.
- **On a Mac:** choose the export folder with the standard folder panel, and use **Show in Finder** and **Quick Look** on any result.

Dragging an export out of the window into another app isn't supported; use **Show in Finder**.

## Free and Pro

| Free | Pro |
| --- | --- |
| One file to one destination at a time, built-in presets, single frame grabs, sharp-frame suggestions, SEO names, the 100% check | Adds batches, several destinations at once, your own presets, interval grabs and contact sheets |

The server enforces these limits, not just the interface.

## Before you ship: four things only you can do

1. **Host the preset list.** Upload `presets.json` to `https://greenlaunch.co/aptus/presets.json` (or change `PRESETS_URL` in `app.py`). When platform sizes change, edit it and raise `version` by 1; apps pick it up the next time they open.
2. **Turn on licensing.** In `greenlaunch_license.py`, replace `REPLACE_WITH_PUBLIC_KEY` with the license service's public key. Add `aptus` as an app in the license service. Until then the built app runs as Free with no way to activate.
3. **Finish the app icon.** `mac/Aptus.icns` is an interim flat icon. Build the final one in Icon Composer 2 from the layers in `mac/icon/`, as described in `DESIGN.md`.
4. **Set up signing.** In the [stumpandroot/aptus-mac repository](https://github.com/stumpandroot/aptus-mac), go to **Settings > Secrets and variables > Actions** and add the same six secrets as Vectorize: `MACOS_CERTIFICATE`, `MACOS_CERTIFICATE_PWD`, `MACOS_SIGN_IDENTITY`, `APPLE_ID`, `APPLE_APP_PASSWORD`, `APPLE_TEAM_ID`. The Vectorize README has the step-by-step for making each one.

## Build the app

Every push to `main` builds two DMGs, one for Apple silicon and one for Intel. Find them on the **Actions** tab under **Artifacts**. To release, run **Build Aptus** by hand with a version such as `1.0.0`; it makes a draft release with both DMGs.

Each build:

1. Builds an LGPL-only ffmpeg from source (`mac/build_ffmpeg.sh`), with no GPL parts and no outside codec libraries. It's cached between builds.
2. Runs the tests against that ffmpeg.
3. Builds `Aptus.app` with PyInstaller, then checks it: `--self-test` confirms every bundled part works, and `--smoke-test` exports the sample and opens and closes the window.
4. Signs, makes the DMG, and notarizes it when the secrets are set. Without them, you get an unsigned test build.

To build on your own Mac instead: `bash mac/build_ffmpeg.sh`, then `pyinstaller --noconfirm mac/Aptus.spec`, then `bash mac/sign_and_package.sh`.

## Privacy and safety

- Everything runs on the Mac. The only network calls are the preset update and license activation.
- The engine answers only its own window. It refuses other hosts and other websites, and in the app each launch gets a private token.
- Settings, your presets and the license live in `~/Library/Application Support/GreenLaunch/aptus`.

## Tests

```
python3 -m pytest -q
```
