# hubit-node

A small VPN node for [Hubit Panel](https://github.com/hubit-git/panel): SSH (tunnel only) and OpenVPN users on your own server,
managed from the Hubit admin panel through a JSON API or from the command line. It runs in Docker and installs with one command.

Status: early development. Working now: the customers' SSH (own port), OpenVPN (UDP and TCP), users with time, data and device
limits, exact traffic counting, the live online list and the HTTPS API. Coming next: the limit check at connect time, the Hubit
driver, the menu, then OpenConnect and more.

## Install (Ubuntu or Debian, as root)

```bash
git clone <this repository> && cd hubit-node
./install.sh
```

This installs Docker if it is missing, puts the project in `/opt/hubit-node`, starts the container and adds the `hubit-node` command.

The image is built by the GitHub Action "Build image" (Actions tab -> Run workflow, or push a tag such as `v0.2.0`) and published
to `ghcr.io/hubit-git/hubit-node`; make the package public once (Packages -> hubit-node -> Package settings) so a server can pull
it without logging in. Without a published image, `install.sh` builds it on the server.

## Use

```bash
hubit-node status
hubit-node online                       # who is connected now
hubit-node ssh-port 2222
hubit-node user add s1 --password secret
hubit-node user add s2 --group openvpn --days 30 --data 50 --devices 2   # groups: ssh, openvpn, ssh+openvpn
hubit-node user list | show | set | passwd | reset-usage | disable | enable | kick | del
hubit-node openvpn --udp 1194 --tcp 443      # a port or off
hubit-node pki create | import | export | show
hubit-node ovpn > client.ovpn                # the one profile every customer uses
hubit-node protocol list | enable | disable <name>
hubit-node api                               # port, key and certificate fingerprint of the API
```

All settings are in one file, `/opt/hubit-node/data/config.json`; the users are in `data/hubit.db`. Both survive updates and a
re-created container; the Linux users are rebuilt from the database at every start.

## API

HTTPS, a self-signed certificate (Hubit pins its fingerprint), the key in the header `X-API-Key`. `GET /info`, `/groups`, `/online`,
`/users`, `POST /users`, `GET|PATCH|DELETE /users/{u}`, `POST /users/{u}/disable|enable|reset-usage|kick`,
`GET /users/{u}/connection`, `GET /profile/openvpn`, `GET|PUT /pki`. Times are UTC `YYYY-MM-DD HH:MM:SS`, data in bytes.

## Layout

`hubit_node/modules/` holds one file per protocol (`ssh.py`); the core never knows a protocol by name, so a new protocol is a new file.

## License

MIT
