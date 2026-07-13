"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { FlipHorizontal2, Globe2, LayoutList, Microscope } from "lucide-react";

const tabs = [
  { href: "/analysis/overview", label: "市場總覽", icon: Globe2 },
  { href: "/analysis/markets", label: "商品列表", icon: LayoutList },
  { href: "/analysis/research", label: "K 線研究", icon: Microscope },
  { href: "/analysis/adam-theory", label: "二次反射", icon: FlipHorizontal2 },
];

export default function AnalysisLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  return (
    <div className="page-stack">
      <header className="page-header"><div><h1>市場分析</h1><p>市場總覽掌握整體大盤氛圍；商品列表看全商品行情；K 線研究以 K 線為核心，可勾選疊加支撐壓力層級、另開未平倉量子圖等資料，把證據當作提案；二次反射以亞當理論的點反射手法畫出幾何鏡射路徑，同樣不直接當作交易指令。</p></div></header>
      <nav className="section-tabs" aria-label="市場分析子頁面">
        {tabs.map(({ href, label, icon: Icon }) => (
          <Link key={href} href={href} className={pathname?.startsWith(href) ? "section-tab active" : "section-tab"}>
            <Icon size={16} />
            {label}
          </Link>
        ))}
      </nav>
      {children}
    </div>
  );
}
