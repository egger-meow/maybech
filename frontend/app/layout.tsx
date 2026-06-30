import type { Metadata } from 'next';
import './globals.css';
import Sidebar from '@/components/Sidebar';

export const metadata: Metadata = {
  title: 'Maybech Trading Workspace',
  description: 'Local-first strategy and logical-position management for OKX perpetuals',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="zh-TW" suppressHydrationWarning>
      <body suppressHydrationWarning>
        <div className="app-shell">
          <Sidebar />
          <main className="app-main">
            {children}
          </main>
        </div>
      </body>
    </html>
  );
}
