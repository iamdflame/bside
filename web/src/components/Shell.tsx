import { Link, NavLink, Outlet } from "react-router-dom";
import "./shell.css";

export function Logo({ size = 26 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden className="logo-disc">
      <rect width="32" height="32" rx="7" fill="var(--ink-0)" />
      <circle cx="16" cy="16" r="9.5" fill="none" stroke="var(--signal)" strokeWidth="2.5" />
      <circle cx="16" cy="16" r="6.5" fill="none" stroke="var(--line-strong)" strokeWidth="1" />
      <circle cx="16" cy="16" r="3" fill="var(--paper)" />
    </svg>
  );
}

export function Shell() {
  return (
    <div className="shell">
      <header className="topbar">
        <div className="container topbar__inner">
          <Link to="/" className="brand">
            <Logo />
            <span className="brand__name display">B‑Side</span>
            <span className="brand__tag caps">every episode's other half</span>
          </Link>
          <nav className="topnav">
            <NavLink to="/" end className={({ isActive }) => (isActive ? "topnav__link topnav__link--on" : "topnav__link")}>
              Shows
            </NavLink>
            <NavLink to="/judge" className={({ isActive }) => (isActive ? "topnav__link topnav__link--on" : "topnav__link")}>
              Judge mode
            </NavLink>
            <a
              className="topnav__link"
              href="https://github.com/iamdflame/bside"
              target="_blank"
              rel="noreferrer"
            >
              Source ↗
            </a>
          </nav>
        </div>
      </header>
      <main className="shell__main">
        <Outlet />
      </main>
      <footer className="footer">
        <div className="container footer__inner">
          <span className="caps">Orchestrated by Genblaze · Archived on Backblaze B2</span>
          <span className="caps num">every asset hash‑verified</span>
        </div>
      </footer>
    </div>
  );
}
