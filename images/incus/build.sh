#!/bin/bash
# Run inside a fresh native ARM64 or AMD64 Debian 13 Incus image, without host secrets.
set -eu
case "$(dpkg --print-architecture)" in
  arm64) node_arch=arm64 ;;
  amd64) node_arch=x64 ;;
  *) echo 'Raft images support ARM64 and AMD64' >&2; exit 1 ;;
esac
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y --no-install-recommends ca-certificates curl git openssh-server rsync jq ripgrep file less sudo locales python3 python3-pip python3-venv pipx build-essential clang cmake ninja-build golang rustc cargo default-jdk maven gradle kotlin scala ruby ruby-bundler php-cli composer erlang-base elixir r-base docker.io docker-cli docker-buildx chromium chromium-sandbox ffmpeg unzip xz-utils tar x11vnc xvfb x11-utils novnc websockify openbox dbus-x11
mkdir -p /workspace /opt/raft
node_archive="node-v24.21.0-linux-${node_arch}.tar.xz"
curl -fsSL -A 'OpenAI File Downloader, XaiImageApiFetch/1.0' "https://nodejs.org/dist/v24.21.0/${node_archive}" -o /tmp/raft-node.tar.xz
curl -fsSL -A 'OpenAI File Downloader, XaiImageApiFetch/1.0' https://nodejs.org/dist/v24.21.0/SHASUMS256.txt -o /tmp/raft-node.sha256
cd /tmp
awk -v archive="$node_archive" '$2 == archive {print $1 "  raft-node.tar.xz"}' raft-node.sha256 | sha256sum -c -
tar -xJf /tmp/raft-node.tar.xz -C /usr/local --strip-components=1
PIPX_HOME=/opt/raft/pipx PIPX_BIN_DIR=/usr/local/bin pipx install uv==0.12.23
npm install -g pnpm@12.9.1 bun@1.4.2 deno@2.9.6
curl -fsSL -A 'OpenAI File Downloader, XaiImageApiFetch/1.0' https://dot.net/v1/dotnet-install.sh -o /tmp/raft-dotnet-install.sh
bash /tmp/raft-dotnet-install.sh --version 10.0.401 --install-dir /usr/local/share/dotnet
ln -s /usr/local/share/dotnet/dotnet /usr/local/bin/dotnet
useradd -m -s /bin/bash developer
printf 'developer ALL=(ALL) NOPASSWD:ALL\n' > /etc/sudoers.d/raft-developer
chmod 440 /etc/sudoers.d/raft-developer
mkdir -p /etc/ssh/sshd_config.d /etc/systemd/system/ssh.service.d
printf 'PasswordAuthentication no\nKbdInteractiveAuthentication no\nPermitRootLogin prohibit-password\nHostKey /etc/ssh/ssh_host_ed25519_key\n' > /etc/ssh/sshd_config.d/00-raft.conf
# Generate keys before sshd validates them; published templates contain no host keys.
# Only the advertised Ed25519 key is needed; generating unused RSA keys slows first boot.
cat > /etc/systemd/system/ssh.service.d/raft-keys.conf <<'UNIT'
[Service]
ExecStartPre=
ExecStartPre=/bin/sh -ec 'test -f /etc/ssh/ssh_host_ed25519_key || ssh-keygen -q -t ed25519 -N "" -f /etc/ssh/ssh_host_ed25519_key'
ExecStartPre=/usr/sbin/sshd -t
UNIT
systemctl enable docker ssh
systemctl start docker
docker info --format '{{.ServerVersion}}'
dpkg-query -W > /opt/raft/packages.tsv
npm list -g --depth=0 --json > /opt/raft/npm-packages.json
apt-get clean
rm -rf /var/lib/apt/lists/* /tmp/raft-node* /tmp/raft-dotnet-install.sh /root/.npm/_cacache /root/.npm/_logs
rm -f /etc/ssh/ssh_host_*
truncate -s 0 /etc/machine-id
