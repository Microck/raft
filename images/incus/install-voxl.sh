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
source_dir="$build_dir/source"
mkdir -p "$source_dir"
install -m 755 "$0" "$source_dir/install-voxl.sh"
curl -fsSL -A 'OpenAI File Downloader, XaiImageApiFetch/1.0' "https://static.crates.io/crates/voxl-server/voxl-server-${version}.crate" -o "$build_dir/voxl.crate"
printf '%s  %s\n' "$checksum" "$build_dir/voxl.crate" | sha256sum -c -
tar -xzf "$build_dir/voxl.crate" -C "$source_dir"

# Debian 13 ships libyuv's headers and shared library but no pkg-config file.
# Describe the installed package for upstream's build.rs without patching it or
# leaving extra system-wide metadata in the image.
mkdir "$source_dir/pkgconfig"
multiarch=$(dpkg-architecture -qDEB_HOST_MULTIARCH)
yuv_version=$(dpkg-query -W -f='${Version}' libyuv-dev)
cat > "$source_dir/pkgconfig/libyuv.pc" <<PC
prefix=/usr
libdir=/usr/lib/${multiarch}
includedir=/usr/include

Name: libyuv
Description: Debian libyuv color conversion library
Version: ${yuv_version}
Libs: -L\${libdir} -lyuv
Cflags: -I\${includedir}
PC
export PKG_CONFIG_PATH="$source_dir/pkgconfig"
# Keep build artifacts private; retain the sources needed to rebuild the binary.
export CARGO_HOME="$build_dir/cargo"
export CARGO_TARGET_DIR="$build_dir/target"
export CARGO_BUILD_JOBS=2
cd "$source_dir/voxl-server-${version}"
mkdir .cargo
cargo vendor --locked --versioned-dirs ../vendor > .cargo/config.toml
rm -rf "$CARGO_HOME/registry"
cargo build --release --locked --offline --bin voxl
install -m 755 -s "$CARGO_TARGET_DIR/release/voxl" /usr/local/bin/voxl
install -d /opt/raft
install -m 644 LICENSE /opt/raft/voxl-LICENSE
printf 'voxl-server\t%s\t%s\n' "$version" "$checksum" > /opt/raft/voxl.tsv
install -m 644 /usr/share/common-licenses/GPL-2 /opt/raft/voxl-GPL-2
cat > /opt/raft/voxl-NOTICE <<NOTICE
Voxl ${version} source code is MIT licensed; its executable links x264 and is
distributed under GNU GPL version 2. There is no warranty.
See /opt/raft/voxl-GPL-2 and /opt/raft/voxl-LICENSE for the license texts.
Corresponding source is included in /opt/raft/voxl-source.tar.gz and is also
published as voxl-source-ARCH.tar.gz alongside each Raft native image release.
NOTICE

# Download the exact Debian source packages for the libraries linked by Voxl.
# Source-only indexes live in the build directory, not the guest's APT config.
cat > "$build_dir/deb-src.list" <<'SOURCES'
deb-src http://deb.debian.org/debian trixie main
deb-src http://deb.debian.org/debian trixie-updates main
deb-src http://deb.debian.org/debian-security trixie-security main
SOURCES
source_options=(-o "Dir::Etc::sourcelist=$build_dir/deb-src.list" -o Dir::Etc::sourceparts=-)
apt-get "${source_options[@]}" update -qq
dpkg-query -W -f='${source:Package}=${source:Version}\n' \
  libx264-dev libyuv-dev libopus-dev libpulse-dev libxcb1-dev > "$source_dir/native-packages.txt"
mkdir "$source_dir/native"
cd "$source_dir/native"
mapfile -t native_packages < "$source_dir/native-packages.txt"
apt-get "${source_options[@]}" source --download-only --only-source "${native_packages[@]}"
{
  dpkg --print-architecture
  rustc --version
  cargo --version
  esbuild --version
  dpkg-query -W
} > "$source_dir/build-environment.txt"
cat > "$source_dir/README.txt" <<'README'
Voxl corresponding source

voxl-server-0.3.2/ contains the published source, Cargo.lock, and a Cargo source
replacement config. vendor/ contains its locked Rust dependency sources and
licenses. native/ contains the exact Debian source packages (including patches)
for x264, libyuv, Opus, PulseAudio, and libxcb; see native-packages.txt.
install-voxl.sh is the image's build/install recipe. build-environment.txt records
the architecture, compiler/bundler versions, and all installed Debian package
versions. pkgconfig/libyuv.pc describes the installed Debian libyuv library,
which ships no pkg-config metadata.

To rebuild on the matching native Debian 13 architecture, install the build
dependencies listed in install-voxl.sh at the versions in build-environment.txt.
Then, from this extracted source directory:

  export PKG_CONFIG_PATH="$PWD/pkgconfig"
  cd voxl-server-0.3.2
  cargo build --release --locked --offline --bin voxl

Rust and esbuild are build tools; no credentials or online Cargo registry are
needed for that build. Native library sources can be unpacked with dpkg-source -x
and built using their Debian build instructions. See voxl-server-0.3.2/LICENSE,
the licenses in vendor/, and each native package's copyright/license files.
The x264-linked executable is distributed under GPL-2.0; the image includes
/opt/raft/voxl-GPL-2 and /opt/raft/voxl-NOTICE.
README
tar -czf /opt/raft/voxl-source.tar.gz -C "$source_dir" .
