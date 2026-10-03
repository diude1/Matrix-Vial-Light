/* 浅色 / 深色主题。文档根节点的 data-theme 属性驱动 styles.css 里的两套
 * CSS 变量，所以切换主题不需要重绘任何图形。
 */

export type Theme = 'light' | 'dark';

const KEY = 'vml.theme';
const THEMES: Theme[] = ['light', 'dark'];

export function currentTheme(): Theme {
  try {
    const saved = localStorage.getItem(KEY);
    if (saved === 'light' || saved === 'dark') return saved;
    if (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) {
      return 'dark';
    }
  } catch (err) {
    /* 隐私模式 */
  }
  return 'light';
}

export function applyTheme(theme: Theme): void {
  const next: Theme = THEMES.indexOf(theme) >= 0 ? theme : 'light';
  document.documentElement.setAttribute('data-theme', next);
  try {
    localStorage.setItem(KEY, next);
  } catch (err) {
    /* 忽略 */
  }
}