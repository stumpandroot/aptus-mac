#!/bin/bash
# Builds LGPL-only ffmpeg and ffprobe for Aptus into mac/vendor/ffmpeg/.
# Aptus only reads video frames and writes PNG frames, so this is a small build with
# FFmpeg's own decoders. No GPL parts, no network code, no external codec libraries.
# Run on the Mac (or GitHub Mac runner) that matches the app you're building.
set -euo pipefail

VERSION="${FFMPEG_VERSION:-7.1.1}"
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="$HERE/vendor/ffmpeg"
WORK="${TMPDIR:-/tmp}/aptus-ffmpeg-$VERSION"

if [ -x "$OUT/ffmpeg" ] && "$OUT/ffmpeg" -hide_banner -version | grep -q "ffmpeg version $VERSION"; then
  echo "ffmpeg $VERSION is already built in $OUT"
  exit 0
fi

mkdir -p "$WORK" "$OUT"
cd "$WORK"
if [ ! -d "ffmpeg-$VERSION" ]; then
  curl -fsSL "https://ffmpeg.org/releases/ffmpeg-$VERSION.tar.xz" -o ffmpeg.tar.xz
  tar xf ffmpeg.tar.xz
fi
cd "ffmpeg-$VERSION"

# --disable-autodetect stops ffmpeg from quietly linking whatever happens to be installed
# (Homebrew libraries would make the app fail on other Macs). zlib is turned back on because
# the PNG encoder needs it; macOS ships zlib, so nothing extra is bundled.
./configure \
  --prefix="$WORK/install" \
  --disable-gpl --disable-nonfree \
  --disable-autodetect --enable-zlib \
  --disable-network --disable-doc --disable-debug \
  --disable-ffplay --disable-avdevice \
  --enable-static --disable-shared \
  --extra-cflags="-mmacosx-version-min=12.0" --extra-ldflags="-mmacosx-version-min=12.0"

make -j"$(sysctl -n hw.ncpu)"
cp ffmpeg ffprobe "$OUT/"
cp COPYING.LGPLv2.1 "$OUT/LICENSE-ffmpeg.txt"
strip "$OUT/ffmpeg" "$OUT/ffprobe" || true

if "$OUT/ffmpeg" -hide_banner -version | grep -q -- "--enable-gpl"; then
  echo "This ffmpeg build includes GPL parts. Stopping." >&2
  exit 1
fi
otool -L "$OUT/ffmpeg" | tail -n +2
echo "Built LGPL ffmpeg $VERSION into $OUT"
