import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { App } from './App';
import { installBrowserCompositeProbe } from './perf/browserCompositeProbe.js';
import './styles.css';

const rootElement = document.getElementById('root');
if (!rootElement) throw new Error('Application root is missing.');

// Evidence hook for Playwright browser-performance probes and manual diagnostics.
installBrowserCompositeProbe(window);

createRoot(rootElement).render(
  <StrictMode>
    <App />
  </StrictMode>,
);

if ('serviceWorker' in navigator && import.meta.env.PROD) {
  window.addEventListener('load', () => {
    void navigator.serviceWorker.register('/sw.js');
  });
}
