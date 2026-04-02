export type ModelVersion = 'h60_v1' | 'h60_v3' | 'h300';
export type Symbol = 'BTCUSDT' | 'SOLUSDT' | 'ETHUSDT';
export type Direction = 'up' | 'down';
export type Outcome = 'correct' | 'incorrect' | 'unresolved';
export type ContractDuration = 300 | 900;
export type SuppressedReason = 'contract_mismatch' | 'utc_blackout' | null;
export type AlertSeverity = 'INFO' | 'WARN' | 'CRITICAL';
export type AlertType = 'GATE_STATUS_CHANGE' | 'NE_T_DAILY' | 'NE_T_NEGATIVE' | 'ACCURACY_ALERT' | 'COVERAGE_DROP' | 'FEATURE_DRIFT' | 'MODEL_AGREEMENT_FLIP' | 'P_MARKET_ANOMALY' | 'API_FAILURE' | 'SUPPRESSION_EFFECTIVENESS';
export type GateTrend = 'up' | 'down' | 'stable';

export interface PredictionRecord {
  prediction_id: string;
  ts_model_ran_ms: number;
  symbol: Symbol;
  model: ModelVersion;
  pred_direction: Direction;
  pred_proba: number;
  p_market: number | null;
  divergence: number | null;
  signed_divergence: number | null;
  warmup: boolean;
  suppressed_reason: SuppressedReason;
  features: Record<string, number | null>;
  trade_id: number | null;
  outcome: Outcome;
  realized_net: number | null;
}

export interface TradeRecord {
  id: number;
  prediction_id: string | null;
  timestamp_ms: number;
  symbol: Symbol;
  model: ModelVersion;
  contract_duration: ContractDuration;
  direction: Direction;
  simulated_stake_usdc: number;
  p_market: number | null;
  price_at_open: number | null;
  price_at_close: number | null;
  outcome: Outcome;
  correct: boolean | null;
  realized_net: number | null;
  realized_net_breakdown: any | null;
  suppressed_reason: SuppressedReason;
  gate_structural_reason: string | null;
  gate_adverse_pass: boolean | null;
  resolved: boolean;
  resolved_at_ms: number | null;
  resolution_id: number | null;
}

export interface AlertRecord {
  id: string;
  timestamp_ms: number;
  type: AlertType;
  severity: AlertSeverity;
  message: string;
  context: any;
}

export interface StatusResponse {
  ts: number;
  containers: any[];
  data_pipeline: any;
  pmarket_api: any;
  ewm_state: Record<Symbol, any>;
  model_meta: any[];
  suppression_rules: any[];
  coverage_rate_1h: any;
  gate_status: Record<ModelVersion, any>;
  predictions_per_hour: Record<ModelVersion, number>;
  trades_per_hour: Record<ModelVersion, number>;
  open_trades_count: number;
  system_fee: number;
}

export interface PerformanceSummary {
  model: ModelVersion;
  symbol: Symbol | 'ALL';
  contract_duration: ContractDuration | 'ALL';
  total_trades: number;
  unresolved: number;
  wins: number;
  losses: number;
  accuracy: number;
  realized_net: number;
  realized_net_per_trade: number;
  p_market_avg: number;
  expected_value_empirical: number;
  gate_pass_rate: number;
}
