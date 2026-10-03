/* 与后端沟通的全部胶水：fetch 封装、节流、本地日志总线。
 *
 * 所有页面都从这里拿数据，不直接 fetch —— 这样「ok/data」信封的解析和错误
 * 提示只有一处实现。
 */

import type { Envelope, LogEntry, LogLevel } from './types';

export class ApiError extends Error {
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

// ------------------------------------------------------------------ 请求
export async function request<T>(
  method: string,
  path: string,
  body?: unknown,
): Promise<T> {
  const opts: RequestInit = { method, headers: {} };
  if (body !== undefined && body !== null) {
    opts.headers = { 'Content-Type': 'application/json' };
    opts.body = JSON.stringify(body);
  }
  const resp = await fetch(path, opts);
  let payload: Envelope<T> | null = null;
  try {
    payload = (await resp.json()) as Envelope<T>;
  } catch (err) {
    payload = null;
  }
  if (!payload || payload.ok !== true) {
    const msg = (payload && !payload.ok && payload.error) || 'HTTP ' + resp.status;
    throw new ApiError(String(msg), resp.status);
  }
  return payload.data;
}

export const get = <T,>(path: string) => request<T>('GET', path);
export const post = <T,>(path: string, body?: unknown) =>
  request<T>('POST', path, body ?? {});

// ------------------------------------------------------------------ 本地日志
type LogListener = (entry: LogEntry) => void;

const logListeners = new Set<LogListener>();

/** 订阅「本地产生的」日志（HTTP 失败、输入非法等）。SSE 的日志走另一条路。 */
export function onLocalLog(fn: LogListener): () => void {
  logListeners.add(fn);
  return () => {
    logListeners.delete(fn);
  };
}

export function logLocal(level: LogLevel, text: string): void {
  const entry: LogEntry = { ts: Date.now() / 1000, level, text };
  logListeners.forEach((fn) => {
    try {
      fn(entry);
    } catch (err) {
      console.error(err);
    }
  });
}

/**
 * 带日志的动作封装：失败不抛异常，而是往日志面板写一条 err，返回 undefined。
 * 页面里几乎所有按钮都该用它 —— 原来的 Tkinter 版就是「出错就 log 一条」。
 */
export async function act<T>(
  method: string,
  path: string,
  body?: unknown,
  quiet = false,
): Promise<T | undefined> {
  try {
    return await request<T>(method, path, body);
  } catch (err) {
    if (!quiet) logLocal('err', String((err as Error).message || err));
    return undefined;
  }
}

export const actPost = <T,>(path: string, body?: unknown, quiet = false) =>
  act<T>('POST', path, body, quiet);

export const actGet = <T,>(path: string, quiet = false) =>
  act<T>('GET', path, undefined, quiet);

// ------------------------------------------------------------------ 工具
/** 前置节流：拖动滑块时 60ms 最多发一次请求，松手时再发一次完整的。 */
export function throttle<A extends unknown[]>(
  fn: (...args: A) => void,
  ms: number,
): (...args: A) => void {
  let last = 0;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let lastArgs: A;

  return function throttled(...args: A) {
    lastArgs = args;
    const now = Date.now();
    if (now - last >= ms) {
      last = now;
      fn(...args);
    } else if (!timer) {
      timer = setTimeout(() => {
        timer = null;
        last = Date.now();
        fn(...lastArgs);
      }, ms - (now - last));
    }
  };
}

/** 时间戳（秒）-> ``2026-09-24 18:03``，方案列表用。 */
export function fmtTime(seconds: number): string {
  if (!seconds) return '';
  const d = new Date(seconds * 1000);
  const pad = (n: number) => String(n).padStart(2, '0');
  return (
    d.getFullYear() +
    '-' +
    pad(d.getMonth() + 1) +
    '-' +
    pad(d.getDate()) +
    ' ' +
    pad(d.getHours()) +
    ':' +
    pad(d.getMinutes())
  );
}

/** 日志行的时间前缀 ``[18:03:07]``。 */
export function fmtClock(seconds: number): string {
  const d = seconds ? new Date(seconds * 1000) : new Date();
  const pad = (n: number) => String(n).padStart(2, '0');
  return pad(d.getHours()) + ':' + pad(d.getMinutes()) + ':' + pad(d.getSeconds());
}

export function hex4(n: number | null | undefined): string {
  return '0x' + Number(n || 0).toString(16).toUpperCase().padStart(4, '0');
}