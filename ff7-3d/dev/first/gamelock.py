"""Share the game with the review watcher: `with held():` creates report/review/game.lock, waits for the watcher's
watcher.busy to go, and deletes the lock afterwards. Leaves the game on view 3d."""
import contextlib, os, sys, time, ctypes
from pathlib import Path
sys.path.insert(0, r"C:\Users\you\Desktop\ff7-3d\tools")
os.environ.setdefault("CTL_EXE", "ff7_en.exe")
import ctl, live, winshot  # noqa: E402

REVIEW = Path(r"C:\Users\you\Desktop\ff7-3d\report\review")
LOCK, BUSY = REVIEW / "game.lock", REVIEW / "watcher.busy"


def in_front():
    hwnd = ctl.window()
    return bool(hwnd) and ctypes.windll.user32.GetForegroundWindow() == hwnd


TAG = "fix-agent"


def _take_lock(end):
    """Create LOCK atomically with our tag; wait while anyone else holds it."""
    while True:
        try:
            fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, TAG.encode()); os.close(fd)
            return
        except FileExistsError:
            if time.time() > end:
                raise TimeoutError("someone else holds the game lock")
            time.sleep(2)


def _release_lock():
    try:
        if LOCK.read_text().strip() == TAG:
            LOCK.unlink()
    except FileNotFoundError:
        pass


@contextlib.contextmanager
def held(limit=600):
    """Takes the lock (atomic, tagged), waits for the watcher's BUSY to go and for the user not to have had the game
    in front for a minute, then holds the game. Deletes the lock only if it's still ours."""
    end = time.time() + limit
    _take_lock(end)
    used = False
    try:
        quiet_since = time.time()
        while True:
            if in_front():
                quiet_since = time.time()
            if not BUSY.exists() and time.time() - quiet_since >= 60:
                break
            if time.time() > end:
                raise TimeoutError("the game didn't come free")
            time.sleep(1)
        assert ctl.window(), "game not running"
        assert live.status().get("mode") == "0" and live.status().get("field"), f"game not on a field: {live.status()}"
        used = True
        yield
    finally:
        try:
            if used and not in_front() and live.status().get("view") != "3d":
                live.send("view 3d"); time.sleep(3)
        finally:
            _release_lock()


def shot(path):
    winshot.shot(str(path))


def view(v, field):
    live.send(f"view {v}")
    time.sleep(1.0)
    end = time.time() + 20
    while live.status().get("view") != v or live.status().get("field") != field:
        if time.time() > end:
            raise RuntimeError(f"view {v}: {live.status()}")
        time.sleep(0.3)
    time.sleep(2.5)
