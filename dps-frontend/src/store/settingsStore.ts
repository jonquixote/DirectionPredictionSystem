import { create } from 'zustand';

interface AlertThresholds {
  min_accuracy: number;
}

interface Settings {
  timezone: 'UTC' | 'local';
  theme: 'dark' | 'light';
  alert_thresholds: AlertThresholds;
  rolling_n: number;
  fee: number;
  annotation_layer: boolean;

  setTimezone: (timezone: 'UTC' | 'local') => void;
  setTheme: (theme: 'dark' | 'light') => void;
  setSettings: (settings: Partial<Omit<Settings, 'setTimezone' | 'setTheme' | 'setSettings'>>) => void;
}

export const useSettingsStore = create<Settings>((set) => ({
  timezone: 'UTC',
  theme: 'dark',
  alert_thresholds: { min_accuracy: 0.515 },
  rolling_n: 50,
  fee: 0.02,
  annotation_layer: true,

  setTimezone: (timezone) => set({ timezone }),
  setTheme: (theme) => {
    document.documentElement.setAttribute('data-theme', theme);
    set({ theme });
  },
  setSettings: (settings) => set((state) => ({ ...state, ...settings })),
}));
