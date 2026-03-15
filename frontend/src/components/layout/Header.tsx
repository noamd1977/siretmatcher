import { Building2, Search, Crosshair, BarChart3 } from 'lucide-react';
import { NavLink } from 'react-router-dom';

const links = [
  { to: '/', label: 'Recherche', icon: Search },
  { to: '/match', label: 'Matching', icon: Crosshair },
  { to: '/dashboard', label: 'Dashboard', icon: BarChart3 },
];

export function Header() {
  return (
    <header className="bg-[#1e3a5f] text-white shadow-md">
      <div className="mx-auto flex max-w-screen-2xl items-center gap-6 px-6 py-3">
        <div className="flex items-center gap-2">
          <Building2 size={24} />
          <span className="text-lg font-bold">SIRET Matcher</span>
        </div>
        <nav className="flex gap-1">
          {links.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              end={to === '/'}
              className={({ isActive }) =>
                `flex items-center gap-1.5 rounded-md px-3 py-2 text-sm font-medium transition ${
                  isActive
                    ? 'bg-white/15 text-white'
                    : 'text-blue-200 hover:bg-white/10 hover:text-white'
                }`
              }
            >
              <Icon size={16} />
              {label}
            </NavLink>
          ))}
        </nav>
      </div>
    </header>
  );
}
