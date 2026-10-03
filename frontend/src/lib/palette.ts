/* 自定义快捷色板。
 *
 * 原来的 8 个快捷色（``color.PALETTE``）是固定的、不可删；这里额外维护一组
 * 用户自己存下来的颜色，存在 localStorage 里，**全局共享** —— 灯光页 / 分区页 /
 * 灯条栏的每一处 ColorRow 看到的是同一份，改一处其它处立刻跟着变。
 *
 * 用 ``useSyncExternalStore`` 而不是 Context：ColorRow 被散落在很多卡片里，
 * 加一层 Provider 要改所有调用点，而这份状态本来就是全局单例。
 */

import { useSyncExternalStore } from 'react';
import { PALETTE } from './color';
import type { Hsv } from './types';

const KEY = 'vml.palette';

/** 最多存这么多，避免把色板挤爆。 */
export const CUSTOM_LIMIT = 16;

let custom: Hsv[] = load();
const listeners = new Set<() => void>();

function isHsv(value: unknown): value is Hsv {
  return (
    Array.isArray(value) &&
    value.length === 3 &&
    value.every((n) => typeof n === 'number' && Number.isFinite(n))
  );
}

function load(): Hsv[] {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter(isHsv)
      .slice(0, CUSTOM_LIMIT)
      .map((hsv) => hsv.map((n) => Math.max(0, Math.min(255, Math.round(n)))) as Hsv);
  } catch {
    // 隐私模式 / 坏数据 —— 当作没有自定义色，不影响其它功能
    return [];
  }
}

function persist() {
  try {
    localStorage.setItem(KEY, JSON.stringify(custom));
  } catch {
    /* 存不进去也不影响本次会话使用 */
  }
}

function emit() {
  listeners.forEach((fn) => fn());
}

export function subscribe(fn: () => void): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

/** 当前的自定义色列表。**引用稳定** —— 只有真的改了才换新数组。 */
export function getCustom(): Hsv[] {
  return custom;
}

export function useCustomPalette(): Hsv[] {
  return useSyncExternalStore(subscribe, getCustom, getCustom);
}

/** 这个颜色是不是已经在色板里了（含 8 个默认色）。 */
export function isSaved(hsv: Hsv): boolean {
  const same = (a: Hsv) => a[0] === hsv[0] && a[1] === hsv[1] && a[2] === hsv[2];
  return PALETTE.some((item) => same(item.hsv)) || custom.some(same);
}

/** 存一个颜色。已经存在（含 8 个默认色）或已存满时返回 false。 */
export function addCustom(hsv: Hsv): boolean {
  if (isSaved(hsv) || custom.length >= CUSTOM_LIMIT) return false;
  custom = [...custom, [hsv[0], hsv[1], hsv[2]]];
  persist();
  emit();
  return true;
}

export function removeCustom(index: number) {
  if (index < 0 || index >= custom.length) return;
  custom = custom.filter((_, i) => i !== index);
  persist();
  emit();
}

/** 色板是不是已经满了（界面上用来禁用「+」）。 */
export function paletteFull(): boolean {
  return custom.length >= CUSTOM_LIMIT;
}
