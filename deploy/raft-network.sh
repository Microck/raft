#!/bin/sh
set -eu
# Peer frames can bypass IP hooks when bridge-netfilter is absent. Filter them
# at the bridge hook, scoped to Raft, without changing host-wide bridge sysctls.
nft -f - <<'RULES'
add table bridge raft
add chain bridge raft peers { type filter hook forward priority -200; policy accept; }
flush chain bridge raft peers
add rule bridge raft peers meta ibrname "rfbr0" meta obrname "rfbr0" counter drop
RULES
# Replace the owned IPv4 chains in one transaction. Existing jumps continue to
# enforce the old complete policy until the new complete policy commits.
iptables-restore --noflush <<'RULES'
*filter
:RAFT-INPUT - [0:0]
:RAFT-FORWARD - [0:0]
-F RAFT-INPUT
-F RAFT-FORWARD
-A RAFT-INPUT -p udp --dport 67 -j ACCEPT
-A RAFT-INPUT -d 10.232.0.1 -p udp --dport 53 -j ACCEPT
-A RAFT-INPUT -d 10.232.0.1 -p tcp --dport 53 -j ACCEPT
-A RAFT-INPUT -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
-A RAFT-INPUT -j REJECT
-A RAFT-FORWARD -d 10.0.0.0/8 -j REJECT
-A RAFT-FORWARD -d 100.64.0.0/10 -j REJECT
-A RAFT-FORWARD -d 169.254.0.0/16 -j REJECT
-A RAFT-FORWARD -d 172.16.0.0/12 -j REJECT
-A RAFT-FORWARD -d 192.168.0.0/16 -j REJECT
-A RAFT-FORWARD -j ACCEPT
COMMIT
RULES
iptables -C INPUT -i rfbr0 -j RAFT-INPUT 2>/dev/null || iptables -I INPUT 1 -i rfbr0 -j RAFT-INPUT
iptables -C FORWARD -i rfbr0 -j RAFT-FORWARD 2>/dev/null || iptables -I FORWARD 1 -i rfbr0 -j RAFT-FORWARD
iptables -C FORWARD -o rfbr0 -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT 2>/dev/null || iptables -I FORWARD 1 -o rfbr0 -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT

iptables -C FORWARD -o rfbr0 -m conntrack --ctstate NEW -j REJECT 2>/dev/null || iptables -I FORWARD 1 -o rfbr0 -m conntrack --ctstate NEW -j REJECT
# IPv6 is not provided on this bridge; reject link-local peer traffic too.
ip6tables -C INPUT -i rfbr0 -j REJECT 2>/dev/null || ip6tables -I INPUT 1 -i rfbr0 -j REJECT
ip6tables -C FORWARD -i rfbr0 -j REJECT 2>/dev/null || ip6tables -I FORWARD 1 -i rfbr0 -j REJECT
ip6tables -C FORWARD -o rfbr0 -j REJECT 2>/dev/null || ip6tables -I FORWARD 1 -o rfbr0 -j REJECT
