// Open capture — the manual escape hatch for a .bin that wasn't just scanned
// (recovering after a crash, reopening an old capture) — and the cleanup
// prompt for whatever a crashed session left behind.
import React, { useEffect, useState } from 'react';
import { Btn, Chip, Spinner } from './components';
import * as api from './api';

const FILM_PATHS = [
  ['ColNeg', 'Colour neg'],
  ['BnW', 'B&W'],
  ['POSITIVE', 'Positive', true],
  ['IMPORTED', 'Imported'],
];

// The most recent "Measure from file…" reading, kept across dialog opens and
// app restarts (localStorage, this machine only) so a film base measured
// once from a roll's clear-film frame doesn't have to be re-measured or
// re-typed for every other frame of that same roll. Shown as info only —
// never auto-filled into a new open's field, since a stale reading silently
// applied to an unrelated roll would be worse than retyping it.
const LAST_TLX_FILM_BASE_KEY = 'pakon:lastTlxFilmBase';

function readLastTlxFilmBase() {
  try {
    const raw = localStorage.getItem(LAST_TLX_FILM_BASE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function writeLastTlxFilmBase(entry) {
  try {
    localStorage.setItem(LAST_TLX_FILM_BASE_KEY, JSON.stringify(entry));
  } catch {
    /* private window, storage disabled — the info line just won't persist */
  }
}

/** ``"3034,1918,2087"`` -> ``{ invalid }`` — shared by every screen that
 *  takes a typed film-base override, so the three-numbers rule can't drift
 *  between them. */
function parseFilmBase(value) {
  const parts = value.trim() ? value.split(',').map((v) => v.trim()).filter(Boolean) : [];
  const invalid = parts.length > 0
    && (parts.length !== 3 || parts.some((v) => Number.isNaN(Number(v))));
  return { parts, invalid };
}

/** The "Film base override" field plus "Measure from file…", shared by the
 *  single-frame Open dialog and the multi-file Import TLX roll dialog — one
 *  place owns the measure job, the last-measured memory and the wording, so
 *  the two screens can't quietly say different things about the same
 *  control. ``value``/``onChange`` are the override text field itself;
 *  ``filmPath`` only selects the measurement's density-matrix film class
 *  (see measure_tlx_film_base's own docstring — it does not need to match
 *  the roll's own choice). */
function FilmBaseMeasure({ open, value, onChange, filmPath, disabled }) {
  const [measureJob, setMeasureJob] = useState(null);
  const [measureError, setMeasureError] = useState(null);
  const [measureSrc, setMeasureSrc] = useState('');
  const [lastMeasured, setLastMeasured] = useState(null);
  const measuring = measureJob && measureJob.status === 'running';
  const { invalid: filmBaseInvalid } = parseFilmBase(value);

  useEffect(() => {
    if (open) {
      setLastMeasured(readLastTlxFilmBase());
    } else {
      setMeasureJob(null);
      setMeasureError(null);
      setMeasureSrc('');
    }
  }, [open]);

  async function measureFromFile() {
    const p = await window.pakon?.openCapture();
    if (!p) return;
    setMeasureError(null);
    setMeasureSrc(p.split('/').pop());
    try {
      const { id } = await api.measureTlxFilmBase({ path: p, film_path: filmPath });
      const final = await api.pollJob(id, setMeasureJob, 300);
      if (final.status === 'error') {
        setMeasureError(final.error);
        setMeasureJob(null);
        return;
      }
      const { film_base, warning } = final.result || {};
      if (warning) {
        // FindDmin's own "no valid Dmin" sentinel on THIS file too — don't
        // fill the field with a zeroed-out reading, that would silently
        // undo the whole point of choosing a different frame.
        setMeasureError(warning);
      } else if (film_base) {
        const measured = film_base.map((v) => Math.round(v)).join(',');
        onChange(measured);
        const entry = { value: measured, src: p.split('/').pop(), ts: Date.now() };
        setLastMeasured(entry);
        writeLastTlxFilmBase(entry);
      }
    } catch (e) {
      setMeasureError(String(e.message || e));
      setMeasureJob(null);
    }
  }

  return (
    <div className="field" style={{ marginBottom: 12 }}>
      <span className="lbl">Film base override (R,G,B) — optional</span>
      <div style={{ display: 'flex', gap: 6 }}>
        <input
          className="inp"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder="e.g. 3034,1918,2087 — leave blank to measure automatically"
          spellCheck={false}
        />
        <Btn variant="flat" disabled={measuring || disabled} onClick={measureFromFile}>
          {measuring ? 'Measuring…' : 'Measure from file…'}
        </Btn>
      </div>
      <span style={{ fontSize: 11, color: 'var(--faint)', marginTop: 4, display: 'block' }}>
        A single vendor-cropped frame with no clear-film margin can make the automatic
        measurement mistake a bright real subject (sunlit glass, snow, sky) for clear film
        and wash the whole render out. If you have a known-good base from another frame of
        this roll/stock, type it in above, or pick that other TLX export with
        "Measure from file…" and FindDmin will read it for you.
      </span>
      {lastMeasured && !measureSrc ? (
        <div className="rows" style={{ marginTop: 6, padding: '6px 9px', display: 'flex', alignItems: 'center', gap: 8, fontSize: 11 }}>
          <span style={{ flex: 1, color: 'var(--faint)' }}>
            Last measured: <span className="num">{lastMeasured.value}</span> from{' '}
            {lastMeasured.src} · {api.fmtDate(lastMeasured.ts / 1000)}
          </span>
          <Btn variant="flat" onClick={() => onChange(lastMeasured.value)}>Use</Btn>
        </div>
      ) : null}
      {measureError ? (
        <span style={{ fontSize: 11, color: 'var(--danger-ink)', marginTop: 4, display: 'block' }}>
          {measureSrc ? `${measureSrc}: ` : ''}{measureError}
        </span>
      ) : measureJob && measureJob.status === 'done' && value ? (
        <span style={{ fontSize: 11, color: 'var(--ok-ink)', marginTop: 4, display: 'block' }}>
          Measured from {measureSrc}.
        </span>
      ) : null}
      {filmBaseInvalid ? (
        <span style={{ fontSize: 11, color: 'var(--danger-ink)', marginTop: 4, display: 'block' }}>
          Needs exactly three numbers, comma-separated.
        </span>
      ) : null}
    </div>
  );
}

export function OpenDialog({ open, onClose, onOpened, captures }) {
  const [path, setPath] = useState('');
  const [name, setName] = useState('');
  const [filmPath, setFilmPath] = useState('ColNeg');
  const [dx, setDx] = useState('');
  const [filmBase, setFilmBase] = useState('');
  const [film, setFilm] = useState(null);
  const [job, setJob] = useState(null);
  const [error, setError] = useState(null);
  const busy = job && job.status === 'running';
  const isTlx = /\.raw$/i.test(path.trim());
  const { invalid: filmBaseInvalid } = parseFilmBase(filmBase);

  useEffect(() => {
    if (!open) {
      setJob(null);
      setError(null);
      setFilmBase('');
    }
  }, [open]);

  useEffect(() => {
    if (!dx.trim()) return setFilm(null);
    let alive = true;
    api.lookupFilm(dx.trim()).then((f) => alive && setFilm(f.error ? null : f)).catch(() => alive && setFilm(null));
    return () => { alive = false; };
  }, [dx]);

  async function go() {
    setError(null);
    try {
      const openFn = isTlx ? api.openTlxCapture : api.openCapture;
      const { id } = await openFn({
        path,
        name: name.trim() || undefined,
        film_path: filmPath,
        dx: dx.trim() || undefined,
        ...(isTlx ? { film_base: filmBase.trim() || undefined } : {}),
      });
      const final = await api.pollJob(id, setJob, 300);
      if (final.status === 'error') {
        setError(final.error);
        setJob(null);
        return;
      }
      await onOpened(final.roll);
      onClose();
    } catch (e) {
      setError(String(e.message || e));
      setJob(null);
    }
  }

  if (!open) return null;

  return (
    <div className="scrim on" onMouseDown={(e) => e.target === e.currentTarget && !busy && onClose()}>
      <div className="sheet">
        <div style={{ marginBottom: 14 }}>
          <span className="title">Open capture</span>
        </div>

        <div className="field" style={{ marginBottom: 12 }}>
          <span className="lbl">Capture</span>
          <div style={{ display: 'flex', gap: 6 }}>
            <input
              className="inp"
              value={path}
              onChange={(e) => setPath(e.target.value)}
              placeholder="/path/to/capture.bin or TLX export.raw"
              spellCheck={false}
            />
            <Btn
              variant="flat"
              onClick={async () => {
                const p = await window.pakon?.openCapture();
                if (p) {
                  setPath(p);
                  if (!name) setName(p.split('/').pop().replace(/\.(bin|raw)$/i, ''));
                }
              }}
            >
              Browse…
            </Btn>
          </div>
        </div>

        {captures?.length ? (
          <div className="rows" style={{ marginBottom: 12, maxHeight: 150, overflowY: 'auto' }}>
            {captures.map((c) => (
              <button
                key={c.path}
                type="button"
                className={path === c.path ? 'on' : ''}
                onClick={() => {
                  setPath(c.path);
                  if (!name) setName(c.saved_name || c.name.replace(/\.bin$/, ''));
                  if (c.recorded_dx) setDx(c.recorded_dx);
                  if (c.recorded_film_path) setFilmPath(c.recorded_film_path);
                }}
              >
                <span className="num" style={{ flex: 1, fontSize: 12 }}>{c.name}</span>
                {c.recorded_dx || c.recorded_film_path ? (
                  <Chip tone={c.dx_source === 'board' ? 'ok' : 'info'}>
                    {c.recorded_dx || c.recorded_film_path}
                    {c.dx_source === 'board' ? ' · read' : c.dx_source === 'typed' ? ' · typed' : ''}
                  </Chip>
                ) : c.dx_read ? (
                  <Chip tone="ok">{c.dx_read} · read</Chip>
                ) : null}
                {c.has_sidecar ? <Chip tone="info">{c.adjusted} saved</Chip> : null}
                <span className="num" style={{ fontSize: 11, color: 'var(--faint)' }}>{api.fmtBytes(c.bytes)}</span>
              </button>
            ))}
          </div>
        ) : null}

        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 12 }}>
          <div className="field">
            <span className="lbl">Roll name</span>
            <input className="inp" value={name} onChange={(e) => setName(e.target.value)} placeholder="2026-08-07 A" />
          </div>
          <div className="field">
            <span className="lbl">DX</span>
            <input className="inp" value={dx} onChange={(e) => setDx(e.target.value)} placeholder="78-13" spellCheck={false} />
          </div>
        </div>

        {dx.trim() ? (
          <div className="rows" style={{ marginBottom: 12, padding: '8px 11px', fontSize: 12 }}>
            {film ? (
              <>
                <b>{film.name}</b>
                <span style={{ color: 'var(--faint)' }}>
                  {' '}· {film.manufacturer} · {film.path}{film.iso ? ` · ISO ${film.iso}` : ''}
                </span>
              </>
            ) : (
              <span style={{ color: 'var(--danger-ink)' }}>No stock matches that DX.</span>
            )}
          </div>
        ) : (
          <div className="field" style={{ marginBottom: 12 }}>
            <span className="lbl">Film path</span>
            <div className="seg" role="radiogroup" aria-label="Film path">
              {FILM_PATHS.map(([id, label, disabled]) => (
                <button
                  key={id}
                  type="button"
                  role="radio"
                  aria-checked={filmPath === id}
                  className={filmPath === id ? 'on' : ''}
                  disabled={disabled || undefined}
                  onClick={() => !disabled && setFilmPath(id)}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>
        )}

        {isTlx ? (
          <FilmBaseMeasure
            open={open}
            value={filmBase}
            onChange={setFilmBase}
            filmPath={filmPath}
            disabled={busy}
          />
        ) : null}

        {error ? (
          <div style={{ background: 'var(--danger-flat)', color: 'var(--danger-ink)', borderRadius: 'var(--r-sm)', padding: '9px 11px', marginBottom: 12, fontSize: 12 }}>
            {error}
          </div>
        ) : null}

        {busy ? (
          <div style={{ marginBottom: 12 }}>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 5 }}>
              <Spinner>{job.phase}</Spinner>
            </div>
            <div className="bar warnfill">
              <i style={{ width: `${(job.progress || 0) * 100}%` }} />
            </div>
          </div>
        ) : null}

        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
          <Btn variant="flat" disabled={busy} onClick={onClose}>Cancel</Btn>
          <Btn variant="primary" disabled={!path || busy || (!!dx.trim() && !film) || filmBaseInvalid} onClick={go}>
            {busy ? 'Opening…' : 'Open'}
          </Btn>
        </div>
      </div>
    </div>
  );
}

// Import a whole roll from several Kodak TLX client RAW exports — one file
// per frame, concatenated server-side into one multi-frame roll
// (pakon_render.open_tlx_capture_multi) so the rest of the app (frame list,
// param editing, export) never has to know these frames came from separate
// files. Deliberately a separate dialog from OpenDialog rather than that one
// learning to juggle a path array: the natural default here is measuring
// film base over the WHOLE roll (dec.film_base_codes' own roll-wide walk,
// the same one a .bin roll already gets), not per file, and bolting that
// choice onto the single-file dialog's fields would mean branching most of
// them on file count.
export function ImportTlxRollDialog({ open, onClose, onOpened }) {
  const [paths, setPaths] = useState([]);
  const [name, setName] = useState('');
  const [filmPath, setFilmPath] = useState('ColNeg');
  const [dx, setDx] = useState('');
  const [filmBase, setFilmBase] = useState('');
  const [film, setFilm] = useState(null);
  const [job, setJob] = useState(null);
  const [error, setError] = useState(null);
  const busy = job && job.status === 'running';
  const { invalid: filmBaseInvalid } = parseFilmBase(filmBase);

  useEffect(() => {
    if (!open) {
      setPaths([]);
      setName('');
      setJob(null);
      setError(null);
      setFilmBase('');
    }
  }, [open]);

  useEffect(() => {
    if (!dx.trim()) return setFilm(null);
    let alive = true;
    api.lookupFilm(dx.trim()).then((f) => alive && setFilm(f.error ? null : f)).catch(() => alive && setFilm(null));
    return () => { alive = false; };
  }, [dx]);

  async function addFiles() {
    const picked = await window.pakon?.openTlxRollFiles();
    if (!picked?.length) return;
    setPaths((prev) => {
      const merged = [...prev, ...picked.filter((p) => !prev.includes(p))];
      if (!name && merged.length) {
        setName(merged[0].split('/').pop().replace(/\.raw$/i, ''));
      }
      return merged;
    });
  }

  function removeFile(p) {
    setPaths((prev) => prev.filter((x) => x !== p));
  }

  async function go() {
    setError(null);
    try {
      const { id } = await api.openTlxRoll({
        paths,
        name: name.trim() || undefined,
        film_path: filmPath,
        dx: dx.trim() || undefined,
        film_base: filmBase.trim() || undefined,
      });
      const final = await api.pollJob(id, setJob, 300);
      if (final.status === 'error') {
        setError(final.error);
        setJob(null);
        return;
      }
      await onOpened(final.roll);
      onClose();
    } catch (e) {
      setError(String(e.message || e));
      setJob(null);
    }
  }

  if (!open) return null;

  return (
    <div className="scrim on" onMouseDown={(e) => e.target === e.currentTarget && !busy && onClose()}>
      <div className="sheet">
        <div style={{ marginBottom: 14 }}>
          <span className="title">Import TLX roll</span>
        </div>

        <div className="field" style={{ marginBottom: 12 }}>
          <span className="lbl">Frames — one Kodak TLX client RAW export each</span>
          {paths.length ? (
            <div className="rows" style={{ marginBottom: 8, maxHeight: 150, overflowY: 'auto' }}>
              {paths.map((p, i) => (
                <div key={p} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '3px 0' }}>
                  <span className="num" style={{ width: 26, color: 'var(--faint)', fontSize: 11 }}>{i + 1}</span>
                  <span className="num" style={{ flex: 1, fontSize: 12 }}>{p.split('/').pop()}</span>
                  <button
                    type="button"
                    className="quiet"
                    disabled={busy}
                    onClick={() => removeFile(p)}
                    style={{ fontSize: 11 }}
                  >
                    Remove
                  </button>
                </div>
              ))}
            </div>
          ) : (
            <span style={{ fontSize: 12, color: 'var(--faint)', display: 'block', marginBottom: 8 }}>
              No files chosen yet.
            </span>
          )}
          <Btn variant="flat" disabled={busy} onClick={addFiles}>
            {paths.length ? 'Add more…' : 'Choose files…'}
          </Btn>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginBottom: 12 }}>
          <div className="field">
            <span className="lbl">Roll name</span>
            <input className="inp" value={name} onChange={(e) => setName(e.target.value)} placeholder="2026-08-07 A" />
          </div>
          <div className="field">
            <span className="lbl">DX</span>
            <input className="inp" value={dx} onChange={(e) => setDx(e.target.value)} placeholder="78-13" spellCheck={false} />
          </div>
        </div>

        {dx.trim() ? (
          <div className="rows" style={{ marginBottom: 12, padding: '8px 11px', fontSize: 12 }}>
            {film ? (
              <>
                <b>{film.name}</b>
                <span style={{ color: 'var(--faint)' }}>
                  {' '}· {film.manufacturer} · {film.path}{film.iso ? ` · ISO ${film.iso}` : ''}
                </span>
              </>
            ) : (
              <span style={{ color: 'var(--danger-ink)' }}>No stock matches that DX.</span>
            )}
          </div>
        ) : (
          <div className="field" style={{ marginBottom: 12 }}>
            <span className="lbl">Film path</span>
            <div className="seg" role="radiogroup" aria-label="Film path">
              {FILM_PATHS.map(([id, label, disabled]) => (
                <button
                  key={id}
                  type="button"
                  role="radio"
                  aria-checked={filmPath === id}
                  className={filmPath === id ? 'on' : ''}
                  disabled={disabled || undefined}
                  onClick={() => !disabled && setFilmPath(id)}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>
        )}

        <FilmBaseMeasure
          open={open}
          value={filmBase}
          onChange={setFilmBase}
          filmPath={filmPath}
          disabled={busy}
        />
        <p style={{ fontSize: 11, color: 'var(--faint)', marginTop: -6, marginBottom: 12 }}>
          Leave blank to measure automatically over the whole roll — every frame's clear-film
          margin (if any) contributes, the same way a real scanned roll's film base is found.
        </p>

        {error ? (
          <div style={{ background: 'var(--danger-flat)', color: 'var(--danger-ink)', borderRadius: 'var(--r-sm)', padding: '9px 11px', marginBottom: 12, fontSize: 12 }}>
            {error}
          </div>
        ) : null}

        {busy ? (
          <div style={{ marginBottom: 12 }}>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 8, marginBottom: 5 }}>
              <Spinner>{job.phase}</Spinner>
            </div>
            <div className="bar warnfill">
              <i style={{ width: `${(job.progress || 0) * 100}%` }} />
            </div>
          </div>
        ) : null}

        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
          <Btn variant="flat" disabled={busy} onClick={onClose}>Cancel</Btn>
          <Btn
            variant="primary"
            disabled={!paths.length || busy || (!!dx.trim() && !film) || filmBaseInvalid}
            onClick={go}
          >
            {busy ? 'Opening…' : `Import ${paths.length || ''} frame${paths.length === 1 ? '' : 's'}`}
          </Btn>
        </div>
      </div>
    </div>
  );
}

export function CleanupDialog({ state, onDone }) {
  const [sel, setSel] = useState(() => new Set(state.rolls.map((r) => r.id)));
  const [capSel, setCapSel] = useState(() => new Set());
  const [busy, setBusy] = useState(false);
  const captures = state.captures || [];
  const chosen = state.rolls.filter((r) => sel.has(r.id));
  const chosenCaps = captures.filter((c) => capSel.has(c.name));
  const bytes = chosen.reduce((a, r) => a + r.bytes, 0) + chosenCaps.reduce((a, c) => a + c.bytes, 0);

  const toggle = (set, put, key) => {
    const n = new Set(set);
    if (n.has(key)) n.delete(key);
    else n.add(key);
    put(n);
  };

  return (
    <div className="scrim on">
      <div className="sheet">
        <div style={{ marginBottom: 14 }}>
          <span className="title">Scans from a previous session</span>
        </div>

        {state.rolls.length ? (
          <>
            <span className="lbl">Render cache</span>
            <div className="rows" style={{ margin: '4px 0 12px', maxHeight: 190, overflowY: 'auto' }}>
              {state.rolls.map((r) => (
                <label key={r.id}>
                  <input type="checkbox" checked={sel.has(r.id)} onChange={() => toggle(sel, setSel, r.id)} />
                  <span style={{ flex: 1 }}>{r.name}</span>
                  {r.adjusted > r.exported ? (
                    <Chip tone="warn">{r.adjusted} adjusted, {r.exported} exported</Chip>
                  ) : null}
                  <span className="num" style={{ fontSize: 11, color: 'var(--faint)' }}>{api.fmtDate(r.mtime)}</span>
                  <span className="num" style={{ fontSize: 11, width: 72, textAlign: 'right' }}>{api.fmtBytes(r.bytes)}</span>
                </label>
              ))}
            </div>
          </>
        ) : null}

        {captures.length ? (
          <>
            <span className="lbl" style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              Raw captures
              <Chip tone="warn">cannot be remade without rescanning</Chip>
            </span>
            <div className="rows" style={{ margin: '4px 0 12px', maxHeight: 190, overflowY: 'auto' }}>
              {captures.map((c) => (
                <label key={c.name}>
                  <input type="checkbox" checked={capSel.has(c.name)} onChange={() => toggle(capSel, setCapSel, c.name)} />
                  <span className="num" style={{ flex: 1, fontSize: 12 }}>{c.name}</span>
                  {c.adjusted > c.exported ? (
                    <Chip tone="warn">{c.adjusted} adjusted, {c.exported} exported</Chip>
                  ) : null}
                  <span className="num" style={{ fontSize: 11, color: 'var(--faint)' }}>{api.fmtDate(c.mtime)}</span>
                  <span className="num" style={{ fontSize: 11, width: 72, textAlign: 'right' }}>{api.fmtBytes(c.bytes)}</span>
                </label>
              ))}
            </div>
          </>
        ) : null}

        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
          <Btn variant="flat" disabled={busy} onClick={onDone}>Keep everything</Btn>
          <Btn
            variant="primary"
            disabled={busy || !(chosen.length || chosenCaps.length)}
            onClick={async () => {
              setBusy(true);
              try {
                await api.purge({ ids: chosen.map((r) => r.id), captures: chosenCaps.map((c) => c.name) });
              } finally {
                setBusy(false);
                onDone();
              }
            }}
          >
            Delete {api.fmtBytes(bytes)}
          </Btn>
        </div>
      </div>
    </div>
  );
}
