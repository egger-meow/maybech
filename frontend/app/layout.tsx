import type { Metadata } from 'next';
import './globals.css';
import Sidebar from '@/components/Sidebar';

export const metadata: Metadata = {
  title: 'Maybech Dashboard',
  description: 'Premium UI for Maybech Crypto Trading Bot',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="zh-TW" suppressHydrationWarning>
      <body suppressHydrationWarning>
        <div style={{ display: 'flex', minHeight: '100vh' }}>
          <Sidebar />
          <main style={{ flex: 1, padding: '2rem 3rem', backgroundColor: 'var(--bg-primary)' }}>
            {children}
          </main>
        </div>
      </body>
    </html>
  );
}
