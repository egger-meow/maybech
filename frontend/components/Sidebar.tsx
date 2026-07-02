"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { ActivitySquare, BarChart3, BellRing, Briefcase, History, LayoutDashboard, Settings2, ShieldCheck } from "lucide-react";

import ThemeToggle from "./ThemeToggle";

const links = [
  { href: "/analysis", label: "市場分析", icon: BarChart3 },
  { href: "/", label: "總覽", icon: LayoutDashboard },
  { href: "/strategies", label: "策略管理", icon: Settings2 },
  { href: "/positions", label: "部位管理", icon: Briefcase },
  { href: "/risk", label: "風險上限", icon: ShieldCheck },
  { href: "/history", label: "交易紀錄", icon: History },
  { href: "/events", label: "事件稽核", icon: ActivitySquare },
  { href: "/notifications", label: "通知管理", icon: BellRing },
];

export default function Sidebar() {
  const pathname = usePathname();
  return (
    <aside className="sidebar">
      <div className="brand-block">
        <h1>Maybech</h1>
        <p>交易管理工作台</p>
      </div>
      <nav className="sidebar-nav" aria-label="主要導覽">
        {links.map(({ href, label, icon: Icon }) => (
          <Link key={href} href={href} className={pathname === href ? "nav-link active" : "nav-link"}>
            <Icon size={20} />
            {label}
          </Link>
        ))}
      </nav>
      <div className="theme-slot"><ThemeToggle /></div>
    </aside>
  );
}
