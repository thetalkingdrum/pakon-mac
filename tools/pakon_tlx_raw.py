#!/usr/bin/env python3
"""Adapter: the Kodak TLX client's per-frame planar RAW export -> this
project's own stage-2/Ansel pipeline (pakon_decode.py), so a frame captured
with the *real* vendor client under Wine (e.g. via pakon-tlx-macos) can be
rendered here without a scanner attached.

This is NOT pakon-mac's own capture format. pakon-mac's own tooling produces
a raw EP 0x86 strip dump (see pakon_decode.py); this reads a completely
different, already-per-frame file the vendor's TLXClientDemo.exe wrote via
"Save Settings -> To Client Memory, Planar, Add File Header".

FORMAT (pakon-tlx-macos README, "The file you get"):
  16-byte header, 4x uint32 LE: header size, width, height, bit count.
  Then each channel plane in FULL, uint16 LE, PLANAR (not interleaved) --
  i.e. the whole R plane, then the whole G plane, then the whole B plane
  (bit count 48 = RGB; 64 = RGB+IR, IR plane dropped here, this pipeline has
  no per-pixel IR/Digital-ICE stage). Samples never fill the 16-bit word:
  ~0-4095 with the client's own corrections on (already inverted/positive,
  clips), ~0-11800 with them off (a raw negative, never full scale).

THIS ADAPTER IS ONLY FOR "corrections off" CAPTURES. A "corrections on"
export is already inverted by TLB; running it through stage 2 + Ansel again
does not produce a sane image.

GEOMETRY: TLX's width is the along-strip (transport) axis, resampled by the
client to a fixed count per output "Base" (3000 for Base 16); height is the
cross-strip sensor axis. pakon-mac's own PIXELS_PER_LINE (pakon_decode.py) is
fixed at 2000 -- the same DpiBase16 sensor width -- so a TLX height of 2000
lines up with this project's own (lines, px_per_line, 3) layout with a
transpose and no rescaling. A different height means a different Base this
adapter has not been checked against, and it refuses rather than silently
squashing the geometry.

ASSUMPTION, NOT VERIFIED -- state this plainly, this project's own tier
discipline (CLAUDE.md): there is no matched "corrections off" / "corrections
on" pair of the same frame in this repo to diff bit-exact against, so the
following is inferred from the pakon-tlx-macos README's wording, not
measured:

  * a "corrections off" export already carries TLB's own per-pixel dark/gain
    correction (that is basic sensor read-out, not a client colour toggle),
    so this adapter SKIPS pakon-mac's own apply_unit_calibration() -- running
    it would double-correct with a DIFFERENT table (this port's own measured
    calibration/*.npy, not TLB's).
  * the TLX client's own frame extraction has already spatially registered
    R/G/B, so this adapter SKIPS ccd_deskew() too.

If a render through this adapter shows a flat per-channel offset or
channel-misregistered edges that this project's own strip captures do not,
those two assumptions are the first thing to revisit -- not the stage-2 /
Ansel maths downstream, which is exactly pakon_decode.py's own strip path
(this module calls it, not a reimplementation of it).

Orientation is likewise assumed, not verified: this project's own strip
decoder applies a 180-degree rotation to undo the scanner lens's image
inversion (pakon_decode.ROTATE_180_FOR_LENS); whether the TLX client's own
"corrections off" export already carries that fix is unknown. If the render
comes out upside down, pass --no-rotate.

Usage:
  ./pakon_tlx_raw.py TLX_20140611_01_04.raw out/ --sba-default
  ./pakon_tlx_raw.py TLX_20140611_01_04.raw out/ --dx 78-13 --icc --tiff
"""
from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

import numpy as np

_TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(_TOOLS))
import pakon_decode as pd  # noqa: E402


#: (width, height) for each output "Base" the vendor client offers
#: (pakon-tlx-macos README, "Resolutions"). Used only to guess geometry for a
#: headerless export that has had its 16-byte header stripped.
KNOWN_BASE_RESOLUTIONS = ((3000, 2000), (2100, 1400), (1500, 1000))


def load_tlx_planar_raw(
    path: str | Path,
    expect_height: int = pd.PIXELS_PER_LINE,
    force_width: int | None = None,
    force_height: int | None = None,
) -> np.ndarray:
    """TLX planar RAW -> (lines, px_per_line, 3) uint16, pakon-mac's raw14 domain.

    Returns the same shape/dtype/domain as ``pakon_decode.to_rgb14`` — ready
    for ``render_rpd`` directly. Does NOT apply unit calibration or CCD
    deskew (see module docstring for why).
    """
    data = Path(path).read_bytes()
    w = h = nch = None
    body = data

    if len(data) >= 16:
        hsize, hw, hh, bits = struct.unpack("<4I", data[:16])
        nch_candidate = bits // 16
        if (0 < hsize <= 4096 and nch_candidate in (3, 4)
                and len(data) - hsize == hw * hh * nch_candidate * 2):
            w, h, nch = hw, hh, nch_candidate
            body = data[hsize:]
            print(f"  header: size={hsize} width={w} height={h} bits={bits} "
                  f"({nch} planes)")

    if w is None:
        # No (recognisable) header -- either it was stripped, or this isn't
        # a TLX export at all. Try the explicit override, then the vendor
        # client's known fixed output sizes; refuse rather than guess wrong.
        nch = 3
        if force_width and force_height:
            w, h = force_width, force_height
            print(f"  headerless: using --width/--height {w}x{h} RGB")
        else:
            for gw, gh in KNOWN_BASE_RESOLUTIONS:
                if len(data) == gw * gh * nch * 2:
                    w, h = gw, gh
                    break
            if w is None:
                raise SystemExit(
                    f"{path}: no valid 16-byte TLX header, and body size "
                    f"{len(data)} bytes matches none of this client's known "
                    f"output sizes {KNOWN_BASE_RESOLUTIONS} at 3 planes x "
                    f"u16. Pass --width/--height if you know the geometry."
                )
            print(f"  headerless: guessed {w}x{h} RGB from file size "
                  f"({len(data)} bytes) — not a captured header, verify by eye")

    if force_height is not None:
        expect_height = force_height
    if h != expect_height:
        raise SystemExit(
            f"{path}: plane height {h} != pakon-mac's PIXELS_PER_LINE "
            f"({expect_height}). This adapter assumes the TLX height axis "
            f"is the same fixed sensor width pakon-mac's own strip decoder "
            f"uses; a mismatch means a different Base/DPI this adapter has "
            f"not been checked against. Pass --force-height to override at "
            f"your own risk."
        )

    arr = np.frombuffer(body, dtype="<u2")
    planes = arr.reshape(nch, h, w)  # each plane: (height, width) row-major
    rgb = np.stack([planes[0], planes[1], planes[2]], axis=-1)  # (h, w, 3)
    rgb14 = np.transpose(rgb, (1, 0, 2))  # -> (w, h, 3) == (lines, px, 3)
    for c, name in enumerate("RGB"):
        ch = rgb14[:, :, c]
        print(f"  {name}: min={ch.min()} max={ch.max()} mean={ch.mean():.1f}")
    if rgb14.max() > 12000:
        print("  warning: max sample exceeds ~11800 — this project's "
              "'corrections off' ceiling per pakon-tlx-macos's README. "
              "Check this isn't actually a 'corrections on' (already "
              "inverted) export.", file=sys.stderr)
    return np.clip(rgb14, 0, pd.RAW14_MAX).astype(np.uint16)


def cmd_render(args: argparse.Namespace) -> int:
    want_icc = bool(args.icc)
    sba_key_override = args.sba_key
    film_path = args.film_path
    sba_default = bool(args.sba_default)
    if want_icc and not (args.dx or film_path or sba_key_override or sba_default):
        print("error: --icc requires an explicit film/SBA selection "
              "(--dx, --film-path, --sba-key, or --sba-default). Same rule "
              "as pakon_decode.py strip: no silent CN-default.",
              file=sys.stderr)
        return 2
    try:
        pd.check_film_class(pd.pc.film_class_for_path(film_path),
                             args.model)
    except pd.FilmClassNotPorted as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    rgb14 = load_tlx_planar_raw(
        args.input,
        force_width=args.width,
        force_height=args.force_height,
    )
    n_lines = rgb14.shape[0]
    ts = 1.0  # vendor client already resampled the transport axis

    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)

    if args.no_rotate:
        pd.ROTATE_180_FOR_LENS = False

    raw_u8 = pd.raw14_preview_u8(rgb14)
    pd.write_png(out / "tlx_raw14.png", raw_u8, ts)
    print(f"wrote {out / 'tlx_raw14.png'}")
    if args.tiff:
        pd.write_tiff16(out / "tlx_raw14.tiff", rgb14, ts)
        print(f"wrote {out / 'tlx_raw14.tiff'}")

    if not (args.color or want_icc):
        return 0

    print(f"colour-correcting model={args.model} (unit EEPROM/registry poly) …")
    stock = None
    if args.dx:
        p1, p2 = pd.film.parse_dx(args.dx)
        stock = pd.film.lookup(p1, p2)
        print(f"film: {stock.name} ({stock.manufacturer}) path={stock.path} "
              f"ISO={stock.iso} (from --dx {args.dx})")
    elif film_path:
        print(f"film: path={film_path} (from --film-path; no DX)")

    rpd = pd.render_rpd(
        rgb14, args.data_dir, offsets=args.offsets, model=args.model,
        film_class=pd.pc.film_class_for_path(stock.path if stock else film_path),
    )
    rpd_u8 = pd.rpd_preview_u8(rpd)
    pd.write_png(out / "tlx_rpd.png", rpd_u8, ts)
    print(f"wrote {out / 'tlx_rpd.png'}")
    if args.tiff:
        pd.write_tiff16(out / "tlx_rpd16.tiff", rpd, ts)
        print(f"wrote {out / 'tlx_rpd16.tiff'}")

    if not want_icc:
        return 0

    if stock:
        scene = pd.ansel.scene_from_filmstock(
            path=stock.path, dx_part1=stock.dx_part1,
            dx_part2=stock.dx_part2, iso=stock.iso,
        )
    elif film_path:
        metric = (pd.ansel.maps.METRIC_ROM12 if args.model == "f135"
                  else pd.ansel.maps.METRIC_PD12)
        scene = pd.ansel.scene_from_filmstock(path=film_path, metric=metric)
    else:
        scene = pd.ansel.SceneContext()
    force_key = sba_key_override
    if sba_default and not force_key and not stock and not film_path:
        force_key = "ansel-sba-CN-default"
    engine = pd.ansel.AnselEngine.load(
        args.ansel_root, scene=scene, sba_key_override=force_key,
    )

    if args.model == "f135":
        _c = pd.pc.load_unit_matrix("auto")
        _rpd_max = pd.pc.RPD_MAX_BY_MODEL["f135"]
        rpd12_pos = pd.f135_rom12_to_rpd12(
            pd.ansel.rpd16_to_rpd12(rpd, _rpd_max),
            (_c[9], _c[19], _c[29]),
            engine.sba.fpo, engine.setshifts_out,
            capture=str(args.input),
        )
        rpd = pd.rpd12_to_u16(rpd12_pos)
        engine.rpd_max = pd.ansel.SHASTA_MAX
        import os
        engine.shasta_stand_in = os.environ.get("PAKON_REAL_AUTOTONE") != "1"
        print("  F-135 tone: "
              + ("shasta two-anchor stand-in" if engine.shasta_stand_in
                 else "REAL analyzeAutoTone chain (PAKON_REAL_AUTOTONE=1)"))

    print(f"  Ansel {'legacy-v1' if args.legacy_tone else 'two-pass'} "
          f"on 1 frame ({n_lines} lines) …")
    srgb, toned12 = engine.render_strip(
        rpd, [(0, n_lines)], return_toned=True, legacy_tone=args.legacy_tone,
    )
    pd.write_png(out / "tlx_srgb.png", srgb, ts)
    print(f"wrote {out / 'tlx_srgb.png'}")

    ansel_prev = pd.rpd12_preview_u8(toned12)
    pd.write_png(out / "tlx_ansel_rpd.png", ansel_prev, ts)
    print(f"wrote {out / 'tlx_ansel_rpd.png'}")
    if args.tiff:
        pd.write_tiff16(out / "tlx_ansel_rpd16.tiff",
                        pd.rpd12_to_u16(toned12), ts)
        print(f"wrote {out / 'tlx_ansel_rpd16.tiff'}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", help="TLX client planar RAW export "
                                  "(corrections OFF — a negative)")
    ap.add_argument("output", help="output directory")
    ap.add_argument("--color", action="store_true",
                    help="stage-2 colour → 12-bit RPD (default F-135 poly)")
    ap.add_argument("--model", choices=pd.pc.MODELS, default=pd.pc.DEFAULT_MODEL)
    ap.add_argument("--icc", action="store_true",
                    help="Ansel stand-in (SBA/Shasta/FUGC) + Rpd2Pcs→sRGB "
                         "(implies --color)")
    ap.add_argument("--legacy-tone", action="store_true")
    ap.add_argument("--offsets", choices=("dmin", "template"), default="dmin")
    ap.add_argument("--dx", default=None)
    ap.add_argument("--film-path",
                    choices=("ColNeg", "BnW", "POSITIVE", "IMPORTED"),
                    default=None)
    ap.add_argument("--sba-key", default=None)
    ap.add_argument("--sba-default", action="store_true")
    ap.add_argument("--data-dir", default=pd.DEFAULT_DATA_DIR)
    ap.add_argument("--ansel-root", default=pd.DEFAULT_ANSEL_ROOT)
    ap.add_argument("--tiff", action="store_true", help="also write 16-bit TIFFs")
    ap.add_argument("--width", type=int, default=None,
                    help="headerless export only: plane width (along-strip)")
    ap.add_argument("--force-height", type=int, default=None,
                    help="override the expected plane height "
                         "(pakon-mac's PIXELS_PER_LINE, 2000) — use only if "
                         "you know this is a different Base/DPI")
    ap.add_argument("--no-rotate", action="store_true",
                    help="skip the 180-degree lens-inversion rotation "
                         "pakon_decode.py applies by default — try this if "
                         "the render comes out upside down (see module "
                         "docstring: orientation here is unverified)")
    ap.set_defaults(func=cmd_render)

    args = ap.parse_args()
    try:
        return args.func(args)
    except (pd.FilmClassNotPorted, pd.FilmBaseNotFound) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
