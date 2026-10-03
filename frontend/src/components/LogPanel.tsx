import { useEffect, useRef } from 'react';
import { fmtClock } from '../lib/api';
import { useSession } from '../store/useSession';

/** 底部固定日志面板。SSE 的日志与本地错误都汇到这里。 */
export function LogPanel() {
  const { logs, clearLogs, online } = useSession();
  const boxRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const box = boxRef.current;
    if (box) box.scrollTop = box.scrollHeight;
  }, [logs.length]);

  return (
    <footer className="logbar">
      <div className="logbar-head">
        <span>日志</span>
        <span className={online ? 'pill ok' : 'pill err'}>
          {online ? '事件流已连接' : '事件流已断开'}
        </span>
        <span className="spacer" />
        <button className="btn tiny" type="button" onClick={clearLogs}>
          清空
        </button>
      </div>
      <div className="log" ref={boxRef}>
        {logs.map((entry, index) => (
          <div key={index} className={'log-line ' + (entry.level || 'info')}>
            [{fmtClock(entry.ts)}] {entry.text}
          </div>
        ))}
      </div>
    </footer>
  );
}
