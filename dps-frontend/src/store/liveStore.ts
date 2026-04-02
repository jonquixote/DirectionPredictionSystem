import { create } from 'zustand';
import type { StatusResponse, PredictionRecord, TradeRecord, AlertRecord } from '../types';

interface LiveState {
  connected: boolean;
  last_status: StatusResponse | null;
  recent_predictions: PredictionRecord[];
  recent_trades: TradeRecord[];
  recent_alerts: AlertRecord[];
  last_update_ms: number | null;
  setConnected: (connected: boolean) => void;
  setStatus: (status: StatusResponse) => void;
  addPrediction: (prediction: PredictionRecord) => void;
  addTrade: (trade: TradeRecord) => void;
  addAlert: (alert: AlertRecord) => void;
}

export const useLiveStore = create<LiveState>((set) => ({
  connected: false,
  last_status: null,
  recent_predictions: [],
  recent_trades: [],
  recent_alerts: [],
  last_update_ms: null,

  setConnected: (connected) => set({ connected }),
  setStatus: (status) => set({ last_status: status, last_update_ms: Date.now() }),
  addPrediction: (prediction) =>
    set((state) => ({
      recent_predictions: [prediction, ...state.recent_predictions].slice(0, 100),
      last_update_ms: Date.now(),
    })),
  addTrade: (trade) =>
    set((state) => ({
      recent_trades: [trade, ...state.recent_trades].slice(0, 50),
      last_update_ms: Date.now(),
    })),
  addAlert: (alert) =>
    set((state) => ({
      recent_alerts: [alert, ...state.recent_alerts].slice(0, 20),
      last_update_ms: Date.now(),
    })),
}));
