"""PID 1 of the container: renders the config, runs the processes of the protocol modules, restarts what dies, and
runs the limits loop. SIGHUP = read the config again and restart (the CLI sends it after a change), SIGTERM = stop."""
import secrets
import signal
import subprocess
import sys
import time

from . import api, config, db, limits, modules, users

# {process name: (Popen, argv)}
procs = {}
flags = {"reload": False, "stop": False}
TICK_SECONDS = 10


def log(text):
    print(f"[hubit-node] {text}", flush=True)


def start_api():
    argv = [sys.executable, "-m", "hubit_node.api"]
    procs["api"] = (subprocess.Popen(argv), argv)
    log("api started")


def start_all():
    cfg = config.load()
    if not cfg["api"]["key"]:
        cfg["api"]["key"] = secrets.token_urlsafe(32)
    config.save(cfg)
    api.ensure_cert()
    users.sync_all(cfg)
    start_api()
    for module in modules.enabled(cfg):
        try:
            module.render(cfg)
            commands = module.commands(cfg)
        except Exception as e:  # one module's problem must not take the others down
            log(f"{module.NAME} could not start: {e}")
            continue
        if not commands:
            log(f"{module.NAME} is not ready (no process to run)")
        for name, argv in commands.items():
            procs[name] = (subprocess.Popen(argv), argv)
            log(f"{name} started")


def stop_all():
    for proc, _ in procs.values():
        proc.terminate()
    for proc, _ in procs.values():
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    procs.clear()


def watch():
    for name, (proc, argv) in list(procs.items()):
        if proc.poll() is not None:
            log(f"{name} stopped with {proc.returncode}, restarting")
            procs[name] = (subprocess.Popen(argv), argv)


def tick():
    try:
        limits.tick(config.load())
    except Exception as e:  # the loop must never stop the node
        log(f"limits loop failed: {e}")


def cleanup():
    cfg = config.load()
    for module in modules.ALL.values():
        module.stop(cfg)


def main():
    db.init()
    signal.signal(signal.SIGHUP, lambda *_: flags.update(reload=True))
    signal.signal(signal.SIGTERM, lambda *_: flags.update(stop=True))
    start_all()
    last_tick = 0
    while not flags["stop"]:
        time.sleep(1)
        if flags["reload"]:
            flags["reload"] = False
            log("reloading")
            stop_all()
            start_all()
        watch()
        if time.monotonic() - last_tick >= TICK_SECONDS:
            last_tick = time.monotonic()
            tick()
    stop_all()
    cleanup()
    sys.exit(0)


if __name__ == "__main__":
    main()
