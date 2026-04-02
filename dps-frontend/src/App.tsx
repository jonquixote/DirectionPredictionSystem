import { BrowserRouter as Router, Routes, Route, Navigate } from 'react-router-dom';
import { useEffect } from 'react';
import { Shell } from './components/layout/Shell';
import { useSettingsStore } from './store/settingsStore';
import { wsManager } from './api/websocket';
import { LiveStatus } from './pages/Status/LiveStatus';
import { PredictionExplorer } from './pages/Predictions/PredictionExplorer';
import { TradeExplorer } from './pages/Trades/TradeExplorer';
import { PerformanceDashboard } from './pages/Performance/PerformanceDashboard';
import { FeatureAnalysis } from './pages/FeatureAnalysis/FeatureAnalysis';
import { ModelComparison } from './pages/ModelComparison/ModelComparison';
import { AlertsPanel } from './pages/Alerts/AlertsPanel';
import { PMarketEV } from './pages/PMarketEV/PMarketEV';
import { PriceOverlay } from './pages/PriceOverlay/PriceOverlay';
import { RawLogExplorer } from './pages/RawLogExplorer/RawLogExplorer';
import { StatisticalTools } from './pages/StatisticalTools/StatisticalTools';

function App() {
  const theme = useSettingsStore((state) => state.theme);

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme);
  }, [theme]);

  useEffect(() => {
    wsManager.connect();
    return () => {
      wsManager.disconnect();
    };
  }, []);

  return (
    <Router>
      <Shell>
        <Routes>
          <Route path="/" element={<Navigate to="/status" replace />} />
          <Route path="/status" element={<LiveStatus />} />
          <Route path="/predictions" element={<PredictionExplorer />} />
          <Route path="/trades" element={<TradeExplorer />} />
          <Route path="/performance" element={<PerformanceDashboard />} />
          <Route path="/features" element={<FeatureAnalysis />} />
          <Route path="/models" element={<ModelComparison />} />
          <Route path="/alerts" element={<AlertsPanel />} />
          <Route path="/pmarket" element={<PMarketEV />} />
          <Route path="/price" element={<PriceOverlay />} />
          <Route path="/logs" element={<RawLogExplorer />} />
          <Route path="/stats" element={<StatisticalTools />} />
          {/* Catch-all */}
          <Route path="*" element={<Navigate to="/status" replace />} />
        </Routes>
      </Shell>
    </Router>
  );
}

export default App;
