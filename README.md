# hubit-node

A small VPN node for [Hubit Panel](https://github.com/hubit-git/panel): SSH (tunnel only) and OpenVPN users on your own server,
managed from the Hubit admin panel through a JSON API or from the command line. It runs in Docker and installs with one command.

Status: early development. Working now: the customers' SSH (own port), users, the live online list. Coming next: limits and exact
traffic counting, OpenVPN (UDP and TCP), the API, the Hubit driver, then OpenConnect and more.

## Install (Ubuntu or Debian, as root)

```bash
git clone <this repository> && cd hubit-node
./install.sh
```

This installs Docker if it is missing, puts the project in `/opt/hubit-node`, starts the container and adds the `hubit-node` command.

## Use

```bash
hubit-node status
hubit-node online                       # who is connected now
hubit-node ssh-port 2222
hubit-node user add s1 --password secret
hubit-node user list | show | passwd | disable | enable | kick | del
```

All settings are in one file, `/opt/hubit-node/data/config.json`; the users are in `data/hubit.db`. Both survive updates and a
re-created container; the Linux users are rebuilt from the database at every start.

## Layout

`hubit_node/modules/` holds one file per protocol (`ssh.py`); the core never knows a protocol by name, so a new protocol is a new file.

## License

MIT
