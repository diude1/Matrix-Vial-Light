import { useEffect, useRef, useState } from 'react';

interface SliderProps {
  label: string;
  min: number;
  max: number;
  value: number;
  /** 拖动过程中持续回调（走节流下发） */
  onInput?: (value: number) => void;
  /** 松手时回调（立刻下发一次完整值） */
  onCommit?: (value: number) => void;
  disabled?: boolean;
}

/**
 * 带数值显示的滑块。
 *
 * 拖动时**不让服务端状态回填** —— 否则 SSE 每次推快照都会把滑块拽回去，
 * 手感是「拖不动」。松手后才恢复跟随。
 */
export function Slider({ label, min, max, value, onInput, onCommit, disabled }: SliderProps) {
  const [local, setLocal] = useState(value);
  const dragging = useRef(false);

  useEffect(() => {
    if (!dragging.current) setLocal(value);
  }, [value]);

  return (
    <div className="slider-row">
      <label>{label}</label>
      <input
        type="range"
        min={min}
        max={max}
        value={local}
        disabled={disabled}
        onPointerDown={() => {
          dragging.current = true;
        }}
        onPointerUp={() => {
          dragging.current = false;
          onCommit?.(local);
        }}
        onKeyUp={() => {
          dragging.current = false;
          onCommit?.(local);
        }}
        onChange={(ev) => {
          const next = Number(ev.target.value);
          setLocal(next);
          onInput?.(next);
        }}
      />
      <span className="val">{local}</span>
    </div>
  );
}
