import { useState } from 'react';
import { ShieldCheck, Loader2, CheckCircle, AlertCircle, X } from 'lucide-react';
import { updateModelFilter } from '../../api/client';
import type { ModelFilterConfig, ModelFilterUpdateRequest, ModelFilterUpdateResponse } from '../../types';

interface ModelFilterEditorProps {
  model: {
    name: string;
    live_eligible?: number | boolean;
    filter_config?: ModelFilterConfig;
  };
  onSaved: () => void;
}

// Parse a comma/space separated string of hours into number[]
function parseHours(raw: string): number[] {
  return raw
    .split(/[\s,]+/)
    .map((s) => parseInt(s, 10))
    .filter((n) => !isNaN(n) && n >= 0 && n <= 23);
}

function hoursToString(hours?: number[]): string {
  return hours && hours.length > 0 ? hours.join(', ') : '';
}

// Tiny labeled input row used throughout the form
function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1">
      <label className="text-xs font-medium text-[var(--color-text-muted)]">{label}</label>
      {children}
      {hint && <span className="text-[10px] text-[var(--color-text-faint)]">{hint}</span>}
    </div>
  );
}

const inputCls =
  'w-full bg-[var(--color-surface-2)] border border-[var(--color-border)] rounded-md px-3 py-1.5 text-sm font-mono text-[var(--color-text)] ' +
  'placeholder:text-[var(--color-text-faint)] focus:outline-none focus:border-[var(--color-primary)] focus:ring-1 focus:ring-[var(--color-primary)] transition-colors';

export const ModelFilterEditor = ({ model, onSaved }: ModelFilterEditorProps) => {
  const cfg = model.filter_config ?? {};
  const isLiveEligible = Boolean(model.live_eligible);

  // Form state — empty string means "unset / use global"
  const [confidence, setConfidence] = useState(
    cfg.confidence_threshold !== undefined ? String(cfg.confidence_threshold) : ''
  );
  const [ev, setEv] = useState(
    cfg.ev_threshold !== undefined ? String(cfg.ev_threshold) : ''
  );
  const [blackoutRaw, setBlackoutRaw] = useState(hoursToString(cfg.blackout_hours));
  const [warmup, setWarmup] = useState(
    cfg.warmup_seconds !== undefined ? String(cfg.warmup_seconds) : ''
  );
  const [confirmToken, setConfirmToken] = useState('');

  const [saving, setSaving] = useState(false);
  const [savedResult, setSavedResult] = useState<ModelFilterUpdateResponse | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const handleSave = async () => {
    setErrorMsg(null);
    setSavedResult(null);

    const body: ModelFilterUpdateRequest = {};
    const clearKeys: string[] = [];

    if (confidence !== '') {
      const v = parseFloat(confidence);
      if (isNaN(v) || v < 0 || v > 1) {
        setErrorMsg('Confidence threshold must be between 0 and 1.');
        return;
      }
      body.confidence_threshold = v;
    } else if (cfg.confidence_threshold !== undefined) {
      clearKeys.push('confidence_threshold');
    }

    if (ev !== '') {
      const v = parseFloat(ev);
      if (isNaN(v) || v < -1 || v > 1) {
        setErrorMsg('EV threshold must be between -1 and 1.');
        return;
      }
      body.ev_threshold = v;
    } else if (cfg.ev_threshold !== undefined) {
      clearKeys.push('ev_threshold');
    }

    if (blackoutRaw.trim() !== '') {
      const hours = parseHours(blackoutRaw);
      if (hours.length === 0) {
        setErrorMsg('Blackout hours must be integers 0–23, comma or space separated.');
        return;
      }
      body.blackout_hours = hours;
    } else if (cfg.blackout_hours && cfg.blackout_hours.length > 0) {
      clearKeys.push('blackout_hours');
    }

    if (warmup !== '') {
      const v = parseInt(warmup, 10);
      if (isNaN(v) || v < 0) {
        setErrorMsg('Warmup seconds must be a non-negative integer.');
        return;
      }
      body.warmup_seconds = v;
    } else if (cfg.warmup_seconds !== undefined) {
      clearKeys.push('warmup_seconds');
    }

    if (clearKeys.length > 0) body.clear_keys = clearKeys;

    // Bail if nothing to send
    if (Object.keys(body).length === 0) {
      setErrorMsg('No changes to save.');
      return;
    }

    setSaving(true);
    try {
      const result = await updateModelFilter(
        model.name,
        body,
        isLiveEligible && confirmToken ? confirmToken : undefined
      );
      setSavedResult(result);
      onSaved();
    } catch (err: any) {
      const status = err?.response?.status;
      const detail = err?.response?.data?.detail;
      if (status === 422) {
        setErrorMsg(`Validation error: ${detail ?? 'invalid field values'}`);
      } else if (status === 401) {
        setErrorMsg('Authentication required. Check your credentials.');
      } else if (status === 403) {
        setErrorMsg('Confirmation token required or invalid for live-eligible model.');
      } else if (status === 404) {
        setErrorMsg('Model not found.');
      } else {
        setErrorMsg(err?.message ?? 'Unknown error saving filter config.');
      }
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="bg-[var(--color-surface-2)] border border-[var(--color-border)] rounded-xl p-5 mt-1 space-y-4 shadow-sm">
      <div className="flex items-center gap-2 mb-1">
        <ShieldCheck className="w-4 h-4 text-[var(--color-primary)]" />
        <span className="text-sm font-semibold text-[var(--color-text)]">Gate Filter Config</span>
        <span className="ml-auto text-[10px] font-mono text-[var(--color-text-faint)]">{model.name}</span>
      </div>

      <div className="grid grid-cols-2 gap-4">
        <Field label="Confidence Threshold" hint="0–1, leave blank to use global default">
          <input
            type="number"
            step="0.01"
            min={0}
            max={1}
            placeholder="e.g. 0.55"
            value={confidence}
            onChange={(e) => setConfidence(e.target.value)}
            className={inputCls}
          />
        </Field>

        <Field label="EV Threshold" hint="-1–1, leave blank to use global default">
          <input
            type="number"
            step="0.001"
            min={-1}
            max={1}
            placeholder="e.g. 0.005"
            value={ev}
            onChange={(e) => setEv(e.target.value)}
            className={inputCls}
          />
        </Field>

        <Field label="Blackout Hours (UTC)" hint="Hours 0–23, comma or space separated. Blank = no blackout">
          <input
            type="text"
            placeholder="e.g. 21, 22, 23, 0, 1, 2"
            value={blackoutRaw}
            onChange={(e) => setBlackoutRaw(e.target.value)}
            className={inputCls}
          />
          {/* Chip preview */}
          {blackoutRaw.trim() !== '' && (
            <div className="flex flex-wrap gap-1 mt-1">
              {parseHours(blackoutRaw).map((h) => (
                <span
                  key={h}
                  className="px-1.5 py-0.5 rounded text-[10px] font-mono bg-[var(--color-primary)]/10 text-[var(--color-primary)] border border-[var(--color-primary)]/20"
                >
                  {String(h).padStart(2, '0')}:00
                </span>
              ))}
            </div>
          )}
        </Field>

        <Field label="Warmup Seconds" hint="Seconds after startup before trading. Blank = global default">
          <input
            type="number"
            step={60}
            min={0}
            placeholder="e.g. 1800"
            value={warmup}
            onChange={(e) => setWarmup(e.target.value)}
            className={inputCls}
          />
        </Field>
      </div>

      {/* Confirmation token — only shown for live-eligible models */}
      {isLiveEligible && (
        <Field label="HMAC Confirmation Token" hint="Required for live-eligible models (X-Confirm-Token)">
          <input
            type="text"
            placeholder="Paste confirmation token..."
            value={confirmToken}
            onChange={(e) => setConfirmToken(e.target.value)}
            className={inputCls + ' font-mono text-xs'}
          />
        </Field>
      )}

      {/* Error banner */}
      {errorMsg && (
        <div className="flex items-start gap-2 bg-[var(--color-error)]/10 border border-[var(--color-error)]/20 rounded-lg p-3 text-sm text-[var(--color-error)]">
          <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
          <span>{errorMsg}</span>
          <button
            onClick={() => setErrorMsg(null)}
            className="ml-auto opacity-60 hover:opacity-100"
          >
            <X className="w-3.5 h-3.5" />
          </button>
        </div>
      )}

      {/* Success result */}
      {savedResult && (
        <div className="bg-[var(--color-success)]/10 border border-[var(--color-success)]/20 rounded-lg p-3 space-y-1">
          <div className="flex items-center gap-2 text-sm font-semibold text-[var(--color-success)]">
            <CheckCircle className="w-4 h-4" />
            Saved — applied at {savedResult.applied_at}
            {savedResult.trader_reloaded && (
              <span className="ml-2 text-[10px] bg-[var(--color-success)]/20 text-[var(--color-success)] px-2 py-0.5 rounded-full border border-[var(--color-success)]/30">
                trader reloaded
              </span>
            )}
          </div>
          <div className="grid grid-cols-2 gap-x-6 gap-y-0.5 text-[11px] font-mono text-[var(--color-text-muted)]">
            {savedResult.filter_config.confidence_threshold !== undefined && (
              <span>confidence: {savedResult.filter_config.confidence_threshold}</span>
            )}
            {savedResult.filter_config.ev_threshold !== undefined && (
              <span>ev: {savedResult.filter_config.ev_threshold}</span>
            )}
            {savedResult.filter_config.warmup_seconds !== undefined && (
              <span>warmup: {savedResult.filter_config.warmup_seconds}s</span>
            )}
            {savedResult.filter_config.blackout_hours !== undefined && (
              <span>blackout: [{savedResult.filter_config.blackout_hours.join(', ')}]</span>
            )}
          </div>
        </div>
      )}

      <div className="flex justify-end pt-1">
        <button
          onClick={handleSave}
          disabled={saving}
          className={
            'flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-semibold transition-colors ' +
            (saving
              ? 'bg-[var(--color-primary)]/40 text-[var(--color-text-inverse)] cursor-not-allowed'
              : 'bg-[var(--color-primary)] hover:bg-[var(--color-primary-hover)] active:bg-[var(--color-primary-active)] text-[var(--color-text-inverse)] cursor-pointer')
          }
        >
          {saving && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
          {saving ? 'Saving…' : 'Save Gates'}
        </button>
      </div>
    </div>
  );
};
