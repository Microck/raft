#!/bin/bash
# Run as root inside a native Debian 13 guest. No host credentials are needed.
set -euo pipefail
case "$(dpkg --print-architecture)" in
  arm64|amd64) ;;
  *) echo 'Raft images support ARM64 and AMD64' >&2; exit 1 ;;
esac
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y --no-install-recommends ca-certificates curl tar build-essential rustc cargo pkg-config clang libclang-dev libxcb1-dev libx264-dev libyuv-dev libopus-dev libpulse-dev esbuild

version=0.3.2
checksum=5a7563fe3f84d90ae29cd63772d057f7891aeb1073ccafdc1bc1c393791c5893
build_dir=$(mktemp -d /tmp/raft-voxl.XXXXXX)
trap 'rm -rf "$build_dir"' EXIT
curl -fsSL -A 'OpenAI File Downloader, XaiImageApiFetch/1.0' "https://static.crates.io/crates/voxl-server/voxl-server-${version}.crate" -o "$build_dir/voxl.crate"
printf '%s  %s\n' "$checksum" "$build_dir/voxl.crate" | sha256sum -c -
tar -xzf "$build_dir/voxl.crate" -C "$build_dir"

# Debian 13 ships libyuv's headers and shared library but no pkg-config file.
# Describe the installed package for upstream's build.rs without patching it or
# leaving extra system-wide metadata in the image.
mkdir "$build_dir/pkgconfig"
multiarch=$(dpkg-architecture -qDEB_HOST_MULTIARCH)
yuv_version=$(dpkg-query -W -f='${Version}' libyuv-dev)
cat > "$build_dir/pkgconfig/libyuv.pc" <<PC
prefix=/usr
libdir=/usr/lib/${multiarch}
includedir=/usr/include

Name: libyuv
Description: Debian libyuv color conversion library
Version: ${yuv_version}
Libs: -L\${libdir} -lyuv
Cflags: -I\${includedir}
PC
export PKG_CONFIG_PATH="$build_dir/pkgconfig"
# Keep downloaded crates and release artifacts out of the published image.
export CARGO_HOME="$build_dir/cargo"
export CARGO_TARGET_DIR="$build_dir/target"
export CARGO_BUILD_JOBS=2
cd "$build_dir/voxl-server-${version}"
cargo build --release --locked --bin voxl
install -m 755 -s "$CARGO_TARGET_DIR/release/voxl" /usr/local/bin/voxl
install -d /opt/raft
install -m 644 LICENSE /opt/raft/voxl-LICENSE
printf 'voxl-server\t%s\t%s\n' "$version" "$checksum" > /opt/raft/voxl.tsv
