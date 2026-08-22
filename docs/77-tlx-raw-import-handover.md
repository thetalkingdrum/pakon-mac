# 77 — TLX raw import handover: what it is, what it assumes, and what it found

Branch: `experimental-tlx-raw-import` (4 commits on top of `main`'s
`ac1beb1`, not merged, not pushed — deliberately kept off `main` pending more
testing). This is the pickup document for that branch: what it built, which
of its own assumptions are unverified, and the real findings it produced
while being tested — most usefully, that this port's long-standing
washed-out/cast defect (`docs/74`) is visible on ordinary imported frames,
independent of anything this branch added.

---

## 1. What this branch is

A new input path: a single frame captured by the **real Kodak TLX client**
(`TLXClientDemo.exe` under Wine, e.g. via `pakon-tlx-macos`) can now be
rendered through this port's own stage-2/Ansel pipeline, in the CLI and in
the Electron app, without a Pakon scanner attached. This is not this
project's own capture format — pakon-mac's own tooling produces a raw EP
0x86 strip dump (`pakon_decode.py`); this reads something structurally
different: an already-cropped, already-geometry-corrected single frame the
vendor's own client wrote to disk.

**Format** (from `pakon-tlx-macos`'s README — no vendor code involved,
interoperability only): 16-byte header, four `uint32` LE (header size,
width, height, bit count), then whole channel planes in full, `uint16` LE,
**planar**, not interleaved. Use only a "corrections off" export (a raw
negative, ~0–11800, never full scale) — "corrections on" is already inverted
by TLB and re-inverting it produces nonsense.

**New files / entry points:**
- `tools/pakon_tlx_raw.py` — standalone CLI adapter. Reads the format,
  builds a `(lines, px_per_line, 3)` uint16 array in this port's own raw14
  domain, and calls straight into `pakon_decode.render_rpd` +
  `pakon_ansel.AnselEngine` — the same stage-2/Ansel code `pakon_decode.py
  strip` uses, not a reimplementation.
- `pakon_render.open_tlx_capture()` — builds the same single-frame `Roll`
  shape `open_capture()` builds from a `.bin`, so the app's frame list, param
  editing and export work on it unmodified. `Roll.source = "tlx_raw"` makes
  `attach()` skip this port's own `calibration/*.npy` (identity dark=0/gain=1
  instead) — see §2.
- `/api/app/open_tlx` (`pakon_app.job_open_tlx`) + the Open dialog
  auto-detecting `.raw` vs `.bin` by extension (`app/src/Dialogs.jsx`), with
  a film-base override field — see §3.
- A real, general HTTP caching bug fix in `pakon_app.py`, found while testing
  this branch but not specific to it — see §4. Worth keeping regardless of
  what happens to the rest of this branch.

---

## 2. What this branch assumes, unverified

There is no matched "corrections off"/"corrections on" pair of the same
frame in this repo to diff bit-exact against, so the following is inferred
from the `pakon-tlx-macos` README's wording, not measured — stated in the
adapter's own docstrings too, repeated here because it is the first thing to
revisit if a render looks wrong in a new way:

- **Calibration**: a "corrections off" export is assumed to already carry
  TLB's own per-pixel dark/gain correction (basic sensor read-out, not a
  client colour toggle) — so `attach()` skips this port's own calibration
  rather than stacking a second, different one on top.
- **CCD deskew**: assumed already done by the vendor client's own frame
  extraction — skipped for the same reason.
- **Orientation**: assumed to need the same 180-degree lens-inversion
  rotation `pakon_decode.py`'s own strip decoder applies
  (`ROTATE_180_FOR_LENS`) — untested against a TLX export specifically.

**What IS empirically confirmed**, not just assumed: the plane order (R/G/B),
the width/height-to-lines/pixels-per-line transpose, and the orientation
guess were all validated by rendering real files and getting coherent,
correctly-oriented photographs (a house, a mountain, recognizable
interiors/portraits) — a wrong guess on any of those would have produced a
scrambled or sideways image, not a plausible one. That is real evidence for
the *geometry*, but it is not a bit-exact vendor comparison, and it says
nothing about the calibration/deskew assumptions above.

---

## 3. The film-base override, and what it actually found

`open_tlx_capture()` measures `roll.film_base` (FindDmin) from the frame
itself, because the Go colour engine (this app's default) has no per-frame
fallback the way the Python engine's `scene_rpd12` does — it needs
`roll.film_base` populated at open time, the way `open_capture()` populates
it for a `.bin` (FindDmin over the **whole roll's** film area).

Testing surfaced a real, adapter-specific gap: a single vendor-cropped frame
can contain **no genuine clear-film pixels at all**, if the whole frame is
photographic content. Tested directly on a real frame (an interior shot with
a sunlit window): `lines_kept: 3000 of 3000, clip_pct: [0,0,0]` — nothing
looked saturated to the check, so FindDmin walked the histogram and picked
the brightest *real content* (the sunlit window) as if it were clear film,
anchoring the whole inversion on the wrong reference and rendering it
blown/washed.

**This was initially misdiagnosed** — see §5. The manual film-base override
(`film_base` param on `open_tlx_capture`/`/api/app/open_tlx`, a UI field in
the Open dialog for `.raw` imports) exists because of this finding, but a
direct A/B test (a drastically different override, 1300/1300/1300 vs. the
measured 1576/2013/2495) barely changed the render — so **whatever this
particular frame's problem was, film_base was not the dominant term**. The
override is still real, still correct to have, and may matter more on a
different frame, but do not assume it is *the* fix for a washed-out TLX
render — check §5 first.

---

## 4. The caching bug (general, not TLX-specific)

Frame images were served `Cache-Control: public, max-age=31536000,
immutable`. False: a rendered frame's pixels also depend on this process's
`PAKON_*` environment (colour engine, real-autotone, vendor-invert, and
others), which the URL never encodes, and a roll resumes with the **same
id** across backend restarts — so switching `PAKON_COLOUR_ENGINE`/
`PAKON_REAL_AUTOTONE` and relaunching produced *zero visible change*, not
because the fix didn't work, but because the browser's disk cache served
the old bytes for the identical URL without ever asking the backend.

Fixed by `render_env_fingerprint()` (hashes the whole `PAKON_*` environment,
not a hardcoded flag list — a future flag is covered automatically) folded
into the server-side cache key and an `ETag`, with `Cache-Control: no-cache`
(always revalidate, cheap `304` when nothing changed) replacing the false
"immutable" claim. Verified directly, not just reasoned about: same URL
against two different backend launches gave two different ETags and two
different SHA1s of the response body; the same URL against an unchanged
backend gave `304`.

**If you are debugging "I changed a setting and nothing happened" anywhere
in this app, and the roll was resumed rather than freshly opened, this class
of bug is the first thing to suspect** — check whether the response actually
came from the backend (a `200` with a new `ETag`) or the browser's cache (a
`304`, or no request at all in the network log).

---

## 5. The real finding: this port's washed-out/cast defect, seen fresh

This is the part worth reading even if the TLX branch itself never lands.

Testing this adapter against real TLX-captured frames reproduced this
project's own long-standing, already-tracked defect (`docs/74`) — and, on
one frame, made the misdiagnosis risk concrete enough to be worth recording
as a lesson, not just a data point.

**The sequence:**
1. A frame with an interior room + a blown-white sunlit window rendered with
   the window solid white, zero detail. First hypothesis: FindDmin
   mis-anchoring on the window (§3). Plausible-looking, matched the window
   stats exactly — and **wrong**, disproven by directly testing it (a
   drastically different film_base barely moved the output).
2. The actual fix: re-rendering through `PAKON_COLOUR_ENGINE=python
   PAKON_REAL_AUTOTONE=1` (the real, Unicorn-verified six-subsystem
   `analyzeAutoTone` chain, not the "Shasta two-anchor" stand-in) recovered
   real detail in the window — a photographer's silhouette that the
   stand-in had clipped to pure white. The raw data was never clipped at
   capture; the **stand-in's percentile-based black/metricGray anchoring**
   handles this frame's bimodal, high-dynamic-range histogram badly. Same
   defect `docs/74` already tracks, just far more visible on this frame's
   shape than a typical exposure.
3. **The Go colour engine (this app's actual default) has zero wiring for
   the real chain.** `pakon_render._go_request` never sets `req.OutToneLut`,
   so Go always uses the stand-in regardless of `PAKON_REAL_AUTOTONE`. That
   env var only affects the deprecated Python engine. Getting the real chain
   today requires `PAKON_COLOUR_ENGINE=python PAKON_REAL_AUTOTONE=1`
   together — there is no way to reach it from the app's default
   configuration. Wiring `OutToneLut` into the Go request is real,
   pre-existing, separate outstanding work (`docs/74`'s "Phase 6.2"), not
   something this branch did or should try to finish.
4. Tested next on **conventional, non-extreme frames** from two different
   rolls (an evenly-lit interior, a portrait) — the real chain still reads
   flat/washed/high-key, just less catastrophically than the stand-in.
   `docs/74` §202 already states why: swapping the tone chain closes only
   ~40% of the total error; there is a separate, still-unsolved per-frame
   colour-cast term (δ) on top of it.
5. Tried `PAKON_VENDOR_INVERT=1` stacked with the real chain, hoping to
   combine its better contrast with the real chain's better colour balance.
   **It does not compose that way.** The red/orange cast `PAKON_VENDOR_INVERT`
   introduces is present with *either* tone stage — switching the tone
   stage does not touch it. The cast is introduced upstream, in the
   front-end inversion, before the tone stage ever runs. This is a clean,
   reproducible finding (all 10 engine/tone/invert/ICC combinations were
   rendered and compared on the same frame): **contrast and colour balance
   are not independent knobs across these two flags**, and getting both
   would need investigating why `PAKON_VENDOR_INVERT` casts red — real,
   separate work, not attempted here.

**How to apply:** don't re-run this exact investigation from scratch. Before
attributing a washed-out or cast TLX (or any) render to something
TLX-specific, check whether `docs/74`'s already-tracked defect explains it
first — it is the default explanation now, not an exotic one. And per
[[feedback-verify-before-diagnosing]] (session memory, not a doc): confirm a
diagnosis by actually perturbing the suspected variable and checking the
output moves, before presenting it as the cause. The film-base
misdiagnosis in §3 is the concrete example of why.

---

## 6. Status / next steps

- **Not merged to `main` on purpose.** The branch works and is internally
  consistent, but two of its core assumptions (calibration, deskew) are
  still unverified, and testing it immediately surfaced that the app's
  actual colour output is dominated by the pre-existing tone-chain defect,
  not anything this branch controls. Land it once there's an appetite to
  either verify those assumptions against a real matched vendor pair, or to
  explicitly accept them as "best effort, stated plainly" the way the rest
  of this port states its open items.
- **If picking this up again:** a matched "corrections off" + "corrections
  on" TLX export of the *same* frame would settle the calibration/deskew
  assumptions in §2 directly (diff the corrections-on export against this
  port's own render of the corrections-off one, post-inversion). None
  existed in the files tested against here.
- **Independent of this branch:** the Go `OutToneLut` wiring (§5.3) and the
  `PAKON_VENDOR_INVERT` cast (§5.5) are both real, standing, separately
  worth investigating pieces of `docs/74`'s main line of work, surfaced here
  but not caused by this branch and not fixed by it.
