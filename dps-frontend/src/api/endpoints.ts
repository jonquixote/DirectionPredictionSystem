import { apiClient } from './client';
import type { 
  PredictionRecord, 
  TradeRecord,
  PerformanceSummary
} from '../types';

export interface ListResponse<T> {
  data: T[];
  meta: {
    total: number;
    page: number;
    page_size: number;
    from_ms: number | null;
    to_ms: number | null;
    filters_applied: Record<string, unknown>;
  };
  warnings: string[];
}

export const endpoints = {
  // Status
  // GET /api/status - (we might have a type for StatusResponse)
  getStatus: async () => {
    const res = await apiClient.get('/api/status');
    return res.data;
  },

  // Predictions
  getPredictions: async (params?: Record<string, any>): Promise<ListResponse<PredictionRecord>> => {
    const res = await apiClient.get('/api/predictions', { params });
    return res.data;
  },

  // Trades
  getTrades: async (params?: Record<string, any>): Promise<ListResponse<TradeRecord>> => {
    const res = await apiClient.get('/api/trades', { params });
    return res.data;
  },

  // Performance
  getPerformanceSummary: async (params?: Record<string, any>): Promise<PerformanceSummary[]> => {
    const res = await apiClient.get('/api/performance/summary', { params });
    return res.data;
  },

  getRollingAccuracy: async (params?: Record<string, any>) => {
    const res = await apiClient.get('/api/performance/rolling', { params });
    return res.data;
  },

  getPerformanceTimeline: async (params?: Record<string, any>) => {
    const res = await apiClient.get('/api/performance/timeline', { params });
    return res.data;
  },

  // Models
  getModelsRegistry: async () => {
    const res = await apiClient.get('/api/models');
    return res.data;
  },

  getModelDiff: async (modelA: string, modelB: string) => {
    const res = await apiClient.get('/api/models/diff', {
      params: { a: modelA, b: modelB }
    });
    return res.data;
  },

  // Logs
  getRawLogs: async (params?: Record<string, any>) => {
    const res = await apiClient.get('/api/logs/recent', { params });
    return res.data;
  },

  // Market Data
  getOHLCV: async (symbol: string, from_ms?: number, to_ms?: number) => {
    const res = await apiClient.get('/api/parquet/price', { 
      params: { symbol, from_ms, to_ms } 
    });
    return res.data;
  },

  // Alerts
  getActiveAlerts: async (params?: Record<string, any>) => {
    const res = await apiClient.get('/api/alerts', { params });
    return res.data;
  },

  getSuppressionEffectiveness: async (params?: Record<string, any>) => {
    const res = await apiClient.get('/api/performance/suppression-effectiveness', { params });
    return res.data;
  },

  // Features
  getFeatureImportance: async (model: string) => {
    const res = await apiClient.get('/api/features/importance', { params: { model_version: model } });
    return res.data;
  },

  getFeatureDistributions: async (model: string, symbol: string = "BTCUSDT") => {
    const res = await apiClient.get('/api/features/distributions', { params: { model_version: model, symbol } });
    return res.data;
  }
};
