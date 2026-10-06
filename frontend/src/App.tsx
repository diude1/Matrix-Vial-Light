import { useEffect, useState } from 'react';
import { DeviceBar } from './components/DeviceBar';
import { LogPanel } from './components/LogPanel';
import { PresetPanel } from './components/PresetPanel';
import { TopBar, type PageDef } from './components/TopBar';
import { applyTheme, currentTheme, type Theme } from './lib/theme';
import { InfoPage } from './pages/InfoPage';
import { LightPage } from './pages/LightPage';
import { PerKeyPage } from './pages/PerKeyPage';
import { ZonePage } from './pages/ZonePage';
import { SessionProvider } from './store/useSession';

const PAGES: PageDef[] = [
  { id: 'light', label: '灯光' },
  { id: 'zone', label: '分区' },
  { id: 'perkey', label: '逐键' },
  { id: 'info', label: '信息' },
];

const TAB_KEY = 'vml.tab';

function Shell() {
  const [active, setActive] = useState<string>(() => {
    const saved = window.localStorage.getItem(TAB_KEY);
    // 上次停在已删除的「配列」页时回落到灯光页
    return PAGES.some((page) => page.id === saved) ? String(saved) : 'light';
  });
  const [theme, setTheme] = useState<Theme>(() => currentTheme());

  useEffect(() => {
    window.localStorage.setItem(TAB_KEY, active);
  }, [active]);

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  return (
    <>
      <TopBar
        pages={PAGES}
        active={active}
        onSelect={setActive}
        theme={theme}
        onToggleTheme={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
      />
      <DeviceBar />
      <main>
        {active === 'light' ? <LightPage /> : null}
        {active === 'zone' ? <ZonePage /> : null}
        {active === 'perkey' ? <PerKeyPage /> : null}
        {active === 'info' ? <InfoPage /> : null}
        {/* 方案库是整套灯光的状态，跟页签无关 —— 放在外壳里，四个页签都能存取 */}
        <PresetPanel />
      </main>
      <LogPanel />
    </>
  );
}

export default function App() {
  return (
    <SessionProvider>
      <Shell />
    </SessionProvider>
  );
}
