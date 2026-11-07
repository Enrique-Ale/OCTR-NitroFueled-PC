import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import physics_compare as pc  # noqa: E402


def write_run(folder, name, fps, slow=1.0, repeat=False):
    """A kart accelerating along x; slow < 1 makes it slower (a frame-rate dependent loss)."""
    path = Path(folder) / name
    lines = ["# menu noise", "# run high_fps_physics=1", "kart,kind,t,dt,speed,out,top,k,k_used,x,y,z"]
    dt, x, speed, t = 1 / fps, 0.0, 0.0, 0.0
    for _ in range(int(fps * 4)):
        speed = min(speed + 30 * dt * slow, 60 * slow)
        x += speed * dt
        t += dt
        row = f"0,0,{t:.5f},{dt:.6f},{speed:.4f},{speed:.4f},60,-1,-1,{x:.3f},0,0"
        lines.append(row)
        if repeat:
            lines.append(row)  # e4a220 called twice in the same frame
        lines.append(f"1,0,{t:.5f},{dt:.6f},0,0,60,-1,-1,0,0,0")
    path.write_text("\n".join(lines) + "\n")
    return str(path)


class PhysicsCompareTest(unittest.TestCase):
    def test_same_physics_lines_up_and_a_loss_shows(self):
        with tempfile.TemporaryDirectory() as folder:
            ref = write_run(folder, "60.csv", 60)
            same = write_run(folder, "144.csv", 144, repeat=True)
            slower = write_run(folder, "144-off.csv", 144, slow=.9)
            label, frames = pc.load(same, 0)
            self.assertEqual(label, "high_fps_physics=1")
            self.assertEqual(len(frames), 576)
            self.assertAlmostEqual(frames[-1]["t"], 4, places=2)
            out = io.StringIO()
            sys.argv = ["physics_compare.py", ref, same, slower]
            with contextlib.redirect_stdout(out):
                pc.main()
            text = out.getvalue()
            run2 = next(line for line in text.splitlines() if line.strip().startswith("run 2:"))
            run3 = next(line for line in text.splitlines() if line.strip().startswith("run 3:"))
            self.assertLess(abs(float(run2.split("(")[1].split(" s")[0])), .03)
            self.assertGreater(float(run3.split("(")[1].split(" s")[0]), .1)


if __name__ == "__main__":
    unittest.main()
