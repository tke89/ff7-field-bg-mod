"""Go to a field in FF8 Remastered: write a jump save standing in it (saves.py), restart the game, and load it.

The save is a copy of a base save (a save of the user's; their old saves are free to use, 2026-11-21) moved to the
field: the story state is the base save's. It goes in slot 2, save 30, which the load screen then has selected (the
newest file). About a minute from click to field."""
import threading
import time

from . import control, saves, scripts
from .field import Field

DEFAULT_BASE = "slot1_save04.ff8"  # Ultimecia Castle, disc 4: the first save tested


class Player:
    def __init__(self, game):
        self.game = game
        self.lock = threading.Lock()
        self.status = {"busy": False, "field": None, "message": "", "ok": None}
        self.trace, self.t0 = [], 0.0

    def _say(self, msg, **kw):
        self.status.update(message=msg, **kw)

    def maplist(self):
        r = self.game.archive.root()
        return r.sub("c:/ff8/data/x/field/mapdata").read("c:/ff8/data/x/field/mapdata/maplist").decode(
            "latin-1").split()

    def write_jump(self, name, base=DEFAULT_BASE):
        """Write the jump save for field `name`; returns (path, x, y, triangle)."""
        folder = saves.saves_dir()
        files = self.game.archive.field(name)
        ids = files.find(f"/{name}.id")
        if not ids:
            raise ValueError(f"{name} has no walkmesh to stand on")
        tris = saves.walkmesh(files.read(ids[0]))
        if not tris:
            raise ValueError(f"{name}'s walkmesh is empty")
        x, y, tri = saves.standing_spot(tris)
        f = Field(name, files)
        place = scripts.place(f.jsm) if f.jsm is not None else None
        block = saves.jump_block(saves.read(folder / base), saves.field_id(self.maplist(), name), x, y, tri, place)
        path = saves.jump_path(folder)
        path.write_bytes(saves.compress(block))
        return path, x, y, tri

    def start(self, name, base=DEFAULT_BASE):
        """Go to the field in the background (status() tells how it goes). False if a jump is already running."""
        with self.lock:
            if self.status["busy"]:
                return False
            self.status = {"busy": True, "field": name, "message": "writing the jump save", "ok": None}
        threading.Thread(target=self._run, args=(name, base), daemon=True).start()
        return True

    def _run(self, name, base):
        try:
            self.write_jump(name, base)
            self.boot()
            self._say(f"in {name}", busy=False, ok=True)
        except Exception as e:  # noqa: BLE001 (reported on the page)
            self._say(f"couldn't go to {name}: {e}", busy=False, ok=False)

    def boot(self):
        """Restart the game and load the jump save (slot 2, the newest save). Each key is chosen from what the game
        shows (control.screen), so a slow start or a skipped logo doesn't put the presses out of step."""
        self._say("restarting the game")
        self.trace, self.t0 = [], time.time()  # (seconds, phase or "press", screen or key, detail): for diagnosis
        if control.running():
            control.close()
            time.sleep(3)
        control.launch()
        end = time.time() + 90
        hwnd = None
        while time.time() < end and hwnd is None:
            time.sleep(2)
            hwnd = control.window()
        if hwnd is None:
            raise RuntimeError("the game window didn't appear (is Steam running?)")
        time.sleep(6)
        self._say("skipping the logos (keep the game window in front)")
        phase, pressed, loading_since, missed = "logos", 0.0, 0.0, 0
        end = time.time() + 120
        while time.time() < end:
            # looked up every time: at start-up the game shows a smaller window first, then its real one (a handle
            # kept from the start pressed keys into a window that was gone, and the game sat on "Press Any Key")
            hwnd = control.window() or hwnd
            kind, arg = control.screen(control.grab(hwnd))
            self.trace.append((round(time.time() - self.t0, 1), phase, kind, arg))

            def press(key):
                nonlocal missed
                ok = control.press(hwnd, key)
                self.trace.append((round(time.time() - self.t0, 1), "press", key, ok))
                missed = 0 if ok else missed + 1
                return ok

            if phase == "loading":
                if kind == "menu":  # the save list fading out, or a message ("Saved data is damaged") that stays
                    if time.time() - loading_since > 10:
                        raise RuntimeError("the game showed a message instead of loading the jump save")
                    time.sleep(1)
                    continue
                time.sleep(6)  # the field fading in: make sure no message followed
                if control.screen(control.grab(hwnd))[0] == "menu":
                    raise RuntimeError("the game showed a message instead of loading the jump save")
                control.focus(hwnd)  # there: the game in front to look at
                return
            # a step only counts once its key press got through (the game takes keys only while in front)
            if kind == "title":
                if phase != "title" or time.time() - pressed > 6:  # once: a second Enter would pick a slot
                    self._say("at the title screen")
                    if press("enter"):  # Continue: the title's cursor starts there
                        phase, pressed = "title", time.time()
            elif kind == "slots":
                if arg != saves.JUMP_SLOT:
                    self._say("choosing slot 2")
                    press("down")
                elif phase != "slots" or time.time() - pressed > 6:
                    if press("enter"):
                        phase, pressed = "slots", time.time()
            elif kind == "menu":
                if phase == "slots":  # slot 2's save list, the newest save (the jump save) selected
                    self._say("loading the jump save")
                    if press("enter"):
                        phase, loading_since = "loading", time.time()
                else:  # a menu we didn't open (another slot's saves, a message): back out to the slots
                    press("esc")
            elif kind == "other" and phase == "logos":
                press("enter")  # skip a logo
            if missed >= 3:
                self._say("waiting for the game window to be in front (it only takes keys then)")
            time.sleep(2.5)
        last = self.trace[-1] if self.trace else None
        raise RuntimeError(f"the game didn't get past its {phase} (timed out; last screen: {last})")
