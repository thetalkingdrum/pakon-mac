# 78 — TLX real-roll testing handover: what landed, what broke, what's still open

Branch: `experimental-tlx-raw-import`. Three real features shipped and pushed
this session (`9eb4574`, `8d511be`, `dd7793c`); the rest of this doc is what
testing those against a real 40-frame TLX roll (not synthetic fixtures)
turned up — a real data-loss bug, a real export-mode gap, and the
Exposure-slider symptom traced to an already-documented defect. One change
is uncommitted and needs a frontend half before it does anything.

---

## 1. What shipped (commits, evidence)

- **`9eb4574`** — `pakon_app.py`'s `calibration/run` refused every call,
  including the first ever one on a fresh backend: `S.job_new("calibration")`
  ran *before* the "one calibration at a time" guard, so the guard's own
  busy-scan always found the job it had just created — itself. Confirmed
  live: a bare `POST /api/app/calibration/run` against a process that had
  never run a calibration came back 409 "A calibration is already running."
  Fixed by minting the job id only after every guard passes. Verified live
  before/after (`curl` against a fresh backend), and confirmed again
  visually in the app: the banner now correctly reads "Calibration stopped"
  / "No scanner is connected" instead of the misleading message.
- **`8d511be`** — 16-bit sRGB TIFF export (`kcmsclut.EvalU16`/`Rpd12ToSrgb16`,
  Go-only). Blends the currently-discarded low 14 bits of the tetrahedral
  interpolator's `t` toward the *next* real captured vendor byte instead of
  floor-snapping (`t>>14`) to one, anchored to real vendor sample points —
  exact at every real sample (`EvalU8(in)*257`), interpolated between them.
  New exhaustive Go test proves `EvalU8(in)*257 <= EvalU16(in) <=
  EvalU8(in)*257+257` over the whole u8 domain. **Not independently
  vendor-verified above 8 bits** — see §3 below for what this cost in
  practice.
- **`dd7793c`** — whole-roll `.raw` import
  (`pakon_render.open_tlx_capture_multi`). Concatenates each file's frame
  along the line axis into one cached array, same `Frame(a, b)` span shape
  `open_capture()` already builds from a `.bin`. `film_base` defaults to
  FindDmin over the *whole* concatenated roll. New "Import TLX roll…"
  dialog, multi-select file picker, shared `FilmBaseMeasure` component with
  the single-file Open dialog.

All three regression suites (`pakon_gate.py selftest`, `test_calib.py`,
`test_render_f135.py`) and the Go suite (`go test ./...`,
`test_kcms_clut_ports.py`) passed after each commit.

---

## 2. Real-roll testing: two rolls, one wrong format

Tested against two real rolls on the owner's Desktop, not synthetic
fixtures:

- **"Yahsica T5 ultramax at 200 rpd"** (40 files) — turned out to be **RPD
  domain, not the "corrections off" raw negative** `pakon_tlx_raw.py`
  assumes. Caught by the owner, not by any check in the adapter — it loaded
  without complaint (structurally valid TLX header, plausible byte counts)
  and would have rendered nonsense had the import job been allowed to
  finish. **`pakon_tlx_raw.py` has no domain check for this** — it verifies
  height/header shape, not that the data is actually pre-inversion. Worth a
  real validation (e.g. flag if too much of the frame sits near the
  vendor-domain ceiling) as separate follow-up work; not attempted here.
- **"2026 mai Kodak max 800…"** (40 files, real "corrections off" data,
  confirmed by the owner) — this is the roll the rest of this doc is about.

### 2.1 — Whole-roll import, verified on real data

`open_tlx_capture_multi` on all 40 real files: frame spans landed exactly
where expected (`(0,0,3000), (1,3000,6000), …`), roll-wide `FindDmin`
succeeded with no refusal (`film_base=[1866, 2244, 2253]`), and — checked by
opening actual frame renders, not just trusting the job status — 8 of 10
sampled frames (5, 10, 15, 20, 25, 30, 35, 39) rendered as real, coherent,
correctly-decoded photographs. Frames 0 and 2 rendered as near-pure noise;
their raw dmin (~6100–6200) sits well above a normal frame's (~5100),
consistent with under-exposed/near-blank frames — **confirmed by the owner:
frames 0–2 on this roll are genuinely blank.** Not a bug.

Per-frame orientation varies across the roll (right-side-up, 90°, 180°) —
expected, not a defect: film carries no per-frame orientation metadata, and
neither this port nor the real Pakon software auto-rotates individual
frames. A roll shot with the camera held differently between frames renders
that way here exactly as it would on real vendor software.

### 2.2 — A real, live-caught data-loss bug: silent purge on quit

While comparing renders, the roll's entire cached workspace (~1.4 GB,
`rgb14.npy` + `roll.json`) was found deleted between two `resume` calls
seconds apart — the first succeeded and returned real frame data, the next
failed "no workspace for '<id>'". **Not reproduced under a controlled
trigger, so this is inference from the evidence available, not a confirmed
root cause** — but the only code path in this app that deletes a workspace
is `app/main.js`'s `before-quit` handler via `confirmQuit()`:

```js
async function confirmQuit() {
  let session = null;
  try {
    session = await api('/api/app/session', { timeout: 4000 });
  } catch {
    return 'delete';                    // <-- no dialog, no confirmation
  }
  ...
```

If the health-check call to `/api/app/session` fails for *any* reason
(including a plain timeout), `confirmQuit()` returns `'delete'` immediately
— skipping the "Delete X MB of temporary data?" dialog entirely — and
`before-quit` then calls `POST /api/app/workspace/purge {all: true}`. The
backend was genuinely busy around the time this happened (a full 40-frame
real-roll decode plus several full-res frame renders back to back), so a
4-second timeout during a window close is plausible.

**This is a real, standing bug independent of anything else in this doc.**
A backend that is merely slow — not crashed, not gone — currently has the
same one-line consequence as a backend that no longer exists: everything
gets silently deleted, with zero UI feedback. **Not fixed this session** —
found, diagnosed, not yet touched. The fix is straightforwardly: don't treat
"health check timed out" as "nothing to lose, delete." At minimum, a timeout
should either retry once, or fall back to `'keep'`/prompt rather than
`'delete'` — a busy backend is evidence of the *opposite* of nothing to
lose.

**Recovery**: the roll was successfully re-imported from the same 40 source
files (still on disk, film is not consumed by this) — the import mechanism
itself was never in question, only the workspace cache.

### 2.3 — Discovered while testing: the app's own colour-engine default is easy to bypass by accident

`app/main.js`'s `startBackend()` sets `PAKON_COLOUR_ENGINE:
process.env.PAKON_COLOUR_ENGINE || 'python'` when Electron spawns its own
backend — i.e. **the shipped app's actual default is the Python tone
chain**, not Go's `ShastaToneRpd` stand-in, contrary to what `colour_engine()`
alone would suggest reading `pakon_render.py` in isolation
(`os.environ.get(COLOUR_ENGINE_ENV) or "go"`). Starting the backend by hand
for testing (`python3 tools/pakon_app.py --port N`, no env override) —
which is the natural thing to do when iterating without rebuilding/relaunching
Electron each time — silently reverts to Go's stand-in, which reads
harsher/more contrasty and clips highlights the real chain doesn't.

This produced a real false alarm this session: a first render of the roll
looked "hella contrasty and blown out" purely because the bare test backend
had no `PAKON_COLOUR_ENGINE` set. Restarting with `PAKON_COLOUR_ENGINE=python`
(matching `main.js`'s own default) fixed it — confirmed on the same frame,
same roll, only the env changed. **Not a pipeline bug — a testing-harness
gotcha.** Worth remembering for the next session: a bare backend launched
for testing needs `PAKON_COLOUR_ENGINE=python` set explicitly to match what
the real app actually ships.

### 2.4 — The new 16-bit sRGB export can't follow that setting at all

Exported the whole real roll as `sRGB · 16-bit` (the `dd7793c`-and-earlier
`8d511be` feature) and it came out with the same harsh/blown character as
§2.3's Go-stand-in false alarm — but this time it's real, not a testing
artefact. **`RenderRequest.Want16` only exists in the Go per-pixel loop**
(`main.go`'s `processImage`); there is no way to reach `kcmsclut.EvalU16`
from the deprecated Python engine, by the project's own rule that engine
"must not gain features" (`_render_colour_python`'s docstring, docs/62
§12). So **16-bit sRGB always uses Go's `ShastaToneRpd` stand-in tone,
regardless of `PAKON_COLOUR_ENGINE`** — even on a backend explicitly
configured for the real chain everywhere else. Confirmed by reading the raw
TIFF bytes directly (Pillow cannot open a 3-channel 16-bit TIFF correctly,
so `Image.open` silently mis-decodes one — do not use it to sanity-check
these files; parse the payload manually, header is 128 bytes, `<u2`,
row-major `(h, w, 3)`).

This was disclosed at build time ("not an independently verified 16-bit
render") but the *specific, concrete consequence* — that it diverges from
whatever tone chain the rest of a Python-configured session is using, every
time, unconditionally — was not spelled out clearly enough in the UI copy,
and the owner only found it by exporting a real roll and comparing. **Fix
deferred by owner's own choice this session** (see §4) — the 40 files
already exported to `~/Pictures/Film/Kodak Max 800 real test/` as 16-bit are
known-bad (Go stand-in tone) and were not re-exported.

---

## 3. The Exposure slider, traced to an already-documented defect

Owner's report: "usually I can get something close, but I have to lower
Exposure quite a bit" — i.e. the default render (density = 0) is
consistently too bright. Exposure (`density`) is real, not a UI illusion:
additive offset in vendor button-steps, converted via
`_code_values_per_button` and applied at the same seam
(`RenderRequest.UserOffsets`) in both engines, "+ is brighter (all three
channels)" (`DEFAULT_PARAMS`, `pakon_render.py:141`).

This symptom matches a defect this project's own docs already closed the
loop on (docs/74 §170–175, restated in `CLAUDE.md`): a **measured ~88–89
sRGB-code systematic overbrightness**. Root cause: the real F-135 inversion
(recovered from the live vendor table) is

    out = clamp(14750 − 3500·log10(in), 0, 16383)

— no film base, no Dmin, no pedestal, applied *before* stage 2 — while this
port's own inversion carries all four of those terms, at 1000 codes/decade
instead of 3500, applied *after* the polynomial. Opt-in fix:
`PAKON_VENDOR_INVERT=1`.

**Tested live on this real roll, not just cited from the doc.** Same frame
(15), same tone chain (Python), only the env flag changed:
`PAKON_VENDOR_INVERT=1` produced a visibly darker, less washed render — and
also a visible red/orange cast (jacket and skin tones read noticeably
warmer/more saturated). This matches docs/77 §5.5's own finding exactly:
"[the] red/orange cast `PAKON_VENDOR_INVERT` introduces is present with
*either* tone stage — switching the tone stage does not touch it. The cast
is introduced upstream, in the front-end inversion, before the tone stage
ever runs." So the darker-but-cast tradeoff is confirmed real on this real
roll, not merely inherited from the doc's own single earlier roll.

**Owner has not chosen a side yet.** `PAKON_VENDOR_INVERT=1` was only ever
set as an env var on the bare test backend for this session's comparison —
it is **not** set by `main.js`'s normal launch, so it is already off again
on a plain relaunch. Nothing to revert.

---

## 4. The slider-revert bug: root cause found, fix half-done, uncommitted

Owner's report: "the sliders from Brightness and below don't work — you can
pull them and see a change in the preview but the moment you let them go
they revert to their defaults." Root cause, fully traced:

`merged_params()` (`pakon_render.py:182`) only keeps keys already present in
`DEFAULT_PARAMS` — `density, red, green, blue, rotate, flip_h, flip_v, crop,
rejected, ice`. `Brightness, Contrast, Saturation, Highlights, Shadows,
Sharpening` are not in that dict, so any commit carrying them is silently
dropped, keeping the server-side truth unchanged. Meanwhile
`FrameEditor.jsx`'s `onInput` for those same six sliders drives a
**client-side-only CSS filter** (`brightness(${b}%) contrast(${c}%)
saturate(${s}%)`) for live preview — so the drag *looks* live right up
until release, then reverts once the (dropped) commit round-trips and
`pending` clears back to the real, unchanged `frame.params`.

**This isn't a regression — it contradicts a convention the backend already
states for itself.** `UNAVAILABLE_CONTROLS` (`pakon_render.py:155`,
pre-existing) already lists Contrast, Saturation and (misspelled)
`"sharpen"` as *deliberately not implemented*, with real reasons (contrast
lives in the vendor's FUGC LUT selector; no saturation operator has ever
been traced in `TLB.dll`; the vendor sharpens inside Ansel, not via a
host-side unsharp mask) — and its own docstring says the UI should "show
them disabled carrying this text rather than silently omitting them or,
worse, faking them with an invented curve." **Nothing in the frontend has
ever read `unavailable_controls`** (confirmed: zero references anywhere
under `app/src`, though it's already present on every roll payload via
`roll_json()`, `pakon_app.py:2176`) — so the "disabled, with reason" design
was never wired up, and the six sliders fake exactly the thing the
convention says not to.

**What's done (uncommitted, `tools/pakon_render.py` only):**
- Added `brightness`, `highlights`, `shadows` entries to
  `UNAVAILABLE_CONTROLS` — these were never listed at all, not even as
  "unavailable." `Brightness`'s reason: it would duplicate Exposure, the
  vendor's own real brightness control; a second, unrelated curve on top of
  it would be invented processing.
- Fixed `"sharpen"` → `"sharpening"` — the list's own key didn't match what
  `params.sharpening` (and the frontend) actually use, which is worse than
  not listing it: a slider whose key doesn't match what merged_params
  expects reverts exactly the way this whole bug does, list entry or not.

**What's NOT done, still needed:** the frontend half. `AdjustmentSlider`
(`app/src/components.jsx:216`) already supports a `disabled` prop (dims to
0.45 opacity, `cursor: not-allowed`, `tabIndex -1`) — nothing new to build
there. `FrameEditor.jsx`'s six calls (lines 475–480) need to: build a lookup
from `roll.unavailable_controls` (already on every roll payload, no new
plumbing), pass `disabled` for each of the six, and show the `reason` text
near each disabled slider the way `UNAVAILABLE_CONTROLS`'s own docstring
describes. Exposure/Red/Green/Blue are real, working, vendor-authentic
controls (confirmed independent of this bug) and are not part of this fix.

**Do not commit `tools/pakon_render.py`'s current diff alone** — a backend
that now *lists* six controls as unavailable, with the frontend still
silently faking all six exactly as before, describes a state that never
actually existed. Land both halves together.

---

## 5. Next steps, in the order they were surfacing when the session ended

1. **Finish the slider fix** (§4) — the frontend half, `FrameEditor.jsx`.
   Small, well-scoped, root cause already fully diagnosed.
2. **Fix the silent-purge-on-quit bug** (§2.2) — real data loss, already
   cost one real import once. `app/main.js`'s `confirmQuit()`.
3. **Owner's open decision**: keep `PAKON_VENDOR_INVERT=1` as a working
   default (better exposure, real cast) or stay on plain default (correct
   colour, manual Exposure correction every time) — or investigate the
   cast's actual cause, flagged as real, separate, un-investigated work in
   docs/77 §5.5 itself.
4. **`pakon_tlx_raw.py` has no domain check** (§2) — a corrections-on/RPD
   file loads without complaint and would render nonsense. Worth a real
   refusal check, not attempted this session.
5. 16-bit sRGB export's Go-only tone chain (§2.4) is a known, disclosed-at-
   build-time limitation; making it follow `PAKON_COLOUR_ENGINE` would mean
   threading the real six-subsystem tone chain into Go's per-pixel loop —
   the same standing "Phase 6.2" work `CLAUDE.md` already tracks, not a new
   item.
