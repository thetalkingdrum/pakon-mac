#!/usr/bin/env python3
"""Port of the Kodak CMM's 3-D CLUT interpolator -- ``kodakcms.dll``
``fcn.10018160``, md5 ``e4c8064a9dd3c3a5541d74b00a730e53``.

WHY THIS EXISTS
===============
docs/74 §171 drove the vendor's own CMM under Wine and found this port's
PIL/littleCMS ICC step **not** bit-exact: systematically darker by mean 2.739
sRGB codes with default flags, 1.836 with ``cmsFLAGS_NOOPTIMIZE``. §171.2
ruled out the intent, the profile pair, the data type, ``colorSpaceMax`` and
PCS quantisation, and showed the residual is not a per-channel remap -- it is
a 3-D CLUT interpolation difference. This module closes that by running the
vendor's own interpolation arithmetic instead of lcms's.

THE ROUTINE
===========
``SpEvaluate`` (0x1002ecf0) -> ``PTEvalDT`` (0x10041070) -> fcn.100410a0 ->
fcn.10026d20 -> fcn.10012b30 -> fcn.10012bc0.  fcn.10012bc0 is a pure
dispatcher returning one of 35 evaluator function pointers; the tile loop
fcn.10027410 calls it as ``call dword [ebx + 4]``.

Which one is live was settled **dynamically**, not from naming or proximity:
``kcms_clut_host.exe`` with ``POKE_RVA`` overwrites a candidate's first byte
with ``0xC3`` and re-runs the whole transform.  Exactly one of the 35 changes
SpEvaluate's output -- ``0x10018160`` -- and it sits on the dispatcher's
in=3/out=3 u8 leaf (0x10012f35).

Its whole body (``af``+``pdf``, 799 bytes, no calls) is, per pixel:

    offR, wR = idx[0][r]        # 8-byte records, byte offset + weight
    offG, wG = idx[1][g]
    offB, wB = idx[2][b]
    base = offR + offG + offB

    sort {wR, wG, wB} descending -> (w0, w1, w2), which selects one of six
    tetrahedra and with it two intermediate corner byte offsets (Pa, Pb)

    for ch in 0, 1, 2:
        c = base + 2*ch
        A = clut[c];  C = clut[c + Pa];  B = clut[c + Pb];  D = clut[c + RGB]
        t = (D - B)*w2 + (C - A)*w0 + (B - C)*w1      # signed 32-bit
        out[ch] = otab[ch][ 4*A + (t >> 14) ]         # SAR, i.e. floor

so:

* **Tetrahedral**, not trilinear -- the classic sorted-increment form
  ``A + w0*(C-A) + w1*(B-C) + w2*(D-B)``.
* The grid index and the fraction are NOT computed per pixel. They are read
  out of a 3 x 256 precomputed table (grid+0x8c) that also absorbs any input
  curve. Weights are 16-bit-ish, 0..65535 -- ``idx[c][255].weight`` is 65535,
  **not** 65536, so the top of the input range never reaches the last grid
  node.
* Interpolation happens at **14-bit** precision (``4*A`` plus a ``>>14`` of
  the weighted differences), with an arithmetic shift and therefore
  truncation toward -inf, not round-to-nearest.
* The 14-bit result is then mapped to u8 through a per-channel 16384-entry
  byte table (grid+0x154), so the output transfer curve is exact, not
  interpolated.

The tables themselves are vendor data built by ``SpCombineXforms`` at run
time from ``Rpd2Pcs_HR200_QS_v5s10.pf`` (md5 c1d4f3bba8f06f3427ccfaff5c30b559)
and ``Srgb_v2.pf`` (md5 95bd003685a81450184af6aaf1d0e31c). No closed form is
attempted for them; as with §175's inversion table, byte-exactness comes from
shipping the table. They are captured by the detour in
``tools/re/live_hooks/wine_host/kcms_clut_host.c`` and stored in
``vendor_kcms_rpd2srgb.npz`` next to this file.

STATUS
======
Bit-exact against the real routine over the **entire** u8 RGB input domain --
all 16,777,216 triples, 50,331,648 channel samples, zero differences. See
``pakon_kcms_clut_golden.py``.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

#: Vendor tables captured from the live combined xform.
TABLE_PATH = Path(__file__).resolve().parent / "vendor_kcms_rpd2srgb.npz"

#: Set False to fall back to lcms (e.g. if the table file is absent).
KCMS_CLUT_PORTED = True

_CACHE: dict[str, object] | None = None


# --------------------------------------------------------------------------
# table loading / packing
# --------------------------------------------------------------------------
def pack(dump_dir: str | Path, out: str | Path = TABLE_PATH) -> Path:
    """Pack a ``kcms_clut_host.exe DUMP_DIR`` capture into the npz."""
    d = Path(dump_dir)
    meta: dict[str, int] = {}
    for line in (d / "grid_meta.txt").read_text().splitlines():
        p = line.split()
        if len(p) == 2:
            meta[p[0]] = int(p[1])
    idx = np.frombuffer((d / "idxtab.bin").read_bytes(), dtype="<i4")
    idx = idx.reshape(3, 256, 2).copy()
    clut = np.frombuffer((d / "clut.bin").read_bytes(), dtype="<u2").copy()
    otab = np.frombuffer((d / "otab.bin").read_bytes(), dtype=np.uint8)
    otab = otab.reshape(3, 0x4000).copy()
    corners = np.array([meta["offB"], meta["offG"], meta["offGB"],
                        meta["offR"], meta["offRB"], meta["offRG"],
                        meta["offRGB"]], dtype=np.int64)
    out = Path(out)
    np.savez_compressed(out, idx=idx, clut=clut, otab=otab, corners=corners,
                        gridN=np.int64(meta["gridN"]))
    return out


def tables() -> dict:
    """Load (and cache) the vendor tables."""
    global _CACHE
    if _CACHE is None:
        z = np.load(TABLE_PATH)
        c = z["corners"].astype(np.int64)
        _CACHE = {
            "idx": z["idx"].astype(np.int64),
            "clut": z["clut"].astype(np.int64),
            "otab": z["otab"],
            "offB": int(c[0]), "offG": int(c[1]), "offGB": int(c[2]),
            "offR": int(c[3]), "offRB": int(c[4]), "offRG": int(c[5]),
            "offRGB": int(c[6]), "gridN": int(z["gridN"]),
        }
    return _CACHE


def available() -> bool:
    return KCMS_CLUT_PORTED and TABLE_PATH.is_file()


# --------------------------------------------------------------------------
# the evaluator
# --------------------------------------------------------------------------
#: pixels per chunk -- the intermediates are ~15 int64 planes, so a whole
#: 16.7 M-pixel exhaustive sweep would need several GB in one go.
CHUNK = 1 << 21


def evaluate(rgb_u8: np.ndarray, t: dict | None = None) -> np.ndarray:
    """``fcn.10018160`` on interleaved RGB u8. Shape (..., 3) in and out."""
    if t is None:
        t = tables()
    a = np.asarray(rgb_u8, dtype=np.uint8)
    shape = a.shape
    flat = a.reshape(-1, 3)
    if flat.shape[0] > CHUNK:
        out = np.empty_like(flat)
        for i in range(0, flat.shape[0], CHUNK):
            out[i:i + CHUNK] = _eval_chunk(flat[i:i + CHUNK], t)
        return out.reshape(shape)
    return _eval_chunk(flat, t).reshape(shape)


def _eval_chunk(flat: np.ndarray, t: dict) -> np.ndarray:
    n = flat.shape[0]

    idx, clut, otab = t["idx"], t["clut"], t["otab"]
    oB, oG, oGB = t["offB"], t["offG"], t["offGB"]
    oR, oRB, oRG = t["offR"], t["offRB"], t["offRG"]
    oRGB = t["offRGB"]

    r = flat[:, 0].astype(np.int64)
    g = flat[:, 1].astype(np.int64)
    b = flat[:, 2].astype(np.int64)

    base = idx[0, r, 0] + idx[1, g, 0] + idx[2, b, 0]      # byte offset
    fr, fg, fb = idx[0, r, 1], idx[1, g, 1], idx[2, b, 1]

    # the disassembly's three signed compares, in its own order:
    #   0x100182a4 cmp fr,fg / 0x100182ac cmp fg,fb / 0x100182c9 cmp fr,fb
    rg = fr > fg
    gb = fg > fb
    rb = fr > fb

    w0 = np.empty(n, np.int64)
    w1 = np.empty(n, np.int64)
    w2 = np.empty(n, np.int64)
    Pa = np.empty(n, np.int64)
    Pb = np.empty(n, np.int64)

    for mask, (a0, a1, a2, pa, pb) in (
            (rg & gb,        (fr, fg, fb, oR, oRG)),   # fr > fg > fb
            (rg & ~gb & rb,  (fr, fb, fg, oR, oRB)),   # fr > fb >= fg
            (rg & ~gb & ~rb, (fb, fr, fg, oB, oRB)),   # fb >= fr > fg
            (~rg & gb & ~rb, (fg, fb, fr, oG, oGB)),   # fg > fb >= fr
            (~rg & gb & rb,  (fg, fr, fb, oG, oRG)),   # fg >= fr > fb
            (~rg & ~gb,      (fb, fg, fr, oB, oGB))):  # fb >= fg >= fr
        w0[mask] = a0[mask]
        w1[mask] = a1[mask]
        w2[mask] = a2[mask]
        Pa[mask] = pa
        Pb[mask] = pb

    out = np.empty((n, 3), np.uint8)
    for ch in range(3):
        c = base + 2 * ch
        A = clut[c >> 1]
        C = clut[(c + Pa) >> 1]
        B = clut[(c + Pb) >> 1]
        D = clut[(c + oRGB) >> 1]
        tt = (D - B) * w2 + (C - A) * w0 + (B - C) * w1
        tt = ((tt + 2 ** 31) % 2 ** 32) - 2 ** 31          # 32-bit signed wrap
        out[:, ch] = otab[ch][4 * A + (tt >> 14)]          # SAR 14 == floor
    return out


def evaluate16(rgb_u8: np.ndarray, t: dict | None = None) -> np.ndarray:
    """EXPERIMENTAL, Python-only. ``fcn.10018160``'s 16-bit-output
    counterpart -- a transcription of ``kcmsclut.EvalU16`` (Go,
    ``tools/ansel/pipeline/kcmsclut/kcmsclut.go``), which see for the full
    derivation. Same tetrahedron selection, same CLUT interpolation as
    ``evaluate`` above; only the final step differs: instead of
    ``otab[ch][4*A + (t>>14)]`` (floor-snap to one of otab's 16384 real
    captured bytes), this blends toward the NEXT real sample using the low
    14 bits ``t>>14`` discards (``t & 0x3fff``), landing on
    ``byte*257 + fractional interpolation`` -- exact at every real vendor
    sample point, interpolated between them.

    Bit-for-bit identical by construction to the Go port for the same input:
    both read the identical packed tables (``tools/gen_kcms_clut_tables.py``'s
    npz) and run the identical integer arithmetic -- confirmed empirically
    too, not just assumed: ``tools/ansel/pipeline/cmd/kcmsdump``'s
    (uncommitted, experimental) ``--stream16`` mode reproduces this
    exactly over 2,000,000 random triples, zero differences.

    NOT independently vendor-verified above 8 bits (see the Go docstring)
    and NOT part of this project's stated architecture: docs/62 §12 commits
    the colour pipeline to Go, and ``_render_colour_python`` in
    pakon_render.py must not gain features. This exists purely to answer
    "is it possible", per the owner's own framing -- it is deliberately NOT
    wired into any render path, and it must not be treated as the app's
    16-bit export without a real decision to un-deprecate the Python engine
    first.
    """
    if t is None:
        t = tables()
    a = np.asarray(rgb_u8, dtype=np.uint8)
    shape = a.shape
    flat = a.reshape(-1, 3)
    if flat.shape[0] > CHUNK:
        out = np.empty((flat.shape[0], 3), np.uint16)
        for i in range(0, flat.shape[0], CHUNK):
            out[i:i + CHUNK] = _eval_chunk16(flat[i:i + CHUNK], t)
        return out.reshape(shape)
    return _eval_chunk16(flat, t).reshape(shape)


def _eval_chunk16(flat: np.ndarray, t: dict, idx: np.ndarray | None = None) -> np.ndarray:
    """``idx`` overrides ``t["idx"]`` -- the only hook ``evaluate_fine16``
    (below) needs: same tetrahedron/CLUT/output-blend math, a
    finer-resolution index table."""
    n = flat.shape[0]

    if idx is None:
        idx = t["idx"]
    clut, otab = t["clut"], t["otab"]
    oB, oG, oGB = t["offB"], t["offG"], t["offGB"]
    oR, oRB, oRG = t["offR"], t["offRB"], t["offRG"]
    oRGB = t["offRGB"]

    r = flat[:, 0].astype(np.int64)
    g = flat[:, 1].astype(np.int64)
    b = flat[:, 2].astype(np.int64)

    base = idx[0, r, 0] + idx[1, g, 0] + idx[2, b, 0]
    fr, fg, fb = idx[0, r, 1], idx[1, g, 1], idx[2, b, 1]

    rg = fr > fg
    gb = fg > fb
    rb = fr > fb

    w0 = np.empty(n, np.int64)
    w1 = np.empty(n, np.int64)
    w2 = np.empty(n, np.int64)
    Pa = np.empty(n, np.int64)
    Pb = np.empty(n, np.int64)

    for mask, (a0, a1, a2, pa, pb) in (
            (rg & gb,        (fr, fg, fb, oR, oRG)),
            (rg & ~gb & rb,  (fr, fb, fg, oR, oRB)),
            (rg & ~gb & ~rb, (fb, fr, fg, oB, oRB)),
            (~rg & gb & ~rb, (fg, fb, fr, oG, oGB)),
            (~rg & gb & rb,  (fg, fr, fb, oG, oRG)),
            (~rg & ~gb,      (fb, fg, fr, oB, oGB))):
        w0[mask] = a0[mask]
        w1[mask] = a1[mask]
        w2[mask] = a2[mask]
        Pa[mask] = pa
        Pb[mask] = pb

    out = np.empty((n, 3), np.uint16)
    otab_len = otab.shape[1]
    for ch in range(3):
        c = base + 2 * ch
        A = clut[c >> 1]
        C = clut[(c + Pa) >> 1]
        B = clut[(c + Pb) >> 1]
        D = clut[(c + oRGB) >> 1]
        tt = (D - B) * w2 + (C - A) * w0 + (B - C) * w1
        tt = ((tt + 2 ** 31) % 2 ** 32) - 2 ** 31          # 32-bit signed wrap
        idxo = 4 * A + (tt >> 14)
        frac = tt & 0x3fff                                 # bits t>>14 discards
        lo = otab[ch][idxo].astype(np.int64)
        # idx+1 out of range only at otab's own top edge, where lo is already
        # 255 -- clamping the lookup index (hi := lo there) matches Go's own
        # `if idx+1 < len(otab[ch])` guard exactly.
        hi = otab[ch][np.minimum(idxo + 1, otab_len - 1)].astype(np.int64)
        blended = lo * 257 + (hi - lo) * 257 * frac // 16384
        out[:, ch] = blended.astype(np.uint16)
    return out


# --------------------------------------------------------------------------
# EXPERIMENTAL: finer-than-u8 input, for rebuilding the pipeline's one real
# 8-bit bottleneck (docs/78 §6). See build_fine_idx and evaluate_fine16.
# --------------------------------------------------------------------------

def build_fine_idx(t: dict | None = None, rpd_max: int = 4095) -> np.ndarray:
    """EXPERIMENTAL. A ``(3, rpd_max+1, 2)`` generalization of ``idx``
    (``(3, 256, 2)``) -- same units (byte offset, weight 0..65535), reindexed
    by RPD12 code (0..``rpd_max``) instead of by u8.

    WHY THIS EXISTS. ``rpd12_to_icc_u8`` throws away precision BEFORE the
    CLUT ever runs: measured on a real frame, an RPD12 range that could hold
    up to 4096 distinct codes collapsed to ~100 distinct u8 inputs (docs/78
    §6). ``evaluate16``'s output-side blend (interpolating between adjacent
    ``otab`` bytes) cannot recover that -- it was already gone by the time
    ``evaluate16`` sees the value. This closes the INPUT side instead.

    HOW. ``idx[c]``'s 256 real entries are ``kodakcms.dll``'s own precomputed
    (grid cell, sub-cell weight) for each of the 256 legal u8 inputs -- i.e.
    256 real, verified samples of a continuous function
    ``u8_input -> grid_position`` (``grid_position = offset/axis_step +
    weight/65536``, a float in ``[0, gridN-1]``). This reconstructs that
    same function at ``rpd_max+1`` points via ``np.interp`` (piecewise
    LINEAR, not a smoother fit) over the identical ``0..255`` domain the
    real samples span, evaluated at the RPD12 grid instead of the u8 one
    (``rpd12_to_icc_u8``'s own real scale, ``255/4095`` -- the vendor's
    profile's own ``colorSpaceMax`` relationship -- just not rounded to an
    integer first).

    NOT bit-exact to anything the real vendor ever computed: there is no
    ground truth finer than 256 samples to be exact against, and the real
    ``kcms_idx`` table may encode a genuinely nonlinear input curve this
    linear reconstruction only approximates BETWEEN its 256 real anchor
    points (confirmed nonlinear: neither ``v*gridN/255`` nor ``v*gridN/256``
    reproduces the real table -- see docs/78 §6's derivation). Piecewise
    LINEAR was chosen specifically because it cannot overshoot past the two
    real samples bracketing any reconstructed point, unlike a cubic/spline
    fit -- it can only interpolate between known-correct behaviour, never
    invent an excursion past it. At every real u8 sample point itself
    (``rpd12 * 255/4095`` landing exactly on an integer), the reconstruction
    reproduces that exact real sample: ``np.interp`` passes through its own
    anchors exactly.
    """
    if t is None:
        t = tables()
    idx = t["idx"]
    gridN = int(t["gridN"])
    steps = (int(t["offR"]), int(t["offG"]), int(t["offB"]))
    v_real = np.arange(256, dtype=np.float64)
    v_fine = np.arange(rpd_max + 1, dtype=np.float64) * (255.0 / rpd_max)

    out = np.empty((3, rpd_max + 1, 2), dtype=np.int64)
    for c in range(3):
        step = steps[c]
        pos_real = idx[c, :, 0].astype(np.float64) / step + idx[c, :, 1].astype(np.float64) / 65536.0
        pos_fine = np.interp(v_fine, v_real, pos_real)
        cell = np.clip(np.floor(pos_fine).astype(np.int64), 0, gridN - 2)
        frac = pos_fine - cell
        weight = np.clip(np.round(frac * 65536.0), 0, 65535).astype(np.int64)
        out[c, :, 0] = cell * step
        out[c, :, 1] = weight
    return out


def evaluate_fine16(rpd12: np.ndarray, t: dict | None = None,
                    fine_idx: np.ndarray | None = None,
                    rpd_max: int = 4095) -> np.ndarray:
    """EXPERIMENTAL. RPD12 (0..``rpd_max``) -> 16-bit sRGB directly, using
    ``build_fine_idx`` in place of the real ``idx`` table -- i.e.
    ``rpd12_to_icc_u8``'s rounding step never happens; every distinct RPD12
    code gets its own CLUT position instead of ~16 of them sharing one u8
    bucket. Everything downstream (tetrahedron selection, CLUT lookup,
    otab output blend) is identical to ``evaluate16``.

    Pass a precomputed ``fine_idx`` (``build_fine_idx()``, once) when
    calling this per-frame -- rebuilding it is a 4096-point ``np.interp``
    per channel, cheap but pointless to repeat for every frame of a roll.
    """
    if t is None:
        t = tables()
    if fine_idx is None:
        fine_idx = build_fine_idx(t, rpd_max=rpd_max)
    a = np.clip(np.rint(np.asarray(rpd12, dtype=np.float64)), 0, rpd_max).astype(np.int64)
    shape = a.shape
    flat = a.reshape(-1, 3)
    if flat.shape[0] > CHUNK:
        out = np.empty((flat.shape[0], 3), np.uint16)
        for i in range(0, flat.shape[0], CHUNK):
            out[i:i + CHUNK] = _eval_chunk16(flat[i:i + CHUNK], t, idx=fine_idx)
        return out.reshape(shape)
    return _eval_chunk16(flat, t, idx=fine_idx).reshape(shape)


if __name__ == "__main__":                                  # pragma: no cover
    import sys
    if len(sys.argv) > 2 and sys.argv[1] == "pack":
        print("wrote", pack(sys.argv[2]))
    else:
        t = tables()
        print(f"grid {t['gridN']}^3, corners B={t['offB']} G={t['offG']} "
              f"R={t['offR']} RGB={t['offRGB']}")
        print("clut u16 range %d..%d" % (t["clut"].min(), t["clut"].max()))
