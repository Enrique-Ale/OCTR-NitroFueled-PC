"""tools/modtest.py: routes parse, and a run talks to the renderer through BB_MODTEST_DIR."""
import importlib.util
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from paths import ROOT


def load_tool():
    spec = importlib.util.spec_from_file_location('modtest', ROOT / 'tools' / 'modtest.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeGame:
    """Stands in for the game: answers shots as gpu/shim/d3d11/renderer.cpp ModtestPresent does."""
    pads = []

    def __init__(self, env, log_path):
        self.env = env
        self.dir = Path(env['BB_MODTEST_DIR'])
        self.lines = ['gfx: D3D11 device ready']
        self.running = True
        self.quit_asked = False
        threading.Thread(target=self._renderer, daemon=True).start()

    def _renderer(self):
        while self.running:
            pad = Path(self.env['BB_PAD_FILE']).read_text()
            if pad.strip():
                FakeGame.pads.append(pad.strip())
            request = self.dir / 'request'
            if request.is_file():
                name = request.read_text().strip()
                request.unlink()
                if name == 'quit':
                    self.quit_asked = True
                    self.running = False
                    break
                (self.dir / name).mkdir(exist_ok=True)
                (self.dir / name / 'draws.jsonl').write_text('{"draw":1}\n')
                (self.dir / name / 'screen.bmp').write_bytes(b'BM')
                (self.dir / name / 'done').write_text(
                    json.dumps({'renderer': 'd3d11', 'frame': 7, 'draws': 1, 'renderdoc': False,
                                'picture': 'screen.bmp'}))
            time.sleep(0.01)

    def wait_line(self, pattern, timeout, start=0):
        return 0 if 'ready' in pattern else None

    def alive(self):
        return self.running

    def stop(self, grace=15):
        deadline = time.monotonic() + 2
        while self.running and time.monotonic() < deadline:
            time.sleep(0.01)
        self.running = False


class ModtestTests(unittest.TestCase):
    def setUp(self):
        self.tool = load_tool()
        self.tool.to_png = lambda bmp: None  # no Pillow needed
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def route(self, text):
        path = self.root / 'route.txt'
        path.write_text(text)
        return path

    def test_route_parses_steps(self):
        (self.root / 'menu.pad').write_text('0 0 128 128 128 128 0 0\n1500 16384 128 128 128 128 0 0\n')
        r = self.tool.Route(self.route(
            'waitlog gfx: D3D11 device ready 600  # the device\n'
            'wait 2\npress cross+lx=40 0.3\nreplay menu.pad\nshot front\nquit\n'))
        self.assertEqual([s[1] for s in r.steps], ['waitlog', 'wait', 'press', 'replay', 'shot', 'quit'])
        self.assertEqual(r.steps[0][2], ['gfx: D3D11 device ready', 600.0])
        self.assertEqual(r.steps[2][2], ['cross lx=40', 0.3])
        self.assertEqual(r.replay, (self.root / 'menu.pad').resolve())

    def test_route_rejects_bad_steps(self):
        for text in ('press jump\n', 'shot ../x\n', 'fly 3\n', 'replay missing.pad\n'):
            with self.assertRaises(SystemExit, msg=text):
                self.tool.Route(self.route(text))

    def test_run_takes_shots_and_writes_a_summary(self):
        self.tool.Game = FakeGame
        out = self.root / 'out'
        code = self.tool.main([str(self.route('waitlog ready 5\npress cross 0.05\nshot front\nshot back\n')),
                               '--out', str(out), '--renderer', 'd3d11', '--shot-timeout', '5'])
        self.assertEqual(code, 0)
        summary = json.loads((out / 'summary.json').read_text())
        self.assertTrue(summary['ok'])
        self.assertEqual([s['name'] for s in summary['shots']], ['front', 'back'])
        self.assertEqual(summary['shots'][0]['draws'], 1)
        self.assertIn('cross', FakeGame.pads)
        self.assertFalse((out / 'request').exists())  # "quit" was read

    def test_a_shot_nobody_answers_fails_the_run(self):
        class Silent(FakeGame):
            def _renderer(self):
                pass
        self.tool.Game = Silent
        out = self.root / 'out'
        code = self.tool.main([str(self.route('shot front\n')), '--out', str(out), '--shot-timeout', '0.5'])
        self.assertEqual(code, 1)
        self.assertIn('no answer to shot front', json.loads((out / 'summary.json').read_text())['error'])


if __name__ == '__main__':
    unittest.main()
