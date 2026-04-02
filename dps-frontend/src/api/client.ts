import axios from 'axios';

// Create a basic Axios client with config
export const apiClient = axios.create({
  baseURL: '', // Uses Vite proxy in development, Nginx proxy in production
});

// Using a prompt for auth in development mode to avoid hardcoding credentials
let authHeaders: Record<string, string> = {};

// We can intercept requests to attach headers
apiClient.interceptors.request.use((config) => {
  if (Object.keys(authHeaders).length > 0) {
    config.headers = { ...config.headers, ...authHeaders } as any;
  }
  return config;
});

// A lightweight retry interceptor for 503s
apiClient.interceptors.response.use(
  (response) => response,
  async (error) => {
    const config = error.config;

    if (error.response?.status === 401) {
      // Basic handling: user prompt for credentials
      if (typeof window !== 'undefined') {
        const credentials = window.prompt('Enter API credentials (username:password):');
        if (credentials) {
          const auth = 'Basic ' + btoa(credentials);
          authHeaders['Authorization'] = auth;
          config.headers['Authorization'] = auth;
          return axios(config);
        }
      }
    }

    if (!config || !config.retry) {
      config.retry = 0;
    }

    if (error.response?.status === 503 && config.retry < 2) {
      config.retry += 1;
      await new Promise((resolve) => setTimeout(resolve, 500));
      return axios(config);
    }

    return Promise.reject(error);
  }
);
