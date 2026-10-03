import { useEffect, useRef } from 'react';
import { clamp, hsvToHex } from '../lib/color';

interface HueStripProps {
  /** 0-255 */
  hue: number;
  sat: number;
  val: number;
  disabled?: boolean;
  onPick: (hue: number) => void;
}

/** 色相条：Canvas 画 256 级渐变，拖动即时回调。 */
export function HueStrip({ hue, sat, val, disabled, onPick }: HueStripProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const dragging = useRef(false);

  // 重画渐变（尺寸变化 / 主题切换后都要重画，因为高度由 CSS 决定）
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const draw = () => {
      const ctx = canvas.getContext('2d');
      if (!ctx) return;
      const width = canvas.clientWidth || 380;
      const height = canvas.clientHeight || 30;
      const dpr = window.devicePixelRatio || 1;
      canvas.width = Math.max(1, Math.round(width * dpr));
      canvas.height = Math.max(1, Math.round(height * dpr));
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      for (let x = 0; x < width; x += 1) {
        ctx.fillStyle = hsvToHex(Math.floor((x / width) * 256), 255, 255);
        ctx.fillRect(x, 0, 1, height);
      }
      // 当前色相的位置指示
      const markerX = (clamp(hue) / 256) * width;
      ctx.fillStyle = 'rgba(255,255,255,.9)';
      ctx.fillRect(markerX - 1, 0, 2, height);
      ctx.strokeStyle = 'rgba(0,0,0,.55)';
      ctx.lineWidth = 1;
      ctx.strokeRect(markerX - 1.5, 0.5, 3, height - 1);
    };

    draw();
    const observer = new ResizeObserver(draw);
    observer.observe(canvas);
    window.addEventListener('resize', draw);
    return () => {
      observer.disconnect();
      window.removeEventListener('resize', draw);
    };
  }, [hue, sat, val]);

  const pick = (clientX: number) => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const ratio = (clientX - rect.left) / Math.max(1, rect.width);
    onPick(clamp(Math.round(ratio * 255), 0, 255));
  };

  return (
    <canvas
      ref={canvasRef}
      className="hue-strip"
      style={disabled ? { opacity: 0.45, cursor: 'not-allowed' } : undefined}
      onPointerDown={(ev) => {
        if (disabled) return;
        dragging.current = true;
        ev.currentTarget.setPointerCapture(ev.pointerId);
        pick(ev.clientX);
      }}
      onPointerMove={(ev) => {
        if (disabled || !dragging.current) return;
        pick(ev.clientX);
      }}
      onPointerUp={(ev) => {
        dragging.current = false;
        try {
          ev.currentTarget.releasePointerCapture(ev.pointerId);
        } catch (err) {
          /* 忽略 */
        }
      }}
      onPointerCancel={() => {
        dragging.current = false;
      }}
    />
  );
}
