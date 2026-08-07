import { useState } from 'react';
import { Outlet } from 'react-router-dom';
import Sidebar from './Sidebar';
import Header from './Header';

export default function Layout() {
  const [isMobileSidebarOpen, setIsMobileSidebarOpen] = useState(false);

  return (
    <div
      className="flex min-h-screen"
      style={{ backgroundColor: 'var(--color-bg-primary)', color: 'var(--color-text-primary)' }}
    >
      <Sidebar isOpen={isMobileSidebarOpen} onClose={() => setIsMobileSidebarOpen(false)} />
      <div className="flex-1 flex flex-col md:ml-64 min-w-0 h-screen overflow-hidden">
        <Header onMenuClick={() => setIsMobileSidebarOpen(true)} />
        <main
          className="flex-1 p-4 md:p-6 overflow-y-auto flex flex-col min-h-0"
          style={{ backgroundColor: 'var(--color-bg-primary)' }}
        >
          <Outlet />
        </main>
      </div>
    </div>
  );
}
