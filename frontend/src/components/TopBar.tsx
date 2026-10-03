import type { Theme } from '../lib/theme';
import { useSession } from '../store/useSession';

export interface PageDef {
  id: string;
  label: string;
}

interface TopBarProps {
  pages: PageDef[];
  active: string;
  onSelect: (id: string) => void;
  theme: Theme;
  onToggleTheme: () => void;
}

export function TopBar({ pages, active, onSelect, theme, onToggleTheme }: TopBarProps) {
  const { snapshot, online } = useSession();

  const backendPill = () => {
    if (!online) return { text: '连接已断开', cls: 'pill err' };
    if (!snapshot || !snapshot.connected) return { text: '未连接', cls: 'pill' };
    return {
      text: snapshot.backend_short || '已连接',
      cls: 'pill ' + (snapshot.backend ? 'ok' : 'warn'),
    };
  };

  const pill = backendPill();

  return (
    <header className="topbar">
      <div className="brand">
        <span className="brand-dot" />
        <span className="brand-name">vial-matrix-light</span>
        <span className="brand-sub">Matrix / Vial 键盘灯光调整</span>
      </div>
      <nav className="tabs">
        {pages.map((page) => (
          <button
            key={page.id}
            type="button"
            className={'tab' + (page.id === active ? ' active' : '')}
            onClick={() => onSelect(page.id)}
          >
            {page.label}
          </button>
        ))}
      </nav>
      <div className="topbar-right">
        <span className={pill.cls}>{pill.text}</span>
        <button
          className="icon-btn"
          type="button"
          title={theme === 'dark' ? '切换到浅色主题' : '切换到深色主题'}
          onClick={onToggleTheme}
        >
          {theme === 'dark' ? '☀' : '☾'}
        </button>
      </div>
    </header>
  );
}
