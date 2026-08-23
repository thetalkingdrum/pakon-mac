// Research flags — the PAKON_* env vars pakon_render.py/pakon_decode.py/
// pakon_ansel.py read live, one per render. Editing here mutates the running
// backend process's own os.environ, nothing more: not persisted, gone on the
// next launch, and never written to any file. Existing to let the flags
// documented across docs/74 be A/B'd from the app instead of relaunching it
// with a different environment each time.
import React, { useEffect, useState } from 'react';
import { Btn } from './components';
import * as api from './api';

/** true/false for a flag's current raw string value, given its "kind"
 *  (see pakon_app.RESEARCH_FLAGS -- this mirrors that file's own polarity
 *  notes so a checkbox here means what the flag's own default means). */
function isOn(kind, value) {
  if (kind === 'bool_inverted') return value !== '0';
  return value === '1';
}

export default function ResearchFlags({ open, onClose, onApplied }) {
  const [state, setState] = useState(null);   // server's last-known catalog + extras
  const [draft, setDraft] = useState({});      // name -> edited string value
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [newName, setNewName] = useState('');
  const [newValue, setNewValue] = useState('');

  useEffect(() => {
    if (!open) return;
    let alive = true;
    api.flags().then((s) => {
      if (!alive) return;
      setState(s);
      const d = {};
      for (const f of s.flags) d[f.name] = f.value ?? '';
      for (const f of s.extra) d[f.name] = f.value ?? '';
      setDraft(d);
    }).catch((e) => alive && setError(String(e.message || e)));
    return () => { alive = false; };
  }, [open]);

  if (!open) return null;

  const setDraftValue = (name, value) => setDraft((d) => ({ ...d, [name]: value }));

  const adopt = (s) => {
    setState(s);
    const d = {};
    for (const f of s.flags) d[f.name] = f.value ?? '';
    for (const f of s.extra) d[f.name] = f.value ?? '';
    setDraft(d);
  };

  const apply = async () => {
    if (!state) return;
    const known = new Map([...state.flags, ...state.extra].map((f) => [f.name, f.value ?? '']));
    const set = {};
    const unset = [];
    for (const [name, value] of Object.entries(draft)) {
      const before = known.get(name) ?? '';
      if (value === before) continue;
      if (value === '') unset.push(name);
      else set[name] = value;
    }
    if (!Object.keys(set).length && !unset.length) return onClose();
    setBusy(true);
    setError(null);
    try {
      adopt(await api.setFlags({ set, unset }));
      onApplied?.();
    } catch (e) {
      setError(String(e.message || e));
    } finally {
      setBusy(false);
    }
  };

  /* Every catalog + extra flag, unset outright -- not "whatever main.js
   * spawned the backend with" (that's not on record here to restore), but
   * each flag's own code-level fallback (colour_engine()'s "go", etc). A
   * relaunch is the only way back to the app's own opinionated startup
   * values (PAKON_COLOUR_ENGINE=python and friends, see app/main.js). */
  const resetAll = async () => {
    if (!state) return;
    const names = [...state.flags, ...state.extra].map((f) => f.name);
    if (!names.length) return;
    setBusy(true);
    setError(null);
    try {
      adopt(await api.setFlags({ unset: names }));
      onApplied?.();
    } catch (e) {
      setError(String(e.message || e));
    } finally {
      setBusy(false);
    }
  };

  const addCustom = () => {
    const name = newName.trim().toUpperCase();
    if (!/^PAKON_[A-Z0-9_]+$/.test(name)) {
      setError(`Not a PAKON_* flag name: "${newName}"`);
      return;
    }
    setDraftValue(name, newValue);
    setNewName('');
    setNewValue('');
  };

  // Same default pakon_render.colour_engine() itself falls back to.
  const currentEngine = (draft.PAKON_COLOUR_ENGINE || 'go').trim().toLowerCase();

  /** Why a flag would have no effect right now, given the rest of the
   *  draft -- or null if it's live. Only the interactions actually traced
   *  through the code (see RESEARCH_FLAGS's own docstring in pakon_app.py
   *  and the group hints above); not a claim about every possible pair. */
  const inertReason = (f) => {
    if (f.engine && f.engine !== currentEngine) {
      return `Inert: PAKON_COLOUR_ENGINE is "${currentEngine}", this needs "${f.engine}".`;
    }
    if (f.name === 'PAKON_NO_INVERT' && isOn('bool', draft.PAKON_VENDOR_INVERT ?? '')) {
      return 'Inert: PAKON_VENDOR_INVERT is on, which returns before this is ever checked.';
    }
    if (f.name === 'PAKON_VENDOR_INVERT_ANCHOR' && !isOn('bool', draft.PAKON_VENDOR_INVERT ?? '')) {
      return 'Inert: only read when PAKON_VENDOR_INVERT is on.';
    }
    return null;
  };

  const groups = state ? [...new Set(state.flags.map((f) => f.group))] : [];
  const dirtyCount = state
    ? [...state.flags, ...state.extra].filter(
        (f) => (draft[f.name] ?? '') !== (f.value ?? ''),
      ).length +
      Object.keys(draft).filter(
        (n) => !state.flags.some((f) => f.name === n) && !state.extra.some((f) => f.name === n),
      ).length
    : 0;
  const anySet = state
    ? [...state.flags, ...state.extra].some((f) => (f.value ?? '') !== '')
    : false;

  return (
    <div className="scrim on" onMouseDown={(e) => e.target === e.currentTarget && !busy && onClose()}>
      <div className="sheet" style={{ width: 640, maxWidth: '92vw' }}>
        <div style={{ marginBottom: 4 }}>
          <span className="title">Research flags</span>
        </div>
        <p className="quiet" style={{ fontSize: 12, marginBottom: 14 }}>
          Live PAKON_* env vars in the running backend process. Changes apply to the
          very next frame render (the ETag folds in every PAKON_* var) — nothing here
          is saved to disk, and everything resets on the next launch.
        </p>

        {error ? (
          <div
            style={{
              background: 'var(--danger-flat)', color: 'var(--danger-ink)',
              borderRadius: 'var(--r-sm)', padding: '8px 11px', marginBottom: 12,
              fontSize: 12.5,
            }}
          >
            {error}
          </div>
        ) : null}

        {!state ? (
          <p className="quiet">Loading…</p>
        ) : (
          <div style={{ maxHeight: '56vh', overflowY: 'auto', paddingRight: 4 }}>
            {groups.map((g) => (
              <div key={g} style={{ marginBottom: 16 }}>
                <div className="quiet" style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: 0.4, marginBottom: 6 }}>
                  {g}
                </div>
                {state.flags.filter((f) => f.group === g).map((f) => {
                  const inert = inertReason(f);
                  return (
                    <div
                      key={f.name}
                      style={{ display: 'flex', gap: 10, alignItems: 'flex-start', padding: '6px 0', opacity: inert ? 0.5 : 1 }}
                    >
                      {f.kind === 'text' ? (
                        <input
                          className="inp"
                          style={{ width: 130, flexShrink: 0 }}
                          value={draft[f.name] ?? ''}
                          placeholder="(default)"
                          onChange={(e) => setDraftValue(f.name, e.target.value)}
                        />
                      ) : (
                        <input
                          type="checkbox"
                          style={{ marginTop: 3, flexShrink: 0 }}
                          checked={isOn(f.kind, draft[f.name] ?? '')}
                          onChange={(e) => {
                            if (f.kind === 'bool_inverted') setDraftValue(f.name, e.target.checked ? '' : '0');
                            else setDraftValue(f.name, e.target.checked ? '1' : '');
                          }}
                        />
                      )}
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div className="num" style={{ fontSize: 12 }}>{f.name}</div>
                        <div className="quiet" style={{ fontSize: 11, lineHeight: 1.4 }}>{f.hint}</div>
                        {inert ? (
                          <div style={{ fontSize: 11, lineHeight: 1.4, color: 'var(--warning-ink, var(--danger-ink))', marginTop: 2 }}>
                            {inert}
                          </div>
                        ) : null}
                      </div>
                    </div>
                  );
                })}
              </div>
            ))}

            {state.extra.length ? (
              <div style={{ marginBottom: 16 }}>
                <div className="quiet" style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: 0.4, marginBottom: 6 }}>
                  Set elsewhere (not in the catalog above)
                </div>
                {state.extra.map((f) => (
                  <div key={f.name} style={{ display: 'flex', gap: 10, alignItems: 'center', padding: '4px 0' }}>
                    <input
                      className="inp"
                      style={{ width: 130, flexShrink: 0 }}
                      value={draft[f.name] ?? ''}
                      onChange={(e) => setDraftValue(f.name, e.target.value)}
                    />
                    <div className="num" style={{ fontSize: 12 }}>{f.name}</div>
                  </div>
                ))}
              </div>
            ) : null}

            <div>
              <div className="quiet" style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: 0.4, marginBottom: 6 }}>
                Add a flag not listed above
              </div>
              <div style={{ display: 'flex', gap: 8 }}>
                <input
                  className="inp" style={{ flex: 1 }} placeholder="PAKON_SOMETHING"
                  value={newName} onChange={(e) => setNewName(e.target.value)}
                  spellCheck={false}
                />
                <input
                  className="inp" style={{ width: 120 }} placeholder="value"
                  value={newValue} onChange={(e) => setNewValue(e.target.value)}
                  spellCheck={false}
                />
                <Btn variant="flat" onClick={addCustom} disabled={!newName.trim()}>Add</Btn>
              </div>
            </div>
          </div>
        )}

        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', marginTop: 14 }}>
          <Btn variant="flat" disabled={busy || !anySet} onClick={resetAll}>Reset all to defaults</Btn>
          <span className="sp" />
          <Btn variant="flat" disabled={busy} onClick={onClose}>Close</Btn>
          <Btn variant="primary" disabled={busy || !state} onClick={apply}>
            {busy ? 'Applying…' : dirtyCount ? `Apply ${dirtyCount} change${dirtyCount === 1 ? '' : 's'}` : 'Apply'}
          </Btn>
        </div>
      </div>
    </div>
  );
}
