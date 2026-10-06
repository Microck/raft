set -eu
architecture=$(dpkg --print-architecture)
case "$architecture" in
  arm64|amd64) ;;
  *) echo 'Raft supports native ARM64 and AMD64 hosts' >&2; exit 1 ;;
esac
if ! command -v incus >/dev/null; then
  sudo mkdir -p /etc/apt/keyrings
  curl -fsSL -A 'OpenAI File Downloader, XaiImageApiFetch/1.0' https://pkgs.zabbly.com/key.asc -o /tmp/raft-zabbly.asc
  fingerprint=$(gpg --show-keys --with-colons /tmp/raft-zabbly.asc 2>/dev/null | awk -F: '$1=="fpr" {print $10; exit}')
  test "$fingerprint" = 4EFC590696CB15B87C73A3AD82CC8797C838DCFD
  sudo install -m 644 /tmp/raft-zabbly.asc /etc/apt/keyrings/raft-zabbly.asc
  . /etc/os-release
  printf 'Types: deb\nURIs: https://pkgs.zabbly.com/incus/lts-6.0\nSuites: %s\nComponents: main\nArchitectures: %s\nSigned-By: /etc/apt/keyrings/raft-zabbly.asc\n' "$VERSION_CODENAME" "$architecture" | sudo tee /etc/apt/sources.list.d/raft-incus.sources >/dev/null
  sudo apt-get update -qq
  sudo env DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=l apt-get install -y --no-install-recommends incus btrfs-progs
fi
sudo incus version
