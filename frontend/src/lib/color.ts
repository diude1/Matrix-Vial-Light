/* 颜色换算与色板。**逐行对应 vial_light/colors.py**，不要随手改成浮点算法 ——
 * 固件吃的是 0-255 的整数 HSV，这里算出来的值会直接写进键盘。
 */

import type { Hsv } from './types';

export function clamp(v: number, lo = 0, hi = 255): number {
  const n = Math.trunc(Number(v));
  if (!isFinite(n)) return lo;
  return Math.min(hi, Math.max(lo, n));
}

/** HSV(0-255) -> [r,g,b]。 */
export function hsvToRgb(h: number, s: number, v: number): [number, number, number] {
  h = clamp(h);
  s = clamp(s);
  v = clamp(v);
  if (s === 0) return [v, v, v];
  const region = Math.floor(h / 43);
  const remainder = (h - region * 43) * 6;
  const p = (v * (255 - s)) >> 8;
  const q = (v * (255 - ((s * remainder) >> 8))) >> 8;
  const t = (v * (255 - ((s * (255 - remainder)) >> 8))) >> 8;
  switch (region) {
    case 0:
      return [v, t, p];
    case 1:
      return [q, v, p];
    case 2:
      return [p, v, t];
    case 3:
      return [p, q, v];
    case 4:
      return [t, p, v];
    default:
      return [v, p, q];
  }
}

/** [r,g,b] -> HSV(0-255)。 */
export function rgbToHsv(r: number, g: number, b: number): Hsv {
  r = clamp(r);
  g = clamp(g);
  b = clamp(b);
  const mx = Math.max(r, g, b);
  const mn = Math.min(r, g, b);
  const delta = mx - mn;
  const v = mx;
  if (mx === 0) return [0, 0, 0];
  const s = Math.trunc((delta * 255) / mx);
  if (delta === 0) return [0, s, v];
  let h: number;
  if (mx === r) h = Math.trunc((43 * ((g - b) / delta)) / 6) % 256;
  else if (mx === g) h = Math.trunc((43 * ((b - r) / delta + 2)) / 6) % 256;
  else h = Math.trunc((43 * ((r - g) / delta + 4)) / 6) % 256;
  if (h < 0) h += 256;
  return [h, s, v];
}

const hex2 = (n: number) => clamp(n).toString(16).padStart(2, '0');

export function rgbToHex(r: number, g: number, b: number): string {
  return '#' + hex2(r) + hex2(g) + hex2(b);
}

export function hsvToHex(h: number, s: number, v: number): string {
  const rgb = hsvToRgb(h, s, v);
  return rgbToHex(rgb[0], rgb[1], rgb[2]);
}

export function hsvHex(hsv: Hsv): string {
  return hsvToHex(hsv[0], hsv[1], hsv[2]);
}

export function hexToRgb(text: string): [number, number, number] {
  if (!text) throw new Error('颜色值不能为空');
  let t = String(text).trim().replace(/^#/, '');
  if (t.length === 3) t = t.split('').map((c) => c + c).join('');
  if (t.length !== 6 || !/^[0-9a-fA-F]{6}$/.test(t)) {
    throw new Error('颜色格式应为 #rrggbb');
  }
  return [
    parseInt(t.slice(0, 2), 16),
    parseInt(t.slice(2, 4), 16),
    parseInt(t.slice(4, 6), 16),
  ];
}

export function hexToHsv(text: string): Hsv {
  const rgb = hexToRgb(text);
  return rgbToHsv(rgb[0], rgb[1], rgb[2]);
}

/** 界面快捷色板 —— 与 colors.PALETTE 一致。 */
export const PALETTE: { name: string; hsv: Hsv }[] = [
  { name: '红', hsv: [0, 255, 255] },
  { name: '橙', hsv: [21, 255, 255] },
  { name: '黄', hsv: [43, 255, 255] },
  { name: '绿', hsv: [85, 255, 255] },
  { name: '青', hsv: [128, 255, 255] },
  { name: '蓝', hsv: [171, 255, 255] },
  { name: '紫', hsv: [213, 255, 255] },
  { name: '粉', hsv: [235, 180, 255] },
];

/** 亮度感知的对比色（原配列页用 130 做阈值）。 */
export function textOn(hsv: Hsv): string {
  const rgb = hsvToRgb(hsv[0], hsv[1], hsv[2]);
  const lum = 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2];
  return lum > 130 ? '#3a3a3a' : '#ffffff';
}