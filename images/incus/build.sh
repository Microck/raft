#!/bin/bash
# Run inside a fresh published Debian 13 ARM64 Incus image, without host secrets.
set -eu
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y --no-install-recommends ca-certificates curl git openssh-server rsync jq ripgrep file less sudo locales python3 python3-pip python3-venv pipx build-essential clang cmake ninja-build golang rustc cargo default-jdk maven gradle kotlin scala ruby ruby-bundler php-cli composer erlang-base elixir r-base docker.io docker-cli docker-buildx chromium ffmpeg unzip xz-utils tar x11vnc xvfb novnc websockify openbox dbus-x11
mkdir -p /workspace /opt/raft
curl -fsSL -A 'OpenAI File Downloader, XaiImageApiFetch/1.0' https://nodejs.org/dist/v24.21.0/node-v24.21.0-linux-arm64.tar.xz -o /tmp/raft-node.tar.xz
curl -fsSL -A 'OpenAI File Downloader, XaiImageApiFetch/1.0' https://nodejs.org/dist/v24.21.0/SHASUMS256.txt -o /tmp/raft-node.sha256
cd /tmp
grep ' node-v24.21.0-linux-arm64.tar.xz$' raft-node.sha256 | sed 's/node-v24.21.0-linux-arm64.tar.xz/raft-node.tar.xz/' | sha256sum -c -
tar -xJf /tmp/raft-node.tar.xz -C /usr/local --strip-components=1
PIPX_HOME=/opt/raft/pipx PIPX_BIN_DIR=/usr/local/bin pipx install uv==0.12.23
npm install -g pnpm@12.9.1 bun@1.4.2 deno@2.9.6 @anthropic-ai/claude-code@2.1.289 @openai/codex@0.160.0 @mariozechner/pi-coding-agent@0.73.1 opencode-ai@1.18.34
curl -fsSL -A 'OpenAI File Downloader, XaiImageApiFetch/1.0' https://dot.net/v1/dotnet-install.sh -o /tmp/raft-dotnet-install.sh
bash /tmp/raft-dotnet-install.sh --version 10.0.401 --install-dir /usr/local/share/dotnet
ln -s /usr/local/share/dotnet/dotnet /usr/local/bin/dotnet
useradd -m -s /bin/bash developer
printf 'developer ALL=(ALL) NOPASSWD:ALL\n' > /etc/sudoers.d/raft-developer
chmod 440 /etc/sudoers.d/raft-developer
mkdir -p /etc/ssh/sshd_config.d /etc/systemd/system/ssh.service.d
printf 'PasswordAuthentication no\nKbdInteractiveAuthentication no\nPermitRootLogin prohibit-password\n' > /etc/ssh/sshd_config.d/00-raft.conf
# Generate keys before sshd validates them; published templates contain no host keys.
printf '[Service]\nExecStartPre=\nExecStartPre=/usr/bin/ssh-keygen -A\nExecStartPre=/usr/sbin/sshd -t\n' > /etc/systemd/system/ssh.service.d/raft-keys.conf
systemctl enable docker ssh
systemctl start docker
docker info --format '{{.ServerVersion}}'
dpkg-query -W > /opt/raft/packages.tsv
npm list -g --depth=0 --json > /opt/raft/npm-packages.json
apt-get clean
rm -rf /var/lib/apt/lists/* /tmp/raft-node* /tmp/raft-dotnet-install.sh /root/.npm/_cacache /root/.npm/_logs
rm -f /etc/ssh/ssh_host_*
truncate -s 0 /etc/machine-id
