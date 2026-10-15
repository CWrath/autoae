#!/bin/bash
set -euo pipefail

VERSION=9.0.2
SHA256=8c3850283eb25fa026482078a04051e0be17347b09ef81a0849bec15a96e002e
SOURCE_URL="https://ffmpeg.org/releases/ffmpeg-${VERSION}.tar.xz"
BUILD_ROOT="${RUNNER_TEMP:-$(mktemp -d)}/autoae-ffmpeg-${VERSION}"
ARCHIVE="$BUILD_ROOT/ffmpeg-${VERSION}.tar.xz"
PREFIX="$BUILD_ROOT/install"

mkdir -p "$BUILD_ROOT" "$PREFIX"
curl --fail --location --silent --show-error "$SOURCE_URL" --output "$ARCHIVE"
echo "$SHA256  $ARCHIVE" | shasum -a 256 --check
tar -xJf "$ARCHIVE" -C "$BUILD_ROOT"
cd "$BUILD_ROOT/ffmpeg-${VERSION}"

export MACOSX_DEPLOYMENT_TARGET=11.0
./configure \
  --prefix="$PREFIX" \
  --disable-autodetect \
  --disable-debug \
  --disable-doc \
  --disable-ffplay \
  --disable-gpl \
  --disable-nonfree \
  --disable-shared \
  --enable-static
make -j"$(sysctl -n hw.ncpu)" ffmpeg ffprobe
make install

OUTPUT="${1:?Usage: build_ffmpeg.sh OUTPUT_DIRECTORY}"
mkdir -p "$OUTPUT/bin"
cp "$PREFIX/bin/ffmpeg" "$PREFIX/bin/ffprobe" "$OUTPUT/bin/"
cp "$ARCHIVE" "$OUTPUT/"
cp COPYING.LGPLv2.1 "$OUTPUT/"
