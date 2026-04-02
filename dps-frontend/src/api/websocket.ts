import { useLiveStore } from '../store/liveStore';

class WebSocketManager {
  private ws: WebSocket | null = null;
  private url: string = `${window.location.protocol === 'https:' ? 'wss:' : 'ws:'}//${window.location.host}/ws/live`;
  private reconnectAttempts = 0;
  private maxAttempts = 5;
  private listeners: Record<string, ((data: any) => void)[]> = {};

  connect(token?: string) {
    if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) {
      return;
    }

    const { setConnected } = useLiveStore.getState();
    const wsUrl = token ? `${this.url}?token=${encodeURIComponent(token)}` : this.url;

    this.ws = new WebSocket(wsUrl);

    this.ws.onopen = () => {
      console.log('WebSocket connected');
      setConnected(true);
      this.reconnectAttempts = 0;
    };

    this.ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        const { type, payload } = data;

        // Auto-feed Zustand store
        const liveStore = useLiveStore.getState();
        if (type === 'status') liveStore.setStatus(payload);
        if (type === 'prediction') liveStore.addPrediction(payload);
        if (type === 'trade') liveStore.addTrade(payload);
        if (type === 'alert') liveStore.addAlert(payload);

        // Notify specific listeners
        if (this.listeners[type]) {
          this.listeners[type].forEach(cb => cb(payload));
        }
      } catch (err) {
        console.error('WebSocket message parsing error:', err);
      }
    };

    this.ws.onclose = () => {
      setConnected(false);
      this.ws = null;
      this.attemptReconnect(token);
    };

    this.ws.onerror = (err) => {
      console.error('WebSocket error:', err);
    };
  }

  subscribe(type: string, callback: (data: any) => void) {
    if (!this.listeners[type]) {
      this.listeners[type] = [];
    }
    this.listeners[type].push(callback);
    return () => {
      this.listeners[type] = this.listeners[type].filter(cb => cb !== callback);
    };
  }

  private attemptReconnect(token?: string) {
    if (this.reconnectAttempts >= this.maxAttempts) {
      console.log('Max WebSocket reconnect attempts reached.');
      return;
    }
    const backoff = Math.min(30000, Math.pow(2, this.reconnectAttempts) * 1000);
    this.reconnectAttempts++;
    console.log(`Reconnecting to WebSocket in ${backoff}ms...`);
    setTimeout(() => this.connect(token), backoff);
  }

  disconnect() {
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
  }
}

export const wsManager = new WebSocketManager();
