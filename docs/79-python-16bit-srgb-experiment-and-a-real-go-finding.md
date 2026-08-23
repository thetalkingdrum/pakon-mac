# 79 — EXPERIMENTAL Python 16-bit sRGB export, a real bottleneck confirmed in Go's shipped path, and the fix landed in Go the same session

§1–§5 (the Python investigation that found and diagnosed the bug) happened
on branch `experimental-tlx-raw-import`, commit `8cef5e0` — still
EXPERIMENTAL and Python-only, and stays on that branch only, per `docs/62
§12`.

§6 (what actually shipped) lives on its own branch, `go-16bit-srgb-export`,
based on `main` — deliberately NOT on `experimental-tlx-raw-import`, so
this real Go fix isn't tangled with that branch's TLX-import-specific work
or the Python experiment above it. That branch is `main` +
`8d511be` (the base 16-bit export feature this fixes, itself not
TLX-specific — see docs/78 §1) + this commit. Local only, not pushed to any
remote by the owner's own explicit choice.

**Status: §3's bug is fixed in the real product path, same session.** §6 is
real code in `tools/ansel/pipeline`, the same Go colour pipeline `docs/62
§12` commits to — a mergeable, self-contained branch on its own.

---

## 1. The question

Following up on `docs/78`'s §2.4/§5.5: the app's real 16-bit sRGB export
(`colour="srgb16"`, Go-only, `kcmsclut.EvalU16`/`RenderRequest.Want16`)
can't follow `PAKON_COLOUR_ENGINE` — it always renders through Go's
`ShastaToneRpd` tone stand-in, even on a backend configured for the real
tone chain everywhere else. Properly fixing that means threading Go's own
tone chain into `main.go`'s per-pixel loop — "Phase 6.2", the standing item
`CLAUDE.md` already tracks, sized as real work, not something to start
casually.

The owner's question: **is a Python-side 16-bit export even possible at
all**, sidestepping Phase 6.2 entirely (Python already has the real tone
chain wired via `PAKON_REAL_AUTOTONE=1`)? Explicitly framed as an
experiment — "even if the docs say not to" (`docs/62 §12` commits the
colour pipeline to Go; `_render_colour_python`'s own docstring says that
engine "must not gain features") — not a decision to un-deprecate the
Python engine, and not intended to leave this branch.

## 2. What was built (Part 1) — a working Python 16-bit path

- `pakon_kcms_clut.evaluate16()` — a transcription of Go's
  `kcmsclut.EvalU16` (same tetrahedral CLUT interpolation as the existing
  8-bit `evaluate()`; only the final step differs — blends toward the next
  real captured `otab` byte using the low 14 bits `t>>14` discards, instead
  of floor-snapping).
- Verified **bit-exact against the Go port over 2,000,000 random u8
  triples** — a temporary `--stream16` mode was added to
  `tools/ansel/pipeline/cmd/kcmsdump` for the cross-check and reverted
  immediately after; not part of the committed diff.
- Wired in as `AnselEngine.to_srgb(depth=16)` and a new
  `_render_colour_python16()`, reachable only via `export_frame`'s new
  `colour="srgb16py"` — never touches the interactive preview path.

Exported a real frame from a real 40-frame TLX roll end to end: valid TIFF,
real photographic content, no visible defect on first look.

## 3. The real finding — traced to a bottleneck that also exists in Go's shipped code

Opened the export in Photoshop. Its histogram was a hard comb — tall
spikes with gaps, not a continuum — on all three channels. Traced, not
guessed:

**`rpd12_to_icc_u8` is the only 8-bit quantization in the whole pipeline**
(confirmed by grep — `astype(np.uint8)` appears exactly once in
`pakon_ansel.py`). The tone stage runs RPD12 (`SHASTA_MAX = 4095`)
throughout; this one function rounds RPD12 down to u8 (`round(rpd12 *
255/4095)`) **before the CLUT ever runs**, and `evaluate16`'s blend can't
recover precision that's already gone by the time it sees the value.

Measured on one real frame (`18.raw`, frame 0, `PAKON_VENDOR_INVERT=1`):

| stage | R | G | B |
|---|---|---|---|
| toned RPD12, distinct values | 1608 | 1488 | 1402 |
| u8 CLUT input, distinct values | 105 | 96 | 91 |
| `evaluate16` output, distinct values | 1503 | 1923 | 2451 |

98%+ of pixels landed **exactly** on a `byte*257` grid point — i.e. almost
no interpolation happened at all; `evaluate16` was mostly just replaying
the 8-bit result at a wider scale.

**This is not Python-specific.** Go's real, shipped `Rpd12ToSrgb16`
(`tools/ansel/pipeline/kcmsclut/kcmsclut.go:224`) does the identical thing:

```go
func Rpd12ToSrgb16(rpd [3]int) [3]uint16 {
	return EvalU16([3]uint8{
		Rpd12ToU8(rpd[0]), Rpd12ToU8(rpd[1]), Rpd12ToU8(rpd[2]),
	})
}
```

**Confirmed directly, not inferred**: the same real toned RPD12 array from
this frame was run through Go's actual compiled `Rpd12ToSrgb16` (a
temporary `--stream-rpd16` mode added to `cmd/kcmsdump`, reverted after —
not part of the committed diff). Result: **1503 / 1923 / 2451 distinct
codes — bit-for-bit identical to Python's pre-fix numbers above.** The
app's real "sRGB · 16-bit" export has this exact banding problem today.
Nobody had checked it against a real histogram before this session.

## 4. The fix (Part 2) — reconstructing the input side, not just blending the output side

`pakon_kcms_clut.build_fine_idx()` / `evaluate_fine16()`:

- `idx[c]` (the vendor's own captured `grid+0x8c` table, 256 real
  `(offset, weight)` samples per channel — the *only* thing standing
  between a u8 input and a CLUT grid position) is a genuinely **nonlinear**
  function of the u8 input — confirmed by checking, not assumed: neither
  `v*gridN/255` nor `v*gridN/256` reproduces the real captured table (30/256
  and 29/256 cell matches respectively, out of 256, on the R channel).
- `build_fine_idx()` reconstructs that same function at full RPD12
  resolution (4096 points instead of 256) via **piecewise-linear**
  interpolation (`np.interp`) through the 256 known-correct real samples.
  Linear, not a smoother fit, specifically because it cannot overshoot past
  the two real samples bracketing any reconstructed point — it can only
  interpolate between known-correct vendor behaviour, never invent an
  excursion past it.
- `evaluate_fine16()` feeds the un-rounded RPD12 value straight into this
  finer index, skipping `rpd12_to_icc_u8` entirely. Everything downstream
  (tetrahedron selection, CLUT lookup, `otab` output blend) is unchanged —
  reuses `evaluate16`'s own already-verified machinery via a shared
  `_eval_chunk16(flat, t, idx=...)`.

**Explicitly NOT bit-exact to anything the real vendor ever computed** —
there is no ground truth finer than 256 samples to be exact against. It is
a principled reconstruction anchored to real vendor samples, same spirit as
`evaluate16` itself, one level further from the vendor's own ground truth.

Measured, same frame:

| | R | G | B |
|---|---|---|---|
| `evaluate16` (old), distinct | 1503 | 1923 | 2451 |
| `evaluate_fine16` (new), distinct | 39,101 | 43,411 | 45,451 |
| factor | 26.0x | 22.6x | 18.5x |

Now **exceeds** the source RPD12's own distinct-value count (1608/1488/1402)
— not a contradiction: the CLUT is a 3D transform, not three independent
per-channel curves, so output distinctness is bounded by distinct
`(R,G,B)` *input triples*, not by any one channel's marginal range.
Confirmed directly: 5,382,113 distinct triples out of 6,000,000 pixels on
this frame, vs. 1608/1488/1402 distinct values per channel alone.

Visually identical to the pre-fix export — same photo, same tones, no
colour shift — and **owner-confirmed in Photoshop**: no more banding.

Wired in as `export_frame`'s `colour="srgb16pyfine"`.

## 5. What's actually transferable to the product

Everything in §2 (the Python 16-bit path itself) is explicitly experimental
and stays off this branch, per `docs/62 §12` — not a candidate for the
product regardless of outcome.

**§3 and §4 are not experimental and are not Python-specific.** Two real,
separable pieces of follow-up work for the Go path, in priority order:

1. **Go's shipped `colour="srgb16"` export bands on real photographs
   today** (§3) — this alone is worth fixing or at minimum disclosing more
   clearly than the current docstring does; a user exporting a real roll
   and opening it in Photoshop will see exactly what triggered this
   investigation.
2. **The fix is directly portable**: `build_fine_idx`/`evaluate_fine16`'s
   approach is ordinary integer/float math over tables Go already has
   embedded (`idxOff`, `idxWeight`, `clut`, `otab` in `kcmsclut.go`) — the
   hard part (finding the real bottleneck, discovering the input curve is
   nonlinear, choosing and verifying piecewise-linear reconstruction) is
   already done. Unlike everything else in this doc, landing it does NOT
   violate any stated architecture rule: Go is exactly where 16-bit export
   is supposed to live. **Done, same session — see §6.**

Evidence tier for both `evaluate16`(Go-verified bit-exact, 2M samples) and
the Go product-path confirmation in §3 (real compiled `Rpd12ToSrgb16`, real
frame data, direct measurement): **Tier 4, empirical**, per `CLAUDE.md`'s
hierarchy — strong for "this bug exists and this fix works", not a
bit-exactness claim about anything.

---

## 6. Landed in the real Go pipeline, same session

`kcmsclut.go`: `EvalU16` refactored (behaviour-preserving — `go test
./kcmsclut/...` passed unchanged before and after) to extract
`evalU16Core(offR, wR, offG, wG, offB, wB int32)`, the tetrahedron
selection / CLUT lookup / `otab` blend with the input-table lookup pulled
out. Added `buildFineIdx()` (a direct transcription of Python's
`build_fine_idx` — same piecewise-linear reconstruction, `sync.Once`-cached
`[3][4096]int32` pair) and `Rpd12ToSrgb16Fine(rpd [3]int) [3]uint16`, which
calls `evalU16Core` with `buildFineIdx`'s table instead of
`Rpd12ToU8`+`idxOff`/`idxWeight`.

**Verified bit-exact against Python's `evaluate_fine16` over 500,000
random RPD12 triples** (a temporary `--stream-rpd16fine` mode added to
`cmd/kcmsdump`, reverted after — not part of the committed diff).

`main.go`'s `Want16` call site (the actual per-pixel loop every real 16-bit
export runs through) now calls `kcmsclut.Rpd12ToSrgb16Fine` instead of
`kcmsclut.Rpd12ToSrgb16`. `Rpd12ToSrgb16` itself is untouched and still
exported (kept for the Go test suite / anyone diffing against the old
behaviour), it is simply no longer what the render path calls.

Rebuilt via `tools/build-native.sh` (the only sanctioned way to produce
`libpakon_colour_go.dylib` — see that script's own docs/62 §5.2 citation
for why a hand-run `go build` is refused) and **confirmed on the real
product path**, not just the Go unit tests: `export_frame(...,
colour="srgb16")` — default engine, no experimental flags, the exact code
path a real export button-press runs — on the same real frame, before vs.
after:

| | R | G | B |
|---|---|---|---|
| `srgb16`, pre-fix | 1503 | 1923 | 2451 |
| `srgb16`, post-fix | 33,687 | 36,449 | 37,157 |

(Numbers differ slightly from §4's Python figures — this ran through Go's
own tone chain, not Python's `PAKON_REAL_AUTOTONE` chain — but the same
order-of-magnitude fix, confirmed on the actual code path that ships.)

Full regression suite (`pakon_gate.py selftest`, `test_calib.py`,
`test_render_f135.py`, `go test ./...`) passed after the refactor and again
after the rebuild. `app/src/ExportModal.jsx`'s "sRGB · 16-bit" disclosure
text updated to point here instead of claiming a plain blend.

**What did NOT change**: `Rpd12ToSrgb8` / the default 8-bit export path —
`Rpd12ToU8` still feeds `EvalU8` exactly as before. This fix is scoped to
`Want16` only, per its own reasoning: 16-bit output was already disclosed
as "not vendor-verified above 8 bits, offered for grading headroom", and
this fix stays entirely within that disclosed limitation — it just makes
the reconstruction less lossy, it does not change what is or isn't
vendor-verified.
