#!/bin/bash
# Builds libjxl's cjxl encoder for Aptus into mac/vendor/jxl/.
# libjxl is BSD-3-Clause, so it's fine in a paid, closed-source app (the Python JPEG XL plugin is GPL, so
# Aptus calls this tool instead). Static build: no Homebrew or system image libraries are linked, so the
# app runs on any Mac. Run on the Mac (or GitHub Mac runner) that matches the app you're building.
set -euo pipefail

VERSION="${JXL_VERSION:-0.11.1}"
PNG_VERSION="${PNG_VERSION:-1.6.47}"
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="$HERE/vendor/jxl"
WORK="${TMPDIR:-/tmp}/aptus-jxl-$VERSION"
JOBS="$( (sysctl -n hw.ncpu || nproc) 2>/dev/null )"

if [ -x "$OUT/cjxl" ] && "$OUT/cjxl" --version 2>&1 | grep -q "v$VERSION"; then
  echo "cjxl $VERSION is already built in $OUT"
  exit 0
fi

if ! command -v cmake >/dev/null; then
  if command -v brew >/dev/null; then brew install cmake; else echo "Install cmake first: brew install cmake" >&2; exit 1; fi
fi

mkdir -p "$WORK" "$OUT"
cd "$WORK"
if [ ! -d libjxl ]; then
  git clone --depth 1 --branch "v$VERSION" --recurse-submodules --shallow-submodules \
    https://github.com/libjxl/libjxl.git
fi

EXTRA=()
if [ "$(uname)" = "Darwin" ]; then
  EXTRA+=(-DCMAKE_OSX_DEPLOYMENT_TARGET=12.0)
fi

# A current static libpng, built first. libjxl's bundled libpng is old enough to break on recent macOS SDKs
# (it looks for the classic Mac OS fp.h) and its generated header never reaches libjxl's include path.
PNG_PREFIX="$WORK/png-install"
if [ ! -f "$PNG_PREFIX/lib/libpng16.a" ]; then
  [ -d libpng ] || git clone --depth 1 --branch "v$PNG_VERSION" https://github.com/pnggroup/libpng.git
  cmake -S libpng -B png-build -G "Unix Makefiles" \
    -DCMAKE_BUILD_TYPE=Release -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
    -DCMAKE_INSTALL_PREFIX="$PNG_PREFIX" -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
    -DPNG_SHARED=OFF -DPNG_STATIC=ON -DPNG_FRAMEWORK=OFF -DPNG_TESTS=OFF -DPNG_TOOLS=OFF -DPNG_EXECUTABLES=OFF \
    "${EXTRA[@]}"
  cmake --build png-build -j"$JOBS"
  cmake --install png-build
fi

# The libpng above (PNG in, with EXIF and ICC), macOS's own zlib, skcms for color, highway and brotli from
# submodules, and libjxl's own jpegli for reading JPEGs. Everything optional that would link a system library is off.
# BUILD_SHARED_LIBS=OFF links libjxl and its parts statically; JPEGXL_STATIC isn't used because it asks for a
# fully static binary, which macOS doesn't allow (cjxl links only the system's libSystem and libc++).
# CMAKE_POLICY_VERSION_MINIMUM lets CMake 4 configure the bundled libpng, which still declares CMake 3.1.
cmake -S libjxl -B build -G "Unix Makefiles" \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_POLICY_VERSION_MINIMUM=3.5 \
  -DBUILD_SHARED_LIBS=OFF \
  -DBUILD_TESTING=OFF \
  -DJPEGXL_ENABLE_TOOLS=ON \
  -DJPEGXL_ENABLE_JPEGLI=ON \
  -DJPEGXL_ENABLE_DEVTOOLS=OFF \
  -DJPEGXL_ENABLE_BENCHMARK=OFF \
  -DJPEGXL_ENABLE_EXAMPLES=OFF \
  -DJPEGXL_ENABLE_MANPAGES=OFF \
  -DJPEGXL_ENABLE_DOXYGEN=OFF \
  -DJPEGXL_ENABLE_SJPEG=OFF \
  -DJPEGXL_ENABLE_OPENEXR=OFF \
  -DJPEGXL_ENABLE_JNI=OFF \
  -DJPEGXL_ENABLE_PLUGINS=OFF \
  -DJPEGXL_ENABLE_VIEWERS=OFF \
  -DJPEGXL_ENABLE_SKCMS=ON \
  -DJPEGXL_BUNDLE_LIBPNG=OFF \
  -DPNG_LIBRARY="$PNG_PREFIX/lib/libpng16.a" \
  -DPNG_PNG_INCLUDE_DIR="$PNG_PREFIX/include" \
  -DCMAKE_DISABLE_FIND_PACKAGE_GIF=ON \
  -DCMAKE_DISABLE_FIND_PACKAGE_JPEG=ON \
  "${EXTRA[@]}"
cmake --build build --target cjxl -j"$JOBS"

cp build/tools/cjxl "$OUT/"
cp libjxl/LICENSE "$OUT/LICENSE-libjxl.txt"
strip "$OUT/cjxl" || true
if [ "$(uname)" = "Darwin" ]; then
  otool -L "$OUT/cjxl" | tail -n +2
  if otool -L "$OUT/cjxl" | tail -n +2 | grep -Eq "/opt/homebrew|/usr/local"; then
    echo "cjxl links a Homebrew library, so the app would break on other Macs. Stopping." >&2
    exit 1
  fi
else
  ldd "$OUT/cjxl" || true
fi
"$OUT/cjxl" --version
echo "Built cjxl $VERSION into $OUT"
