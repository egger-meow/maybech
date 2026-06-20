"use client";

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { LayoutDashboard, Briefcase, Settings2, History, ActivitySquare } from 'lucide-react';
import ThemeToggle from './ThemeToggle';

export default function Sidebar() {
  const pathname = usePathname();

  const links = [
    { href: '/', label: '儀表板 (Dashboard)', icon: <LayoutDashboard size={20} /> },
    { href: '/strategies', label: '策略管理 (Strategies)', icon: <Settings2 size={20} /> },
    { href: '/positions', label: '倉位管理 (Positions)', icon: <Briefcase size={20} /> },
    { href: '/history', label: '歷史記錄 (History)', icon: <History size={20} /> },
    { href: '/events', label: '系統日誌 (Events)', icon: <ActivitySquare size={20} /> },
  ];

  return (
    <aside style={{
      width: '260px',
      height: '100vh',
      borderRight: '1px solid var(--border-color)',
      padding: '2rem 1rem',
      display: 'flex',
      flexDirection: 'column',
      gap: '2rem',
      position: 'sticky',
      top: 0
    }}>
      <div>
        <h1 style={{ fontSize: '1.5rem', fontWeight: 800, color: 'var(--accent-primary)', marginBottom: '0.5rem' }}>Maybech</h1>
        <p style={{ color: 'var(--text-muted)', fontSize: '0.875rem' }}>Crypto Trading Bot</p>
      </div>

      <nav style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem', flex: 1 }}>
        {links.map((link) => (
          <Link
            key={link.href}
            href={link.href}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '0.75rem',
              padding: '0.75rem 1rem',
              borderRadius: 'var(--radius-sm)',
              color: pathname === link.href ? 'var(--accent-primary)' : 'var(--text-secondary)',
              backgroundColor: pathname === link.href ? 'var(--bg-secondary)' : 'transparent',
              fontWeight: pathname === link.href ? 600 : 500,
              textDecoration: 'none',
              transition: 'all 0.2s ease',
            }}
          >
            {link.icon}
            {link.label}
          </Link>
        ))}
      </nav>

      <div style={{ marginTop: 'auto', paddingTop: '2rem', borderTop: '1px solid var(--border-color)' }}>
        <ThemeToggle />
      </div>
    </aside>
  );
}
