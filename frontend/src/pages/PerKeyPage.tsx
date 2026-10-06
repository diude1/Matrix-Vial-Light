/* 逐键页：画笔 / 取色 / 填充 + 6 个图案 + 本地动画（Canvas）。
 *
 * 对应原 ``_build_perkey_tab``。要点：
 * - 颜色缓冲在**服务端**（``perkey.colors``），前端只是镜像 + 提交差量；
 * - 拖动是高频事件，用 requestAnimationFrame 合并成一次提交；
 * - AMK 的配件灯在逐键页**只能显示**（固件的矩阵通道不驱动灯条）。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { actPost, logLocal } from '../lib/api';
import { hsvHex, hsvToHex, textOn } from '../lib/color';
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

/** 灯位形状：键帽（按真实物理几何）/ 圆点（紧凑，一屏能看全）。 */
type Shape = 'key' | 'dot';

/**
 * 键帽四角 → 屏幕多边形（已按 KLE 旋转规则处理）。
 *
 * KLE 的旋转是**绕基准点 (rx, ry) 转整个区块**，`r` 是顺时针角度（度），
 * **不是绕键帽自己的中心** —— 算错会让旋角配列整片歪掉。
 * 返回值已乘 `unit` 并加上原点偏移，可直接喂给 `ctx.lineTo`。
 */
/** 有限数兜底：任何 NaN / undefined 都会让 Canvas 整块画不出来。
 *
 *  后端加了 `w`/`h`/`r` 字段，但**旧服务进程**（没重启）返回的 LED 里
 *  没有这些键 —— `undefined` 参与算术会得到 NaN，Canvas 收到 NaN 坐标
 *  会**静默丢弃整个路径**，表现就是"灯全没了、画布空白"。
 *  这里统一压成安全默认值，保证任何数据形态都能画出东西。
 */
function num(value: unknown, fallback: number): number {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
}

/** 键帽尺寸：宽键可能是 2.25U，退化时按 1×1 方块画。 */
function ledSize(led: PerKeyLed): [number, number] {
  return [Math.max(num(led.w, 1), 0.2), Math.max(num(led.h, 1), 0.2)];
}

/** 键帽四角 → 屏幕多边形（已按 KLE 旋转规则处理）。
 *
 * KLE 的旋转是**绕基准点 (rx, ry) 转整个区块**，`r` 是顺时针角度（度），
 * **不是绕键帽自己的中心** —— 算错会让旋角配列整片歪掉。
 * 返回值已乘 `unit` 并加上原点偏移，可直接喂给 `ctx.lineTo`。
 */
function keyPolygon(
  led: PerKeyLed,
  ox: number,
  oy: number,
  unit: number,
): [number, number][] {
  const [w, h] = ledSize(led);
  const x = num(led.x, 0);
  const y = num(led.y, 0);
  const corners: [number, number][] = [
    [x, y],
    [x + w, y],
    [x + w, y + h],
    [x, y + h],
  ];
  const angle = num(led.r, 0);
  const pts = angle
    ? corners.map(([kx, ky]) => {
        const a = (angle * Math.PI) / 180;
        const ca = Math.cos(a);
        const sa = Math.sin(a);
        const bx = num(led.rx, 0);
        const by = num(led.ry, 0);
        const dx = kx - bx;
        const dy = ky - by;
        return [bx + dx * ca - dy * sa, by + dx * sa + dy * ca] as [number, number];
      })
    : corners;
  return pts.map(([kx, ky]) => [ox + kx * unit, oy + ky * unit]);
}

/** 射线法：点是否在多边形内。 */
function pointInPolygon(px: number, py: number, pts: [number, number][]): boolean {
  let inside = false;
  for (let i = 0, j = pts.length - 1; i < pts.length; j = i, i += 1) {
    const [xi, yi] = pts[i];
    const [xj, yj] = pts[j];
    const cross = yi > py !== yj > py;
    if (cross && px < ((xj - xi) * (py - yi)) / (yj - yi || 1e-9) + xi) {
      inside = !inside;
    }
  }
  return inside;
}

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
  const [shape, setShape] = useState<Shape>('key');

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

  // 画布范围：**必须按旋转后的外接框**算，否则带 r 的键会被画到视口外。
  // 复用 keyPolygon(unit=1, 无偏移) 拿键盘单位下的角点，顺带把
  // undefined/NaN 兜底收在一处 —— 这里一旦算出 NaN，下面整段会被跳过，
  // 画布就全白。
  const geometry = useMemo(() => {
    if (!leds.length) return null;
    let minX = Infinity;
    let minY = Infinity;
    let maxX = -Infinity;
    let maxY = -Infinity;
    leds.forEach((l) => {
      keyPolygon(l, 0, 0, 1).forEach(([kx, ky]) => {
        if (kx < minX) minX = kx;
        if (kx > maxX) maxX = kx;
        if (ky < minY) minY = ky;
        if (ky > maxY) maxY = ky;
      });
    });
    if (!isFinite(minX) || !isFinite(minY)) return null;
    return {
      minX,
      minY,
      spanX: Math.max(maxX - minX, 1),
      spanY: Math.max(maxY - minY, 1),
    };
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
      // 注意：Canvas 的 fillStyle **不认 CSS 变量**（`var(--x)` 会被忽略，
      // 静默不画）。要提示文字必须给具体颜色值。
      ctx.fillStyle = '#7a8494';
      ctx.font = '13px "Segoe UI", sans-serif';
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      ctx.fillText(
        !leds.length ? '还没有 LED 灯位数据，点「从键盘读取」' : '灯位坐标异常，无法绘制',
        width / 2,
        height / 2,
      );
      return;
    }

    const unit = Math.min(
      (width - 90) / (geometry.spanX + 2),
      (height - 90) / (geometry.spanY + 2),
    );
    const ox = (width - geometry.spanX * unit) / 2 - geometry.minX * unit;
    const oy = (height - geometry.spanY * unit) / 2 - geometry.minY * unit;
    layoutRef.current = { unit, ox, oy };
    const gap = Math.max(unit * 0.07, 1);
    // 字太小就别塞了 —— 留白比糊成一团好
    const showLabel = unit > 17;

    leds.forEach((led) => {
      const color = colors[led.i] || [0, 0, 0];
      const fillc = color[2] ? hsvToHex(color[0], color[1], color[2]) : '#e8ecf1';
      // ⚠️ Canvas 的 strokeStyle **不解析 CSS 变量**，写 `var(--x)` 会被
      // 静默忽略（描边直接消失）。这里必须是具体色值。
      // 深色主题下用浅描边，浅色主题下用深描边 —— 统一走 CSS 变量读出来。
      const css = getComputedStyle(canvas);
      const outline = !led.writable
        ? css.getPropertyValue('--readonly').trim() || '#b9b9c0'
        : led.zone === 'key'
          ? css.getPropertyValue('--key-border').trim() || '#9aa0b5'
          : css.getPropertyValue('--accent-soft').trim() || '#d9ebff';

      if (shape === 'dot') {
        // 圆形：直接用中心 + 半径，忽略 w/h
        const [lw, lh] = ledSize(led);
        const cx = ox + (num(led.x, 0) + lw / 2) * unit;
        const cy = oy + (num(led.y, 0) + lh / 2) * unit;
        ctx.beginPath();
        ctx.arc(cx, cy, Math.max(unit * 0.43, 2.5), 0, Math.PI * 2);
        ctx.fillStyle = fillc;
        ctx.fill();
        ctx.lineWidth = 1;
        ctx.strokeStyle = outline;
        ctx.stroke();
        return;
      }

      // 键帽：按 KLE 物理几何画矩形，支持宽键与旋转
      const pts = keyPolygon(led, ox, oy, unit);
      // 旋转过的键帽是斜四边形，用「内缩到重心」的简化方式留缝：
      // 沿每个角到重心的方向缩 gap，看起来就是均匀的键帽间隙。
      const cx0 = pts.reduce((acc, p) => acc + p[0], 0) / 4;
      const cy0 = pts.reduce((acc, p) => acc + p[1], 0) / 4;
      const inner = pts.map(([px, py]) => {
        const vx = px - cx0;
        const vy = py - cy0;
        const len = Math.hypot(vx, vy) || 1;
        const shrink = Math.min(gap / len, 0.35);
        return [px - vx * shrink, py - vy * shrink] as [number, number];
      });

      ctx.beginPath();
      ctx.moveTo(inner[0][0], inner[0][1]);
      for (let k = 1; k < inner.length; k += 1) ctx.lineTo(inner[k][0], inner[k][1]);
      ctx.closePath();
      ctx.fillStyle = fillc;
      ctx.fill();
      ctx.lineWidth = 1;
      ctx.strokeStyle = outline;
      ctx.stroke();

      if (showLabel && led.label) {
        // 熄灭的键帽是浅灰底，必须用深字；点亮时按亮度自动黑白切换
        ctx.fillStyle = color[2] ? textOn([color[0], color[1], color[2]]) : '#7a8494';
        ctx.font = `${Math.max(7, unit * 0.2).toFixed(1)}px "Segoe UI", sans-serif`;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.fillText(led.label, cx0, cy0);
      }
    });
  }, [leds, geometry, colors, shape]);

  useEffect(() => {
    draw();
    const canvas = canvasRef.current;
    if (!canvas) return;
    const observer = new ResizeObserver(() => draw());
    observer.observe(canvas);
    // 主题切换只改 CSS 变量，组件不会重渲染 —— 必须主动侦测重绘，
    // 否则描边色会停在旧主题上（深色主题配浅色描边，反之亦然）。
    const mo = new MutationObserver(() => draw());
    mo.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['data-theme', 'class', 'style'],
    });
    return () => {
      observer.disconnect();
      mo.disconnect();
    };
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

    if (shape === 'key') {
      // 键帽模式：点在**旋转后的四边形内**就算命中（宽键、旋角都准）。
      // 逆序遍历，重叠时后画的（视觉在上层）优先。
      for (let i = leds.length - 1; i >= 0; i -= 1) {
        const led = leds[i];
        const pts = keyPolygon(led, ox, oy, unit);
        if (pointInPolygon(x, y, pts)) return led;
      }
      // 全部落空时退到「最近中心」，避免窄缝里点不中
      let near: PerKeyLed | null = null;
      let nearDist = (unit * 0.7) ** 2;
      leds.forEach((led) => {
        const pts = keyPolygon(led, ox, oy, unit);
        const cx = pts.reduce((a, p) => a + p[0], 0) / 4;
        const cy = pts.reduce((a, p) => a + p[1], 0) / 4;
        const d = (x - cx) ** 2 + (y - cy) ** 2;
        if (d < nearDist) {
          near = led;
          nearDist = d;
        }
      });
      return near;
    }

    // 圆点模式：按中心距离（保持原行为）
    let best: PerKeyLed | null = null;
    let bestDist = (unit * 1.1) ** 2;
    leds.forEach((led) => {
      const [lw, lh] = ledSize(led);
      const dx = x - (ox + (num(led.x, 0) + lw / 2) * unit);
      const dy = y - (oy + (num(led.y, 0) + lh / 2) * unit);
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
      const lx = num(led.x, 0);
      const ly = num(led.y, 0);
      let color: Hsv;
      if (name === '水平渐变') {
        const t = (lx - geometry.minX) / Math.max(geometry.spanX, 1);
        color = [Math.trunc(h + t * 120) % 256, s, v];
      } else if (name === '垂直渐变') {
        const t = (ly - geometry.minY) / Math.max(geometry.spanY, 1);
        color = [Math.trunc(h + t * 120) % 256, s, v];
      } else if (name === '彩虹') {
        const t = (lx - geometry.minX) / Math.max(geometry.spanX, 1);
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
          <span className="field-label">显示</span>
          <Segmented<Shape>
            items={[
              { value: 'key', label: '键帽' },
              { value: 'dot', label: '圆点' },
            ]}
            value={shape}
            onChange={setShape}
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
        {isAmk ? (
          <div className="notice warn" style={{ marginTop: 8 }}>
            若Esc / Tab / Shift / Ctrl 等功能区显示的颜色和别处不一致：
            这是固件在切换灯效档后残留的脏状态，<b>拔插一次 USB 数据线即可恢复</b>
            （灯光状态存在固件 RAM 里，只有断电能清掉，切灯效档修不好）。
          </div>
        ) : null}
      </Card>
    </div>
  );
}