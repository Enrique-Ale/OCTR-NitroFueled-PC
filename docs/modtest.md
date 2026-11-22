# Mod test runs (lab)

`tools/modtest.py` starts the game, drives it to a scene with scripted pad input, captures frames
and closes it, so a mod maker can compare what the game draws with their own renders, run after
run, without playing by hand. It is a lab aid: nothing changes in a normal launch. The game only
reads `BB_MODTEST_DIR`, `BB_PAD_FILE`, `BB_PAD_REPLAY` and `BB_RENDERER` when they are set, and the
script sets them for its own launch only.

Shots are answered by the **Direct3D 11** renderer (Windows) for now. The native Vulkan renderer
runs the route but does not answer shots yet.

## Route

A text file of steps (see the docstring of `tools/modtest.py`, and `tools/modtest_routes/example.txt`):
`waitlog`, `wait`, `press`, `hold`, `replay` (a recording made with `--record`, F9 to start and
stop), `shot NAME`, `quit`. Menus are reached by timings, so leave generous pauses.

## What a shot leaves

`OUT/NAME/`:

| File | What |
|---|---|
| `screen.bmp` / `screen.png` | the picture shown right after the captured frame, before the port's overlay (`.png` when Pillow is installed) |
| `draws.jsonl` | one JSON object per draw of the frame, below |
| `draws.txt`, `cap_*.bmp` | F11's capture: the full draw log and the render targets of the frame (G-buffer included) |
| `renderdoc*.rdc` | RenderDoc's capture of the same frame, with `--renderdoc <renderdoc.dll>` |
| `done` | written last: renderer, frame number, draw count |

`OUT/summary.json` lists the shots; `OUT/game.log` is the game's output.

### draws.jsonl

`draw` (the same number RenderDoc's event list shows in its markers), `vs` / `ps` / `gs` (shader
names), `alpha` (`blend` when blending is on, `clip` when the pixel shader can discard, else
`opaque`), `discard`, `blend` (CB_BLEND0_CONTROL decoded), `rt_mask`, `alpha_to_mask`, `depth`
(bound read-only or read-write, test, write, function), `prim`, `count`, `indexed`, `targets`,
`viewport`, `cull_setup` (PA_SU_SC_MODE_CNTL), `textures` (stage, slot, address, size, mips, GCN
data and number formats, the DXGI format used, tiling, swizzle, `render_target` or `asset`),
`vs_constants` / `ps_constants` (each buffer's slot, size and first 64 floats).

A skinned vertex shader's largest constant buffer holds its bone matrices: its size over 48 (3x4
floats) is an estimate of the bones, to be checked against a model whose bone count is known.

## RenderDoc

`--renderdoc "C:\Program Files\RenderDoc\renderdoc.dll"` loads RenderDoc into the game before the
Direct3D 11 device is made (`BB_RENDERDOC`, as F11's captures). Each shot is then also a RenderDoc
capture, through RenderDoc's in-application API. The API does not change what the GPU driver
offers: a machine whose driver lacks Vulkan 1.3 cannot run the Vulkan renderers under RenderDoc
either.
