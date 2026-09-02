import { useEffect, useRef, useState } from 'react';
import { Outlet, useLocation } from 'react-router-dom';
import Sidebar from './Sidebar';
import Header from './Header';
export default function Layout() {
  const [isMobileSidebarOpen, setIsMobileSidebarOpen] = useState(false);
  const mainRef = useRef<HTMLElement>(null);
  const { pathname } = useLocation();

  // Scroll main content container to top on every route change
  useEffect(() => {
    if (mainRef.current) {
      mainRef.current.scrollTop = 0;
    }
  }, [pathname]);

  return (
    <div className="flex min-h-screen bg-[var(--color-bg-primary)]">
      <Sidebar isOpen={isMobileSidebarOpen} onClose={() => setIsMobileSidebarOpen(false)} />
      <div className="flex-1 flex flex-col md:ml-64 min-w-0 h-screen overflow-hidden">
        <Header onMenuClick={() => setIsMobileSidebarOpen(true)} />
        <main ref={mainRef} className="flex-1 p-2.5 sm:p-4 md:p-6 overflow-y-auto overflow-x-hidden flex flex-col min-h-0 min-w-0 w-full">
          <Outlet />
        </main>
      </div>
    </div>
  );
}

