"""The OpenVPN certificate bundle: ca.crt, server.crt, server.key, tls-crypt.key (10 years, one for every node).
With Hubit the panel keeps the bundle and pushes it to a node; `pki create` / `import` / `export` are for standalone use."""
import io
import os
import secrets
import shutil
import tarfile
import tempfile

from .config import DATA
from .system import run

DIR = f"{DATA}/pki"
FILES = ("ca.crt", "server.crt", "server.key", "tls-crypt.key")
SERVER_NAME = "hubit-vpn"
EXTENSIONS = "basicConstraints=CA:FALSE\nkeyUsage=digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\n"


def exists():
    return all(os.path.exists(f"{DIR}/{name}") for name in FILES)


def static_key():
    """A 2048 bit key in OpenVPN's static key format (what tls-crypt reads)."""
    hexed = secrets.token_hex(256)
    lines = [hexed[i:i + 32] for i in range(0, len(hexed), 32)]
    return "\n".join(["-----BEGIN OpenVPN Static key V1-----", *lines, "-----END OpenVPN Static key V1-----"]) + "\n"


def install(files):
    """files: {name: bytes}. Replaces the bundle in one step."""
    os.makedirs(DATA, exist_ok=True)
    tmp = tempfile.mkdtemp(dir=DATA)
    try:
        for name in FILES:
            path = f"{tmp}/{name}"
            with open(path, "wb") as f:
                f.write(files[name])
            os.chmod(path, 0o600 if name.endswith(".key") else 0o644)
        shutil.rmtree(DIR, ignore_errors=True)
        os.rename(tmp, DIR)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def create(days=3650):
    with tempfile.TemporaryDirectory() as tmp:
        run("openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", str(days),
            "-keyout", f"{tmp}/ca.key", "-out", f"{tmp}/ca.crt", "-subj", "/CN=Hubit VPN CA")
        run("openssl", "req", "-newkey", "rsa:2048", "-nodes", "-keyout", f"{tmp}/server.key",
            "-out", f"{tmp}/server.csr", "-subj", f"/CN={SERVER_NAME}")
        with open(f"{tmp}/ext.cnf", "w") as f:
            f.write(EXTENSIONS)
        run("openssl", "x509", "-req", "-in", f"{tmp}/server.csr", "-CA", f"{tmp}/ca.crt", "-CAkey", f"{tmp}/ca.key",
            "-CAcreateserial", "-out", f"{tmp}/server.crt", "-days", str(days), "-extfile", f"{tmp}/ext.cnf")
        files = {name: open(f"{tmp}/{name}", "rb").read() for name in FILES if name != "tls-crypt.key"}
    files["tls-crypt.key"] = static_key().encode()
    install(files)  # the CA key is not kept: the server certificate is all a node needs


def export():
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name in FILES:
            tar.add(f"{DIR}/{name}", arcname=name)
    return buf.getvalue()


def import_bundle(data):
    files = {}
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for member in tar.getmembers():
            if member.isfile() and member.name in FILES:
                files[member.name] = tar.extractfile(member).read()
    missing = [name for name in FILES if name not in files]
    if missing:
        raise ValueError("the bundle misses: " + ", ".join(missing))
    install(files)


def read(name):
    with open(f"{DIR}/{name}") as f:
        return f.read().strip()


def expires():
    """The end date of the server certificate, or None."""
    if not exists():
        return None
    return run("openssl", "x509", "-in", f"{DIR}/server.crt", "-noout", "-enddate").split("=", 1)[1]
