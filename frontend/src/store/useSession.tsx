/* 会话状态：SSE 订阅 + 快照 + 日志环形缓冲。
 *
 * 设计原则与旧版 app.js 一致：**状态只有一份**，页面从 Context 里读快照，
 * 页面自己持有的只有「正在拖动 / 正在输入」这类交互态，避免被 SSE 推送冲掉。
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { actGet, onLocalLog } from '../lib/api';
import type { DeviceInfo, LogEntry, Snapshot } from '../lib/types';

/** 日志面板最多留多少行（与旧版一致）。 */
const LOG_LIMIT = 400;

interface SessionValue {
  snapshot: Snapshot | null;
  logs: LogEntry[];
  online: boolean;
  busy: number;
  devices: DeviceInfo[];
  setDevices: (list: DeviceInfo[]) => void;
  pushLog: (entry: LogEntry) => void;
  clearLogs: () => void;
}

const SessionContext = createContext<SessionValue | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [online, setOnline] = useState(true);
  const [busy, setBusy] = useState(0);
  const [devices, setDevices] = useState<DeviceInfo[]>([]);

  const pushLog = useCallback((entry: LogEntry) => {
    setLogs((prev) => {
      const next = prev.length >= LOG_LIMIT ? prev.slice(prev.length - LOG_LIMIT + 1) : prev.slice();
      next.push(entry);
      return next;
    });
  }, []);

  const clearLogs = useCallback(() => setLogs([]), []);

  // 本地产生的日志（HTTP 失败、输入非法）也汇进同一个面板
  useEffect(() => onLocalLog(pushLog), [pushLog]);

  // SSE：state / log / busy 三类事件
  useEffect(() => {
    let closed = false;
    const source = new EventSource('/api/events');

    source.onopen = () => setOnline(true);
    source.onerror = () => setOnline(false);
    source.onmessage = (ev: MessageEvent<string>) => {
      let payload: { kind: string; data: unknown };
      try {
        payload = JSON.parse(ev.data);
      } catch (err) {
        return;
      }
      if (payload.kind === 'state') {
        setSnapshot(payload.data as Snapshot);
      } else if (payload.kind === 'log') {
        pushLog(payload.data as LogEntry);
      } else if (payload.kind === 'busy') {
        setBusy((payload.data as { count: number }).count);
      }
    };

    return () => {
      closed = true;
      source.close();
      void closed;
    };
  }, [pushLog]);

  // 首屏补历史日志 + 拉一次快照（SSE 也会补一帧，这里只是兜底）
  const booted = useRef(false);
  useEffect(() => {
    if (booted.current) return;
    booted.current = true;
    actGet<LogEntry[]>('/api/logs', true).then((list) => {
      (list || []).forEach(pushLog);
    });
    actGet<Snapshot>('/api/state', true).then((snap) => {
      if (snap) setSnapshot(snap);
    });
  }, [pushLog]);

  const value = useMemo<SessionValue>(
    () => ({
      snapshot,
      logs,
      online,
      busy,
      devices,
      setDevices,
      pushLog,
      clearLogs,
    }),
    [snapshot, logs, online, busy, devices, pushLog, clearLogs],
  );

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

export function useSession(): SessionValue {
  const ctx = useContext(SessionContext);
  if (!ctx) throw new Error('useSession 必须在 SessionProvider 内使用');
  return ctx;
}

/** 只要快照的便捷钩子。 */
export function useSnapshot(): Snapshot | null {
  return useSession().snapshot;
}
