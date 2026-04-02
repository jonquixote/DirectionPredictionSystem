import { create } from 'zustand';
import type { ModelVersion, Symbol, Direction, Outcome, ContractDuration } from '../types';

export interface FilterState {
  model: ModelVersion[];
  symbol: Symbol[];
  from_ms: number | null;
  to_ms: number | null;
  direction: Direction | 'ALL';
  suppressed: boolean | 'ALL';
  warmup: boolean | 'ALL';
  outcome: Outcome | 'ALL';
  contract_duration: ContractDuration | 'ALL';
  divergence_min: number | null;
  divergence_max: number | null;
  pmodel_min: number | null;
  pmodel_max: number | null;
  net_sign: 'positive' | 'negative' | 'ALL';
  settled: boolean | 'ALL';

  setFilters: (filters: Partial<Omit<FilterState, 'setFilters' | 'clearFilters'>>) => void;
  clearFilters: () => void;
}

const initialState: Omit<FilterState, 'setFilters' | 'clearFilters'> = {
  model: [],
  symbol: [],
  from_ms: null,
  to_ms: null,
  direction: 'ALL',
  suppressed: 'ALL',
  warmup: 'ALL',
  outcome: 'ALL',
  contract_duration: 'ALL',
  divergence_min: null,
  divergence_max: null,
  pmodel_min: null,
  pmodel_max: null,
  net_sign: 'ALL',
  settled: 'ALL',
};

export const useFilterStore = create<FilterState>((set) => ({
  ...initialState,
  setFilters: (filters) => set((state) => ({ ...state, ...filters })),
  clearFilters: () => set(initialState),
}));
