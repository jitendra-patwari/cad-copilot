import React from 'react';
import ReactDOM from 'react-dom/client';
import './styles.css';

ReactDOM.createRoot(document.getElementById('root') as HTMLElement).render(
  <React.StrictMode>
    <div className="flex h-screen w-screen items-center justify-center bg-slate-950 text-slate-100">
      <div className="text-center">
        <h1 className="text-2xl font-bold tracking-tight">CAD Copilot</h1>
        <p className="mt-2 text-sm text-slate-400">Desktop Foundation</p>
      </div>
    </div>
  </React.StrictMode>
);
