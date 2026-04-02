import { useEffect, useRef, useState } from 'react';
import { createChart } from 'lightweight-charts';
import type { IChartApi } from 'lightweight-charts';
import { Tally3, Loader2 } from 'lucide-react';
import { endpoints } from '../../api/endpoints';

export const PriceOverlay = () => {
  const chartContainerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | any>(null);
  const seriesRef = useRef<any>(null); // Use any to bypass version drift in CandlestickSeries types
  const [loading, setLoading] = useState(true);
  const [dataGap, setDataGap] = useState(false);

  useEffect(() => {
    if (!chartContainerRef.current) return;

    // Initialize Chart
    const chart = createChart(chartContainerRef.current, {
      layout: {
        background: { type: 'solid' as any, color: 'transparent' },
        textColor: '#94a3b8',
      },
      grid: {
        vertLines: { color: 'rgba(255, 255, 255, 0.05)' },
        horzLines: { color: 'rgba(255, 255, 255, 0.05)' },
      },
      timeScale: {
        timeVisible: true,
        secondsVisible: false,
      },
      rightPriceScale: {
        borderVisible: false,
      },
    });

    const series = (chart as any).addCandlestickSeries({
      upColor: '#4ade80',
      downColor: '#f87171',
      borderVisible: false,
      wickUpColor: '#4ade80',
      wickDownColor: '#f87171',
    });

    chartRef.current = chart;
    seriesRef.current = series;

    // Handle Resize
    const handleResize = () => {
      if (chartContainerRef.current) {
        chart.applyOptions({ width: chartContainerRef.current.clientWidth });
      }
    };
    window.addEventListener('resize', handleResize);

    return () => {
      window.removeEventListener('resize', handleResize);
      chart.remove();
    };
  }, []);

  useEffect(() => {
    let mounted = true;
    
    // Fetch actual candlestick data from backend
    const fetchCandles = async () => {
      setLoading(true);
      try {
        const resp = await endpoints.getOHLCV("BTCUSDT");
        
        if (mounted && seriesRef.current && chartRef.current) {
          if (resp && resp.data && resp.data.length > 0 && !resp.data_gap) {
            // Map parquet columns to Recharts Lightweight-Charts format
            const mappedData = resp.data.map((row: any) => ({
              time: Math.floor(row.timestamp_ms / 1000) as any,
              open: row.open,
              high: row.high,
              low: row.low,
              close: row.close
            }));
            
            seriesRef.current.setData(mappedData);
            chartRef.current.timeScale().fitContent();
            setDataGap(false);
          } else {
            // No data
            seriesRef.current.setData([]);
            setDataGap(true);
          }
        }
      } catch (e) {
        console.error("Failed to fetch klines:", e);
        if (mounted) setDataGap(true);
      }
      
      if (mounted) setLoading(false);
    };
    
    fetchCandles();
    return () => { mounted = false; };
  }, []);

  return (
    <div className="h-full flex flex-col p-6 overflow-y-auto animate-in fade-in duration-500 space-y-6 relative">
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
         <div>
            <h1 className="text-2xl font-bold tracking-tight text-[var(--color-text)] flex items-center">
              <Tally3 className="w-6 h-6 mr-3 text-[var(--color-primary)]" />
              Price & Market Overlay
            </h1>
            <p className="text-[var(--color-text-muted)] text-sm mt-1">Orderbook mid-price trace superimposed with direction model firing events.</p>
         </div>
      </div>

      <div className="flex-1 min-h-[500px] bg-[var(--color-bg)] border border-[var(--color-border)] rounded-xl shadow-sm relative overflow-hidden">
         {loading && (
            <div className="absolute inset-0 z-10 bg-[var(--color-surface)]/80 backdrop-blur flex items-center justify-center flex-col text-[var(--color-text-muted)]">
               <Loader2 className="w-8 h-8 animate-spin mb-4 text-[var(--color-primary)]" />
               Fetching OHLCV data...
            </div>
         )}
         {!loading && dataGap && (
            <div className="absolute inset-0 z-10 bg-[var(--color-surface)] flex items-center justify-center flex-col text-[var(--color-text-muted)] p-8 text-center">
               <Tally3 className="w-12 h-12 mb-4 text-[var(--color-text-faint)]" />
               <h3 className="text-lg font-bold text-[var(--color-text)] mb-2">Awaiting Kline Sync</h3>
               <p className="max-w-md">The background parquet ingestion pipeline has not populated OHLCV data for this period. Once Bybit data is synced to <code className="bg-black/20 px-1 py-0.5 rounded text-xs mx-1">/data/parquet/klines</code>, this chart will update automatically.</p>
            </div>
         )}
         <div ref={chartContainerRef} className="absolute inset-0 w-full h-full" />
      </div>
    </div>
  );
};
