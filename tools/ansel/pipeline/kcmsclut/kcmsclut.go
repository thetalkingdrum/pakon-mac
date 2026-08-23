// Package kcmsclut is the Go port of the Kodak CMM's 3-D CLUT interpolator,
// kodakcms.dll fcn.10018160 (md5 e4c8064a9dd3c3a5541d74b00a730e53).
//
// # WHY THIS EXISTS
//
// icc.go's trilinearClut is a trilinear, float64, round-to-nearest ICC mft2
// evaluator. docs/74 §176 drove the real vendor CMM under Wine and established
// that the vendor is none of those three: it is tetrahedral, at 14-bit integer
// precision, with an arithmetic shift (truncation toward -inf) rather than
// rounding. §176's negative controls priced each wrong choice on a 32³ lattice
// of the input domain:
//
//	trilinear instead of tetrahedral   2037 / 98304 samples differ, max |d| 3
//	round-to-nearest instead of SAR    1200 / 98304 samples differ, max |d| 1
//
// # THE REFERENCE
//
// tools/ansel/python-pipeline/pakon_kcms_clut.py is bit-exact against the real
// DLL over the entire u8 RGB input domain — all 16,777,216 triples,
// 50,331,648 channel samples, zero differences (pakon_kcms_clut_golden.py).
// This package is a transcription of it, and tools/test_kcms_clut_ports.py
// re-proves the transcription over the same 16,777,216 triples.
//
// THE ROUTINE, per pixel
//
//	offR, wR = idx[0][r]     (records of {i32 byte offset, i32 weight})
//	offG, wG = idx[1][g]
//	offB, wB = idx[2][b]
//	base = offR + offG + offB
//
//	sort {wR, wG, wB} descending -> (w0, w1, w2); which of the six orderings
//	holds selects one of six tetrahedra and with it two intermediate corner
//	byte offsets (pa, pb)
//
//	for ch in 0, 1, 2:
//	    c = base + 2*ch
//	    A = clut[c];  C = clut[c+pa];  B = clut[c+pb];  D = clut[c+offRGB]
//	    t = (D-B)*w2 + (C-A)*w0 + (B-C)*w1        (signed 32-bit)
//	    out[ch] = otab[ch][4*A + (t >> 14)]       (SAR, i.e. floor)
//
// The grid index and the fraction are not computed per pixel: they come out of
// the precomputed 3×256 table at grid+0x8c, which also absorbs the input curve.
// The 14-bit result is mapped to u8 through a per-channel 16384-entry byte
// table (grid+0x154), so the output transfer curve is exact, not interpolated.
//
// Ranges, measured over the whole u8 domain rather than assumed:
//
//	CLUT word index   0 .. 89372   (table is 89373 words)  — always in range
//	t                 -10,513,533 .. 41,730,541            — never overflows i32
//	otab index        1024 .. 16152 (table is 16384)       — never negative
//
// so nothing here clamps; a clamp would be a deviation from the vendor rather
// than safety. Go's >> on a signed value is defined as an arithmetic shift, so
// t>>14 is the vendor's SAR directly.
package kcmsclut

import (
	"math"
	"sync"
)

// EvalU8 is fcn.10018160 on one interleaved RGB u8 triple.
//
// The six-way branch is the disassembly's own three signed compares in its own
// order: 0x100182a4 cmp wR,wG / 0x100182ac cmp wG,wB / 0x100182c9 cmp wR,wB.
// Ties go where the vendor's jg/jle pairs send them — note the asymmetry
// (wR > wG but wB >= wR). That is load-bearing, and it is what the exhaustive
// test checks.
func EvalU8(in [3]uint8) [3]uint8 {
	offRi, wR := idxOff[0][in[0]], idxWeight[0][in[0]]
	offGi, wG := idxOff[1][in[1]], idxWeight[1][in[1]]
	offBi, wB := idxOff[2][in[2]], idxWeight[2][in[2]]
	base := offRi + offGi + offBi

	var w0, w1, w2, pa, pb int32
	if wR > wG {
		switch {
		case wG > wB: // wR > wG > wB
			w0, w1, w2, pa, pb = wR, wG, wB, offR, offRG
		case wR > wB: // wR > wB >= wG
			w0, w1, w2, pa, pb = wR, wB, wG, offR, offRB
		default: // wB >= wR > wG
			w0, w1, w2, pa, pb = wB, wR, wG, offB, offRB
		}
	} else {
		switch {
		case wG > wB && wR > wB: // wG >= wR > wB
			w0, w1, w2, pa, pb = wG, wR, wB, offG, offRG
		case wG > wB: // wG > wB >= wR
			w0, w1, w2, pa, pb = wG, wB, wR, offG, offGB
		default: // wB >= wG >= wR
			w0, w1, w2, pa, pb = wB, wG, wR, offB, offGB
		}
	}

	var out [3]uint8
	for ch := int32(0); ch < 3; ch++ {
		c := base + 2*ch
		a := int32(clut[c>>1])
		cc := int32(clut[(c+pa)>>1])
		b := int32(clut[(c+pb)>>1])
		d := int32(clut[(c+offRGB)>>1])
		t := (d-b)*w2 + (cc-a)*w0 + (b-cc)*w1
		out[ch] = otab[ch][4*a+(t>>14)]
	}
	return out
}

// EvalU16 is EvalU8's counterpart for a 16-bit sRGB rendering: same tetrahedron
// selection, same CLUT interpolation, same otab table — but where EvalU8's
// `t>>14` floors straight to one of otab's 16384 real captured samples, this
// blends into the NEXT sample using the low 14 bits of t that the shift
// discards (`t & 0x3fff`, 0..16383), instead of throwing them away.
//
// This is anchored to real vendor data, not invented: otab is confirmed
// monotonic non-decreasing over its whole domain, in steps of 0 or 1 byte
// (checked exhaustively — see kcmsclut_test.go), so a linear blend between
// two adjacent real samples can never overshoot the vendor's own curve by
// more than a fraction of one 8-bit step. At every one of the 16384 exact
// sample points (frac == 0) this returns precisely the real vendor byte
// widened by the standard ICC 8-to-16 relation (byte*257, the same relation
// icc.go's own mft2 tag parser uses for an embedded 8-bit table) — i.e. it
// reproduces EvalU8's output exactly, just carried at 16-bit precision.
// Between samples it interpolates smoothly rather than floor-snapping.
//
// It is NOT vendor-verified above 8 bits: otab's own bytes are the finest
// resolution the real vendor CMM was ever observed to produce (captured live
// from kodakcms.dll's own memory, not derived), so there is no vendor ground
// truth finer than that to check an interpolated value against. This is a
// principled reconstruction of the real vendor curve, offered so a graded
// export has more than 256 levels per channel to work with, not a claim of
// additional vendor-matched accuracy.
func EvalU16(in [3]uint8) [3]uint16 {
	return evalU16Core(
		idxOff[0][in[0]], idxWeight[0][in[0]],
		idxOff[1][in[1]], idxWeight[1][in[1]],
		idxOff[2][in[2]], idxWeight[2][in[2]],
	)
}

// evalU16Core is EvalU16 with the input-table lookup factored out: given the
// (offset, weight) triple for R/G/B -- wherever it came from -- it runs the
// tetrahedron selection, CLUT interpolation and otab blend, unchanged from
// EvalU16's own original body. The only caller besides EvalU16 is
// Rpd12ToSrgb16Fine (below), which supplies a finer-resolution table than
// idxOff/idxWeight's 256 entries instead of a different algorithm.
func evalU16Core(offRi, wR, offGi, wG, offBi, wB int32) [3]uint16 {
	base := offRi + offGi + offBi

	var w0, w1, w2, pa, pb int32
	if wR > wG {
		switch {
		case wG > wB:
			w0, w1, w2, pa, pb = wR, wG, wB, offR, offRG
		case wR > wB:
			w0, w1, w2, pa, pb = wR, wB, wG, offR, offRB
		default:
			w0, w1, w2, pa, pb = wB, wR, wG, offB, offRB
		}
	} else {
		switch {
		case wG > wB && wR > wB:
			w0, w1, w2, pa, pb = wG, wR, wB, offG, offRG
		case wG > wB:
			w0, w1, w2, pa, pb = wG, wB, wR, offG, offGB
		default:
			w0, w1, w2, pa, pb = wB, wG, wR, offB, offGB
		}
	}

	var out [3]uint16
	for ch := int32(0); ch < 3; ch++ {
		c := base + 2*ch
		a := int32(clut[c>>1])
		cc := int32(clut[(c+pa)>>1])
		b := int32(clut[(c+pb)>>1])
		d := int32(clut[(c+offRGB)>>1])
		t := (d-b)*w2 + (cc-a)*w0 + (b-cc)*w1
		idx := 4*a + (t >> 14)
		frac := t & 0x3fff // the bits t>>14 discards, always 0..16383
		lo := int32(otab[ch][idx])
		hi := lo
		if idx+1 < int32(len(otab[ch])) {
			hi = int32(otab[ch][idx+1])
		}
		blended := lo*257 + (hi-lo)*257*frac/16384
		out[ch] = uint16(blended)
	}
	return out
}

// Rpd12ToU8 is the encode the vendor's own profile-Rpd2Srgb.dpi implies
// (dataType U8, colorSpaceMax 255) and that the Python path performs in
// pakon_ansel.rpd12_to_icc_u8:
//
//	u8 = clip(rint(code * 255 / 4095), 0, 255)
//
// np.rint is round-half-to-even and so is math.RoundToEven, on the identical
// float64 expression code * (255.0/4095.0). This is NOT int(x + 0.5), which is
// what icc.go's rpd12ToU16 and IccRpd12ToSrgb8Depth do. For integer codes
// 0..4095 no exact half-way value actually arises, so the tie rule is not
// load-bearing here; it is written this way to match the reference expression
// rather than to approximate it.
func Rpd12ToU8(rpd12 int) uint8 {
	if rpd12 <= 0 {
		return 0
	}
	if rpd12 >= 4095 {
		return 255
	}
	v := math.RoundToEven(float64(rpd12) * (255.0 / 4095.0))
	if v <= 0 {
		return 0
	}
	if v >= 255 {
		return 255
	}
	return uint8(v)
}

// Rpd12ToSrgb8 is the whole ICC hop as the vendor performs it: 12-bit RPD in,
// 8-bit sRGB out. It needs no .pf files — SpCombineXforms already folded both
// profiles into the tables in tables.go.
func Rpd12ToSrgb8(rpd [3]int) [3]uint8 {
	return EvalU8([3]uint8{
		Rpd12ToU8(rpd[0]), Rpd12ToU8(rpd[1]), Rpd12ToU8(rpd[2]),
	})
}

// Rpd12ToSrgb16 is Rpd12ToSrgb8's 16-bit-output counterpart (EvalU16, see its
// own docstring for what "16-bit" does and does not mean here). The INPUT
// side is untouched: still the vendor's own 8-bit-in leaf (Rpd12ToU8, u8 by
// construction of the transform the vendor built, not a quantisation this
// port chose — see icc.go), so a given RPD-12 triple resolves to the exact
// same tetrahedron and the exact same real vendor sample points as
// Rpd12ToSrgb8 does. Only the final byte-snap is replaced with the
// interpolated blend.
func Rpd12ToSrgb16(rpd [3]int) [3]uint16 {
	return EvalU16([3]uint8{
		Rpd12ToU8(rpd[0]), Rpd12ToU8(rpd[1]), Rpd12ToU8(rpd[2]),
	})
}

// rpdMax is the F-135 RPD12 ceiling (pakon_ansel.SHASTA_MAX / poly.PolyMax),
// hardcoded the same way Rpd12ToU8's own 4095 is above -- this package has
// no shared constant for it.
const rpdMax = 4095

var (
	fineIdxOff    [3][rpdMax + 1]int32
	fineIdxWeight [3][rpdMax + 1]int32
	fineIdxOnce   sync.Once
)

// buildFineIdx reconstructs idxOff/idxWeight (the vendor's own captured
// grid+0x8c table, 256 real (offset, weight) samples per channel -- the
// ONLY thing standing between a u8 input and a CLUT grid position) at full
// RPD12 resolution (rpdMax+1 = 4096 points) instead of 256.
//
// WHY. Rpd12ToSrgb16 rounds RPD12 (4096 possible codes) down to u8 (256)
// via Rpd12ToU8 BEFORE the CLUT ever runs -- confirmed the only 8-bit
// quantisation in the whole pipeline (docs/79 §3): a real frame's toned
// RPD12 data, which held up to ~1600 distinct codes per channel, collapsed
// to ~100 distinct CLUT inputs, and EvalU16's own output-side blend cannot
// recover precision already gone by the time it runs. Confirmed directly
// against this exact function, not a Python analogue: the same real frame
// through this real Rpd12ToSrgb16 reproduced Python's pre-fix numbers
// bit-for-bit (1503/1923/2451 distinct codes).
//
// HOW. idxOff[c][v]/step + idxWeight[c][v]/65536 is a continuous grid
// position for u8 input v -- 256 real samples of a genuinely nonlinear
// function (confirmed: neither v*GridN/255 nor v*GridN/256 reproduces the
// real table). This reconstructs that same function at rpdMax+1 points via
// piecewise-LINEAR interpolation through those 256 real, known-correct
// samples, evaluated over the same 0..255 domain at RPD12 resolution
// (Rpd12ToU8's own real scale, 255/4095, just not rounded to an integer
// first). Linear, not a smoother fit, specifically because it cannot
// overshoot past the two real samples bracketing any reconstructed point
// -- it can only interpolate between known-correct vendor behaviour, never
// invent an excursion past it. Transcribed from
// pakon_kcms_clut.build_fine_idx (Python), which this is verified against
// (docs/79 §4/§5).
//
// NOT bit-exact to anything the real vendor ever computed: there is no
// ground truth finer than 256 samples to be exact against.
func buildFineIdx() {
	steps := [3]int32{offR, offG, offB}
	for c := 0; c < 3; c++ {
		step := steps[c]
		var posReal [256]float64
		for v := 0; v < 256; v++ {
			posReal[v] = float64(idxOff[c][v])/float64(step) +
				float64(idxWeight[c][v])/65536.0
		}
		for i := 0; i <= rpdMax; i++ {
			vFine := float64(i) * (255.0 / float64(rpdMax))
			var pos float64
			switch {
			case vFine <= 0:
				pos = posReal[0]
			case vFine >= 255:
				pos = posReal[255]
			default:
				lo := int(math.Floor(vFine))
				frac := vFine - float64(lo)
				pos = posReal[lo] + frac*(posReal[lo+1]-posReal[lo])
			}
			cell := int32(math.Floor(pos))
			if cell < 0 {
				cell = 0
			}
			if cell > GridN-2 {
				cell = GridN - 2
			}
			weight := int32(math.Round((pos - float64(cell)) * 65536.0))
			if weight < 0 {
				weight = 0
			}
			if weight > 65535 {
				weight = 65535
			}
			fineIdxOff[c][i] = cell * step
			fineIdxWeight[c][i] = weight
		}
	}
}

// Rpd12ToSrgb16Fine is Rpd12ToSrgb16's precision fix: the same EvalU16
// tetrahedron/CLUT/otab-blend math (evalU16Core, shared with EvalU16
// itself), fed buildFineIdx's RPD12-resolution table instead of
// Rpd12ToU8+idxOff/idxWeight's u8-resolution one -- i.e. Rpd12ToU8's
// rounding never happens. See buildFineIdx's own docstring for the full
// derivation and docs/79 for the measurements (~20-26x more distinct
// output codes on a real frame, visually identical, owner-confirmed in
// Photoshop: no more banding).
func Rpd12ToSrgb16Fine(rpd [3]int) [3]uint16 {
	fineIdxOnce.Do(buildFineIdx)
	r, g, b := clampRpd(rpd[0]), clampRpd(rpd[1]), clampRpd(rpd[2])
	return evalU16Core(
		fineIdxOff[0][r], fineIdxWeight[0][r],
		fineIdxOff[1][g], fineIdxWeight[1][g],
		fineIdxOff[2][b], fineIdxWeight[2][b],
	)
}

func clampRpd(v int) int32 {
	if v < 0 {
		return 0
	}
	if v > rpdMax {
		return rpdMax
	}
	return int32(v)
}

// TetraOf reports which of the six weight orderings an input lands in, using
// the same branch as EvalU8. Exported for the test that shows every
// tetrahedron is actually visited over the domain rather than assumed.
func TetraOf(in [3]uint8) int {
	wR, wG, wB := idxWeight[0][in[0]], idxWeight[1][in[1]], idxWeight[2][in[2]]
	if wR > wG {
		if wG > wB {
			return 0
		}
		if wR > wB {
			return 1
		}
		return 2
	}
	if wG > wB {
		if wR > wB {
			return 4
		}
		return 3
	}
	return 5
}
