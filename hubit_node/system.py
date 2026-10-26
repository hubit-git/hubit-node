import subprocess


def run(*args, input=None, check=True):
    """Runs a command (never through a shell) and returns its stdout."""
    done = subprocess.run(args, input=input, capture_output=True, text=True)
    if check and done.returncode != 0:
        raise RuntimeError(f"{args[0]} failed: {done.stderr.strip() or done.stdout.strip()}")
    return done.stdout.strip() if check else done
