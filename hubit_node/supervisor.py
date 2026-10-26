"""PID 1 of the container: renders the config, runs one process per protocol module, restarts what dies.
SIGHUP = read the config again and restart (the CLI sends it after a change), SIGTERM = stop."""
import signal
import subprocess
import sys
import time

from . import config, db, modules, users

procs = {}
flags = {"reload": False, "stop": False}


def log(text):
    print(f"[hubit-node] {text}", flush=True)


def start(module, cfg):
    module.render(cfg)
    procs[module.NAME] = subprocess.Popen(module.command(cfg))
    log(f"{module.NAME} started")


def start_all():
    cfg = config.load()
    config.save(cfg)
    users.sync_all(cfg)
    for module in modules.enabled(cfg):
        start(module, cfg)


def stop_all():
    for proc in procs.values():
        proc.terminate()
    for proc in procs.values():
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    procs.clear()


def main():
    db.init()
    signal.signal(signal.SIGHUP, lambda *_: flags.update(reload=True))
    signal.signal(signal.SIGTERM, lambda *_: flags.update(stop=True))
    start_all()
    while not flags["stop"]:
        time.sleep(1)
        if flags["reload"]:
            flags["reload"] = False
            log("reloading")
            stop_all()
            start_all()
        cfg = config.load()
        for module in modules.enabled(cfg):
            proc = procs.get(module.NAME)
            if proc is not None and proc.poll() is not None:
                log(f"{module.NAME} stopped with {proc.returncode}, restarting")
                start(module, cfg)
    stop_all()
    sys.exit(0)


if __name__ == "__main__":
    main()
