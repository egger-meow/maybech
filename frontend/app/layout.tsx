import type { Metadata } from "next";
import AuthenticationGate from "@/components/AuthenticationGate";
import Sidebar from "@/components/Sidebar";
import "./globals.css";

export const metadata: Metadata = {
  title: "Maybech",
  description: "Maybech trading workspace",
};

const themeInitScript = `
(function () {
  try {
    var theme = localStorage.getItem("theme");
    if (theme !== "dark" && theme !== "light") {
      theme = "light";
    }
    document.documentElement.setAttribute("data-theme", theme);
  } catch (_) {
    document.documentElement.setAttribute("data-theme", "light");
  }
})();
`;

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="zh-TW" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeInitScript }} />
      </head>
      <body>
        <div className="app-shell">
          <Sidebar />
          <main className="app-main">
            <AuthenticationGate>{children}</AuthenticationGate>
          </main>
        </div>
      </body>
    </html>
  );
}
