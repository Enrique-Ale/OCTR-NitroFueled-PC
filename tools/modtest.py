"""Mod test: start the game, drive it to a scene, capture frames, close it (docs/modtest.md).

    python tools/modtest.py ROUTE --out FOLDER [--renderer d3d11] [--renderdoc PATH] [--timeout S]
    python tools/modtest.py --record ROUTE.pad          record a route with the pad (F9 starts/stops)

ROUTE is a text file of steps, one per line ("#" starts a comment):

    waitlog REGEX [SECONDS]     wait for a line of the game's output (default 300 s)
    wait SECONDS                sleep
    press BUTTONS [SECONDS]     hold buttons for a while (default 0.15 s), then release
    hold BUTTONS SECONDS        the same, for longer; BUTTONS: cross circle square triangle l1 r1 l2 r2
                                l3 r3 options touchpad up down left right, lx= ly= rx= ry= (0..255),
                                several joined by "+" (cross+lx=40)
    replay FILE.pad [SECONDS]   play a recording made with --record, then wait for it to end
    shot NAME                   capture the next frame into FOLDER/NAME (needs a renderer that
                                answers: Direct3D 11 for now)
    quit                        end the game (also done after the last step)

What a shot leaves (Direct3D 11, gpu/shim/d3d11/renderer.cpp ModtestPresent): screen.bmp (and
screen.png when Pillow is installed), draws.jsonl (one JSON object per draw), draws.txt and the
render targets of that frame, RenderDoc's capture when --renderdoc is given, and done. The run's
summary is FOLDER/summary.json; the game's output FOLDER/game.log.

Nothing here changes a normal launch: the game only looks at BB_MODTEST_DIR, BB_PAD_FILE and
BB_PAD_REPLAY when they are set, as this script does for its own launch.
"""
import argparse
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

PORT = Path(__file__).resolve().parent.parent
WINDOWS = os.name == 'nt'
BUTTONS = {'cross', 'circle', 'square', 'triangle', 'l1', 'r1', 'l2', 'r2', 'l3', 'r3', 'options', 'touchpad',
           'touchpad_left', 'touchpad_right', 'up', 'down', 'left', 'right'}


class Route:
    def __init__(self, path):
        self.steps = []
        for number, raw in enumerate(Path(path).read_text(encoding='utf-8').splitlines(), 1):
            line = raw.split('#', 1)[0].strip()
            if not line:
                continue
            words = line.split()
            cmd, args = words[0].lower(), words[1:]
            if cmd == 'waitlog':
                # the regex may hold spaces: a trailing number is the timeout
                timeout = 300.0
                if len(args) > 1 and re.fullmatch(r'\d+(\.\d+)?', args[-1]):
                    timeout = float(args.pop())
                args = [' '.join(args), timeout]
            elif cmd in ('press', 'hold'):
                if not args:
                    raise SystemExit(f'{path}:{number}: {cmd} needs buttons')
                for token in args[0].split('+'):
                    if token not in BUTTONS and not re.fullmatch(r'[lr][xy]=\d{1,3}', token):
                        raise SystemExit(f'{path}:{number}: unknown button {token}')
                args = [args[0].replace('+', ' '), float(args[1]) if len(args) > 1 else 0.15]
            elif cmd == 'wait':
                args = [float(args[0])]
            elif cmd == 'replay':
                replay = (Path(path).parent / args[0]).resolve()
                if not replay.is_file():
                    raise SystemExit(f'{path}:{number}: no recording {replay}')
                args = [replay, float(args[1]) if len(args) > 1 else None]
            elif cmd == 'shot':
                if len(args) != 1 or not re.fullmatch(r'[A-Za-z0-9_\-][A-Za-z0-9_.\-]{0,62}', args[0]):
                    raise SystemExit(f'{path}:{number}: shot needs one name (letters, digits, - _ .)')
            elif cmd != 'quit':
                raise SystemExit(f'{path}:{number}: unknown step {cmd}')
            self.steps.append((number, cmd, args))
        replays = {args[0] for _, cmd, args in self.steps if cmd == 'replay'}
        if len(replays) > 1:
            raise SystemExit('one recording per route (the game reads one BB_PAD_REPLAY)')
        self.replay = next(iter(replays), None)


def launcher_game_dir():
    """The game folder the launcher uses (launcher.ini game_dir), else the portable package's
    CUSA13795-1.21 next to it, as "Jugar CTR Nitro-Fueled.bat" does."""
    data = Path(os.environ.get('BB_DATA_DIR') or PORT)
    try:
        for line in (data / 'launcher.ini').read_text(encoding='utf-8', errors='replace').splitlines():
            name, sep, value = line.partition('=')
            if sep and name.strip() == 'game_dir' and value.strip() and Path(value.strip()).is_dir():
                return value.strip()
    except OSError:
        pass
    return str(PORT / 'CUSA13795-1.21') if (PORT / 'CUSA13795-1.21').is_dir() else None


def tell_ended(game):
    """The game's last lines, when it ended on its own."""
    with game.cond:
        tail = game.lines[-8:]
    print('modtest: the game ended. Its last lines:', file=sys.stderr)
    for line in tail:
        print('    ' + line, file=sys.stderr)


class Game:
    """The game started the way its launchers do (run.py on Windows, run.sh on Linux), its output
    kept in game.log and watched for waitlog steps."""

    def __init__(self, env, log_path):
        self.lines = []
        self.cond = threading.Condition()
        self.done = False
        self.log = open(log_path, 'w', encoding='utf-8', errors='replace')
        if not env.get('BB_GAME_DIR'):
            game = launcher_game_dir()
            if game:
                env['BB_GAME_DIR'] = game
        if WINDOWS:
            command = [sys.executable, '-X', 'utf8', 'run.py']
            extra = {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP}
        else:
            command = ['bash', 'run.sh']
            extra = {'start_new_session': True}
        self.proc = subprocess.Popen(command, cwd=PORT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     stdin=subprocess.DEVNULL, **extra)
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for raw in self.proc.stdout:
            line = raw.decode('utf-8', 'replace').rstrip('\r\n')
            self.log.write(line + '\n')
            self.log.flush()
            with self.cond:
                self.lines.append(line)
                self.cond.notify_all()
        with self.cond:
            self.done = True
            self.cond.notify_all()

    def wait_line(self, pattern, timeout, start=0):
        """The index of the first line from `start` on matching `pattern`, or None."""
        rx = re.compile(pattern)
        end = time.monotonic() + timeout
        with self.cond:
            at = start
            while True:
                for i in range(at, len(self.lines)):
                    if rx.search(self.lines[i]):
                        return i
                at = len(self.lines)
                left = end - time.monotonic()
                if self.done or left <= 0:
                    return None
                self.cond.wait(min(left, 1.0))

    def alive(self):
        return self.proc.poll() is None

    def stop(self, grace=15):
        if not self.alive():
            return
        try:
            self.proc.wait(grace)
            return
        except subprocess.TimeoutExpired:
            pass
        if WINDOWS:  # run.py and the game under it
            subprocess.run(['taskkill', '/T', '/F', '/PID', str(self.proc.pid)], capture_output=True)
        else:
            try:
                os.killpg(self.proc.pid, signal.SIGTERM)
                self.proc.wait(10)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(self.proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass


def write_file(path, text):
    tmp = Path(str(path) + '.tmp')
    tmp.write_text(text, encoding='utf-8')
    os.replace(tmp, path)


def to_png(bmp):
    try:
        from PIL import Image
    except ImportError:
        return None
    png = bmp.with_suffix('.png')
    Image.open(bmp).save(png)
    return png


def run(args):
    route = Route(args.route)
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    pad = out / 'pad.txt'
    write_file(pad, '')
    for stale in (out / 'request',):
        stale.unlink(missing_ok=True)
    env = dict(os.environ)
    env['BB_MODTEST_DIR'] = str(out)
    env['BB_PAD_FILE'] = str(pad)
    if route.replay:
        env['BB_PAD_REPLAY'] = str(route.replay)
    if args.renderer:
        env['BB_RENDERER'] = args.renderer
    if args.renderdoc:
        env['BB_RENDERDOC'] = args.renderdoc
    summary = {'route': str(Path(args.route).resolve()), 'renderer': args.renderer or '(bbport.ini)',
               'started': time.strftime('%Y-%m-%d %H:%M:%S'), 'shots': [], 'ok': False}
    game = Game(env, out / 'game.log')
    deadline = time.monotonic() + args.timeout
    failure = None
    try:
        for number, cmd, step in route.steps:
            if not game.alive():
                failure = f'the game ended before step {number} ({cmd})'
                tell_ended(game)
                break
            if time.monotonic() > deadline:
                failure = f'--timeout reached at step {number}'
                break
            print(f'[{number}] {cmd} {" ".join(str(a) for a in step)}', flush=True)
            if cmd == 'waitlog':
                if game.wait_line(step[0], step[1]) is None:
                    failure = f'step {number}: no line matching {step[0]!r} within {step[1]:.0f} s'
                    break
            elif cmd == 'wait':
                time.sleep(step[0])
            elif cmd in ('press', 'hold'):
                write_file(pad, step[0] + '\n')
                time.sleep(step[1])
                write_file(pad, '')
                time.sleep(0.1)
            elif cmd == 'replay':
                mark = len(game.lines)
                write_file(pad, 'replay\n')
                time.sleep(0.2)
                write_file(pad, '')
                length = step[1]
                if length is None:  # the recording's last sample, plus the 0.5 s the game waits
                    last = Path(step[0]).read_text().split()[-8:]
                    length = (int(last[0]) / 1000.0 + 0.5) if len(last) == 8 else 0
                if game.wait_line(r'pad replay finished', length + 30, mark) is None:
                    failure = f'step {number}: the recording did not finish playing'
                    break
            elif cmd == 'shot':
                name = step[0]
                folder = out / name
                (folder / 'done').unlink(missing_ok=True)
                write_file(out / 'request', name + '\n')
                end = time.monotonic() + args.shot_timeout
                while time.monotonic() < end and game.alive() and not (folder / 'done').is_file():
                    time.sleep(0.25)
                if not (folder / 'done').is_file():
                    failure = (f'step {number}: no answer to shot {name} within {args.shot_timeout:.0f} s '
                               '(only the Direct3D 11 renderer answers shots for now)')
                    break
                info = json.loads((folder / 'done').read_text())
                bmp = folder / info.get('picture', 'screen.bmp')
                png = to_png(bmp) if bmp.is_file() else None
                shot = {'name': name, 'folder': str(folder), 'picture': str(png or bmp), 'draws_json': str(folder / 'draws.jsonl'),
                        **{k: v for k, v in info.items() if k != 'picture'}}
                rdc = sorted(folder.glob('renderdoc*.rdc'))
                if rdc:
                    shot['renderdoc_capture'] = str(rdc[-1])
                summary['shots'].append(shot)
                print(f'    {name}: {shot["draws"]} draws, {shot["picture"]}', flush=True)
            elif cmd == 'quit':
                break
        summary['ok'] = failure is None
    finally:
        if game.alive():
            write_file(out / 'request', 'quit\n')
        game.stop()
        write_file(pad, '')
        if failure:
            summary['error'] = failure
            print('modtest:', failure, file=sys.stderr)
        summary['finished'] = time.strftime('%Y-%m-%d %H:%M:%S')
        write_file(out / 'summary.json', json.dumps(summary, indent=2, ensure_ascii=False) + '\n')
        print(f'modtest: summary in {out / "summary.json"}')
    return 0 if failure is None else 1


def record(args):
    """A normal launch with BB_PAD_RECORD: F9 in the game starts the recording, F9 again ends it."""
    env = dict(os.environ)
    env['BB_PAD_RECORD'] = str(Path(args.record).resolve())
    if args.renderer:
        env['BB_RENDERER'] = args.renderer
    print(f'Recording into {env["BB_PAD_RECORD"]}: F9 starts, F9 stops; then close the game.')
    out = Path(args.record).resolve().parent
    out.mkdir(parents=True, exist_ok=True)
    game = Game(env, out / (Path(args.record).name + '.game.log'))
    game.proc.wait()
    time.sleep(0.5)  # the last lines
    if not Path(env['BB_PAD_RECORD']).is_file():
        tell_ended(game)
        print('modtest: no recording made (F9 in the game window starts it, F9 again stops it)', file=sys.stderr)
        return 1
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    p.add_argument('route', nargs='?', help='the steps file')
    p.add_argument('--out', help='folder for the shots (made if missing)')
    p.add_argument('--renderer', choices=('d3d11', 'native', 'shadps4'), default='d3d11' if WINDOWS else None,
                   help='this launch only (BB_RENDERER); default d3d11 on Windows, bbport.ini elsewhere')
    p.add_argument('--renderdoc', help="RenderDoc's renderdoc.dll: each shot is also a RenderDoc capture (D3D11)")
    p.add_argument('--timeout', type=float, default=1200, help='the whole run, seconds (default 1200)')
    p.add_argument('--shot-timeout', type=float, default=120, help='one shot, seconds (default 120)')
    p.add_argument('--record', help='record a route (.pad) instead of running one')
    p.add_argument('--game', help="the game folder (BB_GAME_DIR); default: the launcher's (launcher.ini game_dir)")
    args = p.parse_args(argv)
    if args.game:
        os.environ['BB_GAME_DIR'] = str(Path(args.game).resolve())
    if args.record:
        return record(args)
    if not args.route or not args.out:
        p.error('a route and --out are needed (or --record FILE)')
    return run(args)


if __name__ == '__main__':
    sys.exit(main())
