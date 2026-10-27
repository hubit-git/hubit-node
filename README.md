# hubit-node

[English](README.md) | [فارسی](README.fa.md)

A small VPN node for [Hubit Panel](https://github.com/hubit-git/panel): SSH (tunnel only) and OpenVPN users on your own server,
managed from the Hubit admin panel through a JSON API, or by hand from the command line. It runs in Docker and installs with one
command.

What it does:

- SSH for customers on its own port (the server's own SSH on port 22 is not touched) and OpenVPN on UDP and TCP.
- One user per subscription, with an expiry date, a data limit and a device limit.
- Exact traffic counting per user (nftables), checked every 10 seconds; a user who is over a limit or past the date is locked
  and disconnected.
- A live online list, an HTTPS API, a command line and a self check (`hubit-node doctor`).
- Optional UDP for video calls over SSH (udpgw), which is not counted.

Status: early development; everything above works and is used with Hubit Panel 1.6 and newer. Coming later: OpenConnect and more.

## Contents

1. [Install and update](#install-and-update)
2. [First setup, step by step](#first-setup-step-by-step)
3. [Add the node to Hubit Panel](#add-the-node-to-hubit-panel)
4. [Day to day: the command line](#day-to-day-the-command-line)
5. [How the limits work](#how-the-limits-work)
6. [Addresses and ports](#addresses-and-ports)
7. [The OpenVPN file and the certificate](#the-openvpn-file-and-the-certificate)
8. [UDP for video calls over SSH (udpgw)](#udp-for-video-calls-over-ssh-udpgw)
9. [Firewall](#firewall)
10. [Moving or rebuilding a server](#moving-or-rebuilding-a-server)
11. [Troubleshooting](#troubleshooting)
12. [API](#api)
13. [Layout of the project](#layout-of-the-project)

## Install and update

On Ubuntu or Debian, as root:

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/hubit-git/hubit-node/main/install.sh)
```

The first time, it **asks** what to run (Enter takes the value in brackets): the address customers connect to, the protocols
(`ssh`, `openvpn` or both), the SSH port, the OpenVPN UDP and TCP ports (or `off`), udpgw (yes/no) and the API port. It checks every port: if
a port is already used by a program on the server, it says which one and suggests a free port. At the end it shows a summary, asks
to continue and prints the firewall commands for the ports you chose. An update (a config already exists) asks nothing.

To install without questions (scripts, many servers), set variables and `HUBIT_YES=1`: `HUBIT_HOST`, `HUBIT_PROTOCOLS`
(`ssh,openvpn`), `HUBIT_SSH_PORT`, `HUBIT_UDP_PORT`, `HUBIT_TCP_PORT` (a number or `off`), `HUBIT_API_PORT`, `HUBIT_UDPGW` (`yes` or
`no`). It also runs without questions when there is no terminal. A port that is already in use stops the install with a message.

It installs Docker if it is missing, turns IP forwarding on (OpenVPN clients are routed through the server), puts the project in
`/opt/hubit-node`, starts the container `hubit-node`, adds the `hubit-node` command and prints the API port, key and certificate
fingerprint that Hubit needs.

**To update, run the same command again.** The data in `/opt/hubit-node/data` stays (settings, users, certificates). After an
update, run `hubit-node doctor`: it should end with `all good` (a warning about your hosting provider's firewall is normal, see
[Firewall](#firewall)).

The image is built by the GitHub Action "Build image" on every push to `main` and on tags such as `v0.3.0`, and published to
`ghcr.io/hubit-git/hubit-node`. Without a published image, `install.sh` builds it on the server.

## First setup, step by step

The installer already asked for the address and the ports. Then, on the node:

```bash
hubit-node doctor                   # 1. check the server
hubit-node api                      # 2. port, key and fingerprint: you need them in Hubit
hubit-node host vpn.example.com     # (only to change the address later)
```

The OpenVPN certificate is not made on the node: you upload one bundle in Hubit Panel (Panels -> "OpenVPN certificate") and Hubit
sends it to every node. If you only test one node on its own, `hubit-node pki create` makes a bundle here instead; a bundle made
this way is replaced when you upload one in Hubit.

Open the ports in the firewall ([Firewall](#firewall)), then add the node in Hubit.

## Add the node to Hubit Panel

In the admin panel, Panels -> new panel, type **Hubit node**:

| Field | Value |
|---|---|
| Address | `https://SERVER:8443` (the API port from `hubit-node api`) |
| API key | the `key` from `hubit-node api` |
| Fingerprint | the `fingerprint` from `hubit-node api` (64 hex digits; colons and spaces are fine) |

Then **sync groups**. The node has three groups, each a set of protocols:

| Group | A user gets |
|---|---|
| `ssh` | SSH only |
| `openvpn` | OpenVPN only |
| `ssh+openvpn` | both |

Link a service to the group it should sell (a service with no group gets `ssh`). A customer who buys that service gets exactly
those protocols. To sell the same package in several protocols, see "Also sold in other services" in the package form of Hubit.

The panel checks the node by the certificate's fingerprint, before the key is sent. A node installed again **without** its data
makes a new certificate: run `hubit-node api` and give Hubit the new fingerprint.

## Day to day: the command line

Everything is `hubit-node <command>` on the node. Users made in Hubit are managed from Hubit; the commands below are for tests and
for fixing things by hand.

### Look at the node

| Command | What it does |
|---|---|
| `hubit-node status` | version, ports, switched-on protocols, number of users |
| `hubit-node doctor` | checks the server (tun, forwarding, rules, ports, firewall, API, certificate, the limits loop) and says how to fix what is wrong |
| `hubit-node online` | who is connected now: user, protocol, client IP, how long |

### Addresses and ports

| Command | What it does |
|---|---|
| `hubit-node host vpn.example.com` | the address customers get for every protocol (`auto` = this server's address) |
| `hubit-node host --ssh ssh.example.net` | SSH on its own address; OpenVPN keeps the host. `--openvpn` likewise; `default` goes back to the host |
| `hubit-node host` | shows the addresses now |
| `hubit-node ssh-port 2222` | the port of the customers' SSH |
| `hubit-node openvpn` | shows the OpenVPN ports |
| `hubit-node openvpn --udp 1194 --tcp 443` | changes them; a port or `off` (at least one of the two stays on) |
| `hubit-node ssh-udpgw 7300` | UDP for video calls over SSH; `0` = off ([details](#udp-for-video-calls-over-ssh-udpgw)) |
| `hubit-node api --port 8443` | the API port (not one used by SSH or OpenVPN) |
| `hubit-node api --new-key` | a new API key; **give the new one to Hubit** |
| `hubit-node api` | shows the API port, key and fingerprint |

After a change of port, open it in the firewall. Customers need a new OpenVPN file only when an OpenVPN port or address changed.

### Protocols

```bash
hubit-node protocol list              # which protocols are on
hubit-node protocol disable ssh       # the action first, then the name (ssh or openvpn)
hubit-node protocol enable ssh
```

A user, and its group, is kept while a protocol is off; a group only gives the protocols that are on.

### Users

```bash
hubit-node user add ali --password Abc12345 --group openvpn --days 30 --data 50 --devices 2
hubit-node user list                  # all users: group, status, online, usage, limit, devices, expiry
hubit-node user show ali
hubit-node user set ali --days 60 --data 100      # change the limits or --group
hubit-node user passwd ali --password Xyz98765
hubit-node user disable ali           # locks it and disconnects it
hubit-node user enable ali
hubit-node user kick ali              # closes its connections now; the account stays
hubit-node user reset-usage ali       # usage back to zero
hubit-node user del ali
```

The limit options (for `add` and `set`):

| Option | Meaning |
|---|---|
| `--days N` | ends N days from now |
| `--expire "YYYY-MM-DD HH:MM:SS"` | ends at this time, **UTC** (`none` = never) |
| `--data GB` | data limit in GB (`0` = unlimited) |
| `--devices N` | devices at the same time (`0` = unlimited); without it, `add` uses 1 |
| `--group NAME` | `ssh`, `openvpn`, `ssh+openvpn` (default `ssh`) |

Without `--password`, `add` makes a random one and prints it. A password is 8 to 64 characters.

## How the limits work

- **Data.** Every packet of a user is counted, both directions, and added to its usage every 10 seconds. SSH is counted by the
  user's Linux account (the tunnel's traffic leaves the server with that account), OpenVPN from OpenVPN's own byte counters of each session (the numbers of a session that ended are kept). When the
  usage reaches the limit the user is locked and disconnected. Loopback traffic and the udpgw part are not counted.
- **Time.** At the expiry time the user is locked and disconnected.
- **Devices.** A *device* is a **client IP address**. Two phones behind the same home router share one public IP, so they count as
  **one** device; a phone on mobile data has another IP and is a second one. A new login from a new IP is **refused** while the
  limit is full (the app only says "wrong username or password": SSH and OpenVPN cannot show another message). A connection from an
  IP that is already connected is always allowed. If extra devices get through (a check can be skipped on purpose when it fails, so
  nobody is locked out by a bug), the loop closes the newest IPs within 10 seconds.
- **Locked users** stay on the node with their usage, so they can be enabled again (Hubit renews or enables them through the API).

## Addresses and ports

The defaults: API `8443/tcp`, SSH `2222/tcp`, OpenVPN `1194/udp` and `1194/tcp` (in your config they may differ; `hubit-node status`
shows them). Customers get one OpenVPN file with both `remote` lines, UDP first: the client tries UDP and, after 5 seconds with
no answer, TCP. A network that blocks UDP therefore falls back to TCP by itself.

Use a **DNS only** record (no CDN proxy such as Cloudflare's orange cloud) for the address of the node.

## The OpenVPN file and the certificate

- One bundle (ca, server certificate, server key, tls-crypt key) is shared by all nodes. Because the file customers use holds
  only the ca and the tls-crypt key, **a customer's file keeps working when a node is replaced**, as long as the bundle is the same.
  The bundle's certificate is valid for years (`hubit-node doctor` shows when it ends).
- Upload it once in Hubit (Panels -> "OpenVPN certificate"). Hubit sends it to every active Hubit node when it is saved and when a
  node is added or edited; a node that was off gets it with the "send" button there.
- By hand: `hubit-node pki create` (a new bundle, only if there is none), `pki import < bundle.tgz`, `pki export > bundle.tgz`,
  `pki show` (when it ends).
- `hubit-node ovpn > client.ovpn` prints the client file: the same one for every customer (the username and password are typed
  in the app). Hubit also gives it to customers (a download, a QR code for OpenVPN Connect 3.3.6 and newer, a link).
- The customer's app asks for the username and password of the subscription; Hubit shows them in the subscription's details.

## UDP for video calls over SSH (udpgw)

SSH carries TCP only, so a video call (WhatsApp, Telegram, Instagram) over a plain SSH tunnel is slow or fails. The node therefore
runs [badvpn-udpgw](https://github.com/ambrop72/badvpn) on `127.0.0.1:7300` (only on the server itself, never open to the
internet). An app such as NetMod or HTTP Injector sends UDP packets inside the SSH tunnel to it and it sends them out.

- The customer turns on **UDPGW** in the app and types `127.0.0.1:7300`. Hubit shows this line in the connection details.
- It is **not counted** in the user's usage, on purpose: it carries only UDP and the part inside the tunnel is on loopback. Heavy
  UDP traffic (for example QUIC video) therefore passes without being counted. If that is a problem, turn it off.
- `hubit-node ssh-udpgw 7300` sets the port, `hubit-node ssh-udpgw 0` turns it off.
- OpenVPN needs none of this: it carries UDP itself and counts it.

## Firewall

Open on the server (a ufw example):

```bash
ufw allow 2222/tcp     # SSH of customers
ufw allow 1194/udp     # OpenVPN UDP
ufw allow 1194/tcp     # OpenVPN TCP (use your own ports)
ufw allow 8443/tcp     # the API: Hubit connects here (you may allow only the panel's IP)
```

`hubit-node doctor` warns when ufw is active. It cannot see the firewall of your hosting provider's website (Hetzner, OVH, ...):
open the same ports there too. The container uses the host's network, so no `-p` options are involved.

## Moving or rebuilding a server

- **Back up `/opt/hubit-node/data`**: `config.json` (settings), `hubit.db` (users and usage), `pki/` and `api/` (the certificates). Copy
  it back to a new server before the first start and the node comes back as it was; the Linux users are rebuilt from the database
  at every start.
- **Without the data**, the node is empty and has a new API certificate: its users are gone (add them again) and Hubit needs the
  new fingerprint. Customers' OpenVPN files still work as long as the bundle is the same one (upload it
  in Hubit again).
- To change the server's address and keep customers' files working, use a **domain** and move its DNS record.

## Troubleshooting

| Symptom | What to do |
|---|---|
| Anything unclear | `hubit-node doctor`, then `docker logs hubit-node --tail 50` |
| "Wrong username or password" in the app | the password is wrong, **or** the device limit is full (another IP is connected: `hubit-node online`), **or** the user is locked or ended (`hubit-node user show NAME`) |
| Two devices at home are not stopped | they share one public IP, so they are one device ([How the limits work](#how-the-limits-work)) |
| Hubit says the node is unreachable | the API port is closed in a firewall, the node is off (`docker ps`), or the key changed |
| Hubit says the certificate does not match | the node was installed again without its data: `hubit-node api` and give Hubit the new fingerprint |
| OpenVPN does not listen | no certificate yet: upload the bundle in Hubit (or `hubit-node pki create` for a test) |
| OpenVPN connects but there is no internet | forwarding is off or Docker's FORWARD policy blocks it: `hubit-node doctor` says which, `docker restart hubit-node` re-applies the rules |
| `doctor` says the limits loop has not run | `docker logs hubit-node 2>&1 \| grep -i loop` |
| Usage never grows | the loop is not running (see above), or the user has not made traffic |

## API

HTTPS with a self-signed certificate (Hubit checks its fingerprint: `hubit-node api` prints it), the key in the header
`X-API-Key`. Times are UTC `YYYY-MM-DD HH:MM:SS`, data in bytes, a user name `[a-z][a-z0-9_-]*`.

| Call | What it does |
|---|---|
| `GET /info` | version, host, protocols, number of users and of online users |
| `GET /groups` | the groups and their protocols |
| `GET /users?offset=&limit=` | users (`limit` up to 5000) |
| `POST /users` | `username`, `password` (8 characters or more), `group`, `expire_at`, `data_limit`, `devices` |
| `GET\|PATCH\|DELETE /users/{u}` | read, change (also `password`) or delete (deleting a missing user is fine) |
| `POST /users/{u}/disable\|enable\|reset-usage\|kick` | actions |
| `GET /users/{u}/connection` | what the user needs to connect: `[{protocol, host, port\|udp\|tcp, udpgw}]` |
| `GET /online` | live connections |
| `GET /profile/openvpn` | the `.ovpn` file (text) |
| `GET\|PUT /pki` | the bundle's state; `PUT` takes JSON with the four files as text (`ca.crt`, `server.crt`, `server.key`, `tls-crypt.key`) |

## Layout of the project

`hubit_node/modules/` holds one file per protocol (`ssh.py`, `openvpn.py`); the core never knows a protocol by name, so a new
protocol is a new file. `hubit_node/supervisor.py` is PID 1 of the container (it runs the processes and the 10 second loop),
`api.py` the HTTP API, `cli.py` the command line, `doctor.py` the self check.

## License

MIT
