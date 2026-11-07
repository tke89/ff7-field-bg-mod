"""Walk Cloud with held arrow keys (several at once for diagonals) while recording the window to a GIF.
  walk.py out.gif "right+up:1.5" "up:2" ...   (game window must be allowed to come to the front)"""
import os, sys, threading, time
sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("CTL_EXE", "ff7_en.exe")
import ctl, winshot
from PIL import Image

frames, stop = [], threading.Event()


def record():
    while not stop.is_set():
        winshot.shot("_w.png")
        frames.append(Image.open("_w.png").convert("RGB").crop((160, 0, 1120, 720)).resize((640, 480), Image.LANCZOS))


out, moves = sys.argv[1], sys.argv[2:]
h = ctl.window()
ctl.front(h)
time.sleep(0.4)
t = threading.Thread(target=record)
t.start()
time.sleep(0.5)
for m in moves:
    keys, secs = m.split(":")
    keys = keys.split("+")
    for k in keys:
        ctl._send(ctl.SCAN[k], False, k in ctl.EXTENDED)
    time.sleep(float(secs))
    for k in keys:
        ctl._send(ctl.SCAN[k], True, k in ctl.EXTENDED)
    time.sleep(0.15)
time.sleep(0.8)
stop.set()
t.join()
frames[0].save(out, save_all=True, append_images=frames[1:], duration=130, loop=0)
frames[-1].save(out.replace(".gif", "_last.png"))
print(out, len(frames), "frames")
