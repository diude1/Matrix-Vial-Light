/* 逐键页：画笔 / 取色 / 填充 + 6 个图案 + 本地动画（Canvas）。
 *
 * 对应原 ``_build_perkey_tab``。要点：
 * - 颜色缓冲在**服务端**（``perkey.colors``），前端只是镜像 + 提交差量；
 * - 拖动是高频事件，用 requestAnimationFrame 合并成一次提交；
 * - AMK 的配件灯在逐键页**只能显示**（固件的矩阵通道不驱动灯条）。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { actPost, logLocal } from '../lib/api';
import { hsvHex, hsvToHex } from '../lib/color';
import type { Hsv, PerKeyLed } from '../lib/types';
import { Card } from '../components/Card';
import { ColorRow } from '../components/ColorRow';
import { Segmented } from '../components/Segmented';
import { useSession } from '../store/useSession';

type Tool = '画笔' | '取色' | '填充';

const PATTERNS = ['水平渐变', '垂直渐变', '彩虹', '波浪', '全部同色', '清空'] as const;
type Pattern = (typeof PATTERNS)[number];

/** 拖动时的提交间隔（毫秒）—— 原版用 after(120) 做防抖。 */
const PUSH_DELAY = 120;
/** 本地动画帧间隔（约 30fps）。 */
const ANIM_INTERVAL = 33;

export function PerKeyPage() {
  const { snapshot } = useSession();
  const pk = snapshot?.perkey;
  const leds = useMemo(() => pk?.leds ?? [], [pk]);
  const backend = snapshot?.backend ?? null;
  const connected = !!snapshot?.connected;
  const isAmk = backend === 'amk';
  const isVialRgb = backend === 'vialrgb';

  const [tool, setTool] = useState<Tool>('画笔');
  const [colors, setColors] = useState<Hsv[]>([]);
  const [paint, setPaint] = useState<Hsv>([171, 255, 255]);
  const [anim, setAnim] = useState(false);

  const painting = useRef(false);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const draggingRef = useRef(false);
  const animRef = useRef<number | null>(null);
  const animT = useRef(0);
  const animBusy = useRef(false);

  // 服务端颜色 → 本地镜像；正在涂的时候不回填（否则手感是「涂不动」）
  useEffect(() => {
    if (painting.current) return;
    const src = pk?.colors ?? [];
    setColors(src.map((c) => [c[0], c[1], c[2]] as Hsv));
  }, [pk?.colors]);

  // 画笔颜色初值跟随灯光页
  const light = snapshot?.light;
  useEffect(() => {
    if (light) setPaint([light.hue, light.sat, light.val]);
  }, [light?.hue, light?.sat, light?.val]);

  const geometry = useMemo(() => {
    if (!leds.length) return null;
    const xs = leds.map((l) => l.x);
    const ys = leds.map((l) => l.y);
    const minX = Math.min(...xs);
    const maxX = Math.max(...xs);
    const minY = Math.min(...ys);
    const maxY = Math.max(...ys);
    const spanX = Math.max(maxX - minX, 1);
    const spanY = Math.max(maxY - minY, 1);
    return { minX, minY, spanX, spanY };
  }, [leds]);

  // ---------------------------------------------------------------- 绘制
  const layoutRef = useRef({ unit: 20, ox: 0, oy: 0 });

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const width = canvas.clientWidth || 900;
    const height = canvas.clientHeight || 420;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.max(1, Math.round(width * dpr));
    canvas.height = Math.max(1, Math.round(height * dpr));
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);

    if (!leds.length || !geometry) {
      ctx.fillStyle = 'var(--text-3)';
      return;
    }

    const unit = Math.min(
      (width - 90) / (geometry.spanX + 2),
      (height - 90) / (geometry.spanY + 2),
    );
    const ox = (width - geometry.spanX * unit) / 2 - geometry.minX * unit;
    const oy = (height - geometry.spanY * unit) / 2 - geometry.minY * unit;
    layoutRef.current = { unit, ox, oy };
    const size = Math.max(unit * 0.86, 5);

    leds.forEach((led) => {
      const color = colors[led.i] || [0, 0, 0];
      const cx = ox + led.x * unit;
      const cy = oy + led.y * unit;
      const fillc = color[2] ? hsvToHex(color[0], color[1], color[2]) : '#e8ecf1';
      const outline = !led.writable
        ? 'var(--readonly)'
        : led.zone === 'key'
          ? 'var(--key-border)'
          : 'var(--accent-soft)';
      ctx.beginPath();
      ctx.arc(cx, cy, size / 2, 0, Math.PI * 2);
      ctx.fillStyle = fillc;
      ctx.fill();
      ctx.lineWidth = 1;
      ctx.strokeStyle = outline;
      ctx.stroke();
    });
  }, [leds, geometry, colors]);

  useEffect(() => {
    draw();
    const canvas = canvasRef.current;
    if (!canvas) return;
    const observer = new ResizeObserver(() => draw());
    observer.observe(canvas);
    return () => observer.disconnect();
  }, [draw]);

  // ---------------------------------------------------------------- 提交
  const pending = useRef<Set<number>>(new Set());
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const push = useCallback((changed: number[] | null) => {
    const body = changed === null ? {} : { changed };
    actPost('/api/perkey/push', body, true).then((result) => {
      if (result === undefined) logLocal('err', '逐键推送失败，请看日志');
    });
  }, []);

  const schedulePush = (displayIndex: number) => {
    pending.current.add(displayIndex);
    if (timer.current) return;
    timer.current = setTimeout(() => {
      timer.current = null;
      const list = Array.from(pending.current);
      pending.current.clear();
      if (list.length) push(list);
    }, PUSH_DELAY);
  };

  const syncColors = (next: Hsv[], changed: number[]) => {
    painting.current = true;
    setColors(next);
    actPost('/api/perkey/colors', {
      colors: next.map((hsv, i) => ({ i, h: hsv[0], s: hsv[1], v: hsv[2] })),
    }, true);
    return changed;
  };

  // ---------------------------------------------------------------- 交互
  const ledAt = (clientX: number, clientY: number): PerKeyLed | null => {
    const canvas = canvasRef.current;
    if (!canvas || !leds.length) return null;
    const rect = canvas.getBoundingClientRect();
    const x = clientX - rect.left;
    const y = clientY - rect.top;
    const { unit, ox, oy } = layoutRef.current;
    let best: PerKeyLed | null = null;
    let bestDist = (unit * 1.1) ** 2;
    leds.forEach((led) => {
      const dx = x - (ox + led.x * unit);
      const dy = y - (oy + led.y * unit);
      const dist = dx * dx + dy * dy;
      if (dist < bestDist) {
        best = led;
        bestDist = dist;
      }
    });
    return best;
  };

  const applyLed = (led: PerKeyLed, immediate: boolean) => {
    if (!led.writable) {
      if (immediate) {
        logLocal('warn', 'AMK 配件灯在逐键页仅用于显示，请到分区页编辑灯条');
      }
      return;
    }
    const next = colors.slice();
    if (next.length !== leds.length) {
      while (next.length < leds.length) next.push([0, 0, 0]);
    }
    next[led.i] = paint;
    setColors(next);
    actPost('/api/perkey/colors', { colors: [{ i: led.i, h: paint[0], s: paint[1], v: paint[2] }] }, true);
    if (immediate) push([led.i]);
    else schedulePush(led.i);
  };

  const onClick = (ev: React.PointerEvent<HTMLCanvasElement>) => {
    const led = ledAt(ev.clientX, ev.clientY);
    if (!led) return;

    if (tool === '取色') {
      const color = colors[led.i];
      if (color && color[2]) setPaint([color[0], color[1], color[2]]);
      return;
    }
    if (tool === '填充') {
      const next = leds.map((item) => (item.writable ? paint : (colors[item.i] || [0, 0, 0])));
      const changed = leds.filter((item) => item.writable).map((item) => item.i);
      syncColors(next as Hsv[], changed);
      push(changed);
      return;
    }
    applyLed(led, true);
  };

  const onPointerDown = (ev: React.PointerEvent<HTMLCanvasElement>) => {
    if (tool !== '画笔') {
      onClick(ev);
      return;
    }
    draggingRef.current = true;
    ev.currentTarget.setPointerCapture(ev.pointerId);
    const led = ledAt(ev.clientX, ev.clientY);
    if (led) applyLed(led, false);
  };

  const onPointerMove = (ev: React.PointerEvent<HTMLCanvasElement>) => {
    if (!draggingRef.current || tool !== '画笔') return;
    const led = ledAt(ev.clientX, ev.clientY);
    if (led) applyLed(led, false);
  };

  const onPointerUp = (ev: React.PointerEvent<HTMLCanvasElement>) => {
    draggingRef.current = false;
    painting.current = false;
    try {
      ev.currentTarget.releasePointerCapture(ev.pointerId);
    } catch (err) {
      /* 忽略 */
    }
  };

  // ---------------------------------------------------------------- 图案
  const renderPattern = (name: Pattern) => {
    if (!leds.length) {
      logLocal('warn', '逐键图案需要先读取 LED 灯位');
      return;
    }
    if (!geometry) return;
    const [h, s, v] = paint;
    const base = colors.length === leds.length ? colors.slice() : leds.map(() => [0, 0, 0] as Hsv);
    const changed: number[] = [];

    leds.forEach((led) => {
      if (!led.writable) return;
      let color: Hsv;
      if (name === '水平渐变') {
        const t = (led.x - geometry.minX) / Math.max(geometry.spanX, 1);
        color = [Math.trunc(h + t * 120) % 256, s, v];
      } else if (name === '垂直渐变') {
        const t = (led.y - geometry.minY) / Math.max(geometry.spanY, 1);
        color = [Math.trunc(h + t * 120) % 256, s, v];
      } else if (name === '彩虹') {
        const t = (led.x - geometry.minX) / Math.max(geometry.spanX, 1);
        color = [Math.trunc(t * 255) % 256, 255, v];
      } else if (name === '波浪') {
        const t = led.i / Math.max(leds.length - 1, 1);
        color = [
          Math.trunc(h + t * 200) % 256,
          s,
          Math.trunc(v * (0.35 + 0.65 * Math.abs(((t * 4) % 2) - 1))),
        ];
      } else if (name === '全部同色') {
        color = [h, s, v];
      } else {
        color = [0, 0, 0];
      }
      base[led.i] = color;
      changed.push(led.i);
    });

    syncColors(base as Hsv[], changed);
    push(changed);
  };

  // ---------------------------------------------------------------- 动画
  useEffect(() => {
    if (!anim) {
      if (animRef.current !== null) {
        cancelAnimationFrame(animRef.current);
        animRef.current = null;
      }
      return;
    }

    let stopped = false;
    let last = 0;

    const step = (now: number) => {
      if (stopped) return;
      animRef.current = requestAnimationFrame(step);
      if (now - last < ANIM_INTERVAL) return;
      last = now;
      if (animBusy.current) return; // 上一帧还没回来就跳过，避免请求堆积

      animT.current += 4;
      const span = Math.max(geometry?.spanX ?? 1, 1);
      const lo = geometry?.minX ?? 0;
      const out = leds.map((led) => {
        const t = (led.x - lo) / span;
        return [
          Math.trunc((t * 200 + animT.current) % 256),
          255,
          paint[2],
        ] as Hsv;
      });
      setColors(out);
      animBusy.current = true;
      actPost('/api/perkey/colors', {
        colors: out.map((hsv, i) => ({ i, h: hsv[0], s: hsv[1], v: hsv[2] })),
      }, true).then(() =>
        actPost('/api/perkey/push', {}, true).then(() => {
          animBusy.current = false;
        }),
      );
    };

    animRef.current = requestAnimationFrame(step);
    return () => {
      stopped = true;
      if (animRef.current !== null) cancelAnimationFrame(animRef.current);
      animRef.current = null;
      animBusy.current = false;
    };
  }, [anim, leds, geometry, paint]);

  const toggleAnim = (next: boolean) => {
    if (next && (!isVialRgb || !leds.length)) {
      logLocal('warn', '本地动画仅支持已读取 LED 的 VialRGB 设备');
      return;
    }
    setAnim(next);
    logLocal('ok', next ? '本地动画已开启（由电脑持续推送，约 30fps）' : '本地动画已关闭');
  };

  // ---------------------------------------------------------------- 文案
  const hint = (() => {
    if (!connected) return '未连接';
    const total = pk?.count ?? 0;
    const keyCount = leds.filter((l) => l.zone === 'key').length;
    const accCount = total - keyCount;
    if (isAmk) {
      return pk?.supported
        ? `${keyCount} 键可逐键上色 / 配件灯 ${accCount} 颗（配件灯请到分区页编辑）`
        : `轴灯 ${keyCount} / 配件灯 ${accCount}（矩阵通道不可用）`;
    }
    if (!isVialRgb) return '不支持逐键';
    return `${total} 颗灯（轴灯 ${keyCount} / 配件灯 ${accCount}）`;
  })();

  return (
    <div className="stack">
      <div className="btn-row">
        <span className="field-label">逐键</span>
        <span className="spacer" />
        <span className="zone-hint" style={{ margin: 0 }}>{hint}</span>
      </div>

      <Card>
        <div className="btn-row">
          <span className="field-label">工具</span>
          <Segmented<Tool>
            items={[
              { value: '画笔', label: '画笔' },
              { value: '取色', label: '取色' },
              { value: '填充', label: '填充' },
            ]}
            value={tool}
            onChange={setTool}
          />
          <span style={{ width: 1, height: 18, background: 'var(--border)' }} />
          <span className="field-label">图案</span>
          {PATTERNS.map((name) => (
            <button
              key={name}
              className="btn"
              type="button"
              disabled={!connected || !leds.length}
              onClick={() => renderPattern(name)}
            >
              {name}
            </button>
          ))}
        </div>

        <div className="btn-row" style={{ marginTop: 10 }}>
          <span className="field-label">效果</span>
          <label className="check">
            <input
              type="checkbox"
              checked={anim}
              disabled={!connected || !isVialRgb || !leds.length}
              onChange={(ev) => toggleAnim(ev.target.checked)}
            />
            <span>本地动画（仅 VialRGB，由电脑持续推送，约 30fps）</span>
          </label>
          <span className="spacer" />
          <button
            className="btn"
            type="button"
            disabled={!connected}
            onClick={() => actPost('/api/perkey/read')}
          >
            从键盘读取
          </button>
          <button
            className="btn"
            type="button"
            disabled={!connected}
            onClick={() => actPost('/api/perkey/custom')}
          >
            进入自定义档
          </button>
          <button
            className="btn primary"
            type="button"
            disabled={!connected || !leds.length}
            onClick={() => push(null)}
          >
            立即推送
          </button>
        </div>

        <div className="btn-row" style={{ marginTop: 10 }}>
          <span className="field-label">画笔颜色</span>
          <ColorRow
            hsv={paint}
            disabled={!connected}
            onPick={setPaint}
            onPickNow={setPaint}
          />
          <span className="spacer" />
          <button
            className="btn"
            type="button"
            disabled={!connected}
            onClick={() => actPost('/api/leds/refresh')}
          >
            刷新灯位
          </button>
        </div>
      </Card>

      <div className="canvas-box" style={{ position: 'relative' }}>
        {leds.length ? (
          <>
            <canvas
              ref={canvasRef}
              className="pk-canvas"
              style={{ height: 420, cursor: tool === '取色' ? 'copy' : 'crosshair' }}
              onPointerDown={onPointerDown}
              onPointerMove={onPointerMove}
              onPointerUp={onPointerUp}
              onPointerCancel={onPointerUp}
            />
            <div className="zone-hint" style={{ margin: '6px 4px 2px' }}>
              细边 = 可写轴灯　　亮蓝边 = 配件灯
              {isAmk ? '（AMK 配件灯仅显示，不支持逐灯写）' : ''}
            </div>
          </>
        ) : (
          <div className="preset-empty" style={{ minHeight: 220, display: 'flex', flexDirection: 'column', gap: 6, alignItems: 'center', justifyContent: 'center' }}>
            <div>尚未读取到灯位</div>
            <div>请先连接设备，或点「从键盘读取」</div>
          </div>
        )}
      </div>

      <Card>
        <div className="zone-hint">
          画笔涂单颗；取色把该颗的颜色取回画笔；填充把当前颜色刷给所有可写灯。
          拖动涂色会合并成一次推送（约 120ms）。
          {isAmk ? ' AMK 轴灯需要先「进入自定义档」，逐键上色才会生效。' : ''}
          {isVialRgb ? ' VialRGB 逐键推送会把全局灯效切到 Direct。' : ''}
        </div>
        <div className="zone-hint">
          当前画笔色 {hsvHex(paint)}
          {pk?.custom ? '　·　当前已在自定义档' : ''}
        </div>
      </Card>
    </div>
  );
}