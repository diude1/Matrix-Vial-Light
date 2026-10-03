/* 配列页：矩阵网格 / 物理配列双视图（SVG）。
 *
 * 对应原 ``_build_layout_tab``。Matrix 系键盘的 ``layouts.keymap`` 是机器生成的
 * 拼贴结构，按 KLE 累加会拉成长条 —— 所以**默认是矩阵网格**视图，它直接对应
 * 固件的行 / 列索引，做灯光排查时更准。
 */

import { useMemo, useState } from 'react';
import { hsvToHex } from '../lib/color';
import type { LayoutKey, LayoutView } from '../lib/types';
import { Card } from '../components/Card';
import { Segmented } from '../components/Segmented';
import { useSession } from '../store/useSession';

type ViewId = 'matrix' | 'kle';

/** 与旧 Canvas 版一致的绘制参数（单位：像素 / 键盘单位）。 */
const PAD_X = 90;
const PAD_Y = 70;
const EMPTY_FILL = '#ecf0f4';

function rotatedCorners(key: LayoutKey): [number, number][] {
  const pts: [number, number][] = [
    [key.x, key.y],
    [key.x + key.w, key.y],
    [key.x + key.w, key.y + key.h],
    [key.x, key.y + key.h],
  ];
  if (!key.r) return pts;
  const a = (key.r * Math.PI) / 180;
  const ca = Math.cos(a);
  const sa = Math.sin(a);
  return pts.map(([px, py]) => {
    const dx = px - key.rx;
    const dy = py - key.ry;
    return [key.rx + dx * ca - dy * sa, key.ry + dx * sa + dy * ca] as [number, number];
  });
}

/** 亮度感知的文字色（与原版 lum > 130 的判据一致）。 */
function textOnHex(hex: string): string {
  const t = hex.replace('#', '');
  const r = parseInt(t.slice(0, 2), 16);
  const g = parseInt(t.slice(2, 4), 16);
  const b = parseInt(t.slice(4, 6), 16);
  return (r + g + b) / 3 > 130 ? '#3a3a3a' : '#ffffff';
}

export function LayoutPage() {
  const { snapshot } = useSession();
  const layout = snapshot?.layout ?? null;
  const light = snapshot?.light ?? null;

  const [view, setView] = useState<ViewId>('matrix');
  const [showMatrix, setShowMatrix] = useState(true);

  const data: LayoutView | null = useMemo(() => {
    if (!layout) return null;
    return view === 'matrix' ? layout.grid : layout.kle;
  }, [layout, view]);

  const fill = light && light.val ? hsvToHex(light.hue, light.sat, light.val) : '#c9ced6';

  const geom = useMemo(() => {
    if (!data) return null;
    const uw = Math.max(data.w, 1e-6);
    const uh = Math.max(data.h, 1e-6);
    const unit = Math.min((1000 - PAD_X) / uw, (520 - PAD_Y) / uh);
    return {
      unit,
      ox: (1000 - uw * unit) / 2 - data.min_x * unit,
      oy: (520 - uh * unit) / 2 - data.min_y * unit,
      gap: Math.max(unit * 0.07, 1),
      radius: Math.max(unit * 0.09, 1),
    };
  }, [data]);

  const mappedSet = useMemo(() => {
    const set = new Set<string>();
    (data?.mapped || []).forEach(([r, c]) => set.add(`${r},${c}`));
    return set;
  }, [data]);

  const shown = useMemo(() => {
    if (!data) return 0;
    return data.keys.filter((k) => {
      const isMapped = k.row !== null && k.col !== null && mappedSet.has(`${k.row},${k.col}`);
      return view === 'matrix' ? isMapped : true;
    }).length;
  }, [data, mappedSet, view]);

  const rotatedCount = useMemo(
    () => (data ? data.keys.filter((k) => k.r).length : 0),
    [data],
  );

  const infoText = () => {
    if (!layout || !data) return '未加载';
    const rows = layout.matrix?.rows ?? '?';
    const cols = layout.matrix?.cols ?? '?';
    return (
      `${layout.name || '?'} · ${shown} 键 · ${rows}×${cols} 矩阵 · `
      + `${data.w.toFixed(2)}×${data.h.toFixed(2)}U`
      + (rotatedCount ? `　·　${rotatedCount} 键带旋转` : '')
    );
  };

  const warn = (() => {
    if (!layout) return '';
    if (view === 'kle' && layout.montage) return layout.warn;
    if (view === 'matrix') return '每个格子是一个矩阵单元（行,列），按当前灯光颜色着色。';
    return '';
  })();

  return (
    <div className="stack">
      <div className="btn-row">
        <span className="field-label">配列</span>
        <Segmented<ViewId>
          items={[
            { value: 'matrix', label: '矩阵网格' },
            { value: 'kle', label: '物理配列' },
          ]}
          value={view}
          onChange={setView}
        />
        <label className="check">
          <input
            type="checkbox"
            checked={showMatrix}
            onChange={(ev) => setShowMatrix(ev.target.checked)}
          />
          <span>显示坐标</span>
        </label>
        <span className="spacer" />
        <span className="zone-hint" style={{ margin: 0 }}>
          {infoText()}
        </span>
      </div>

      {warn ? <div className={view === 'kle' ? 'notice warn' : 'notice faint'}>{warn}</div> : null}

      <div className="canvas-box">
        {data && geom && data.keys.length ? (
          <svg
            className="layout-svg"
            viewBox="0 0 1000 520"
            preserveAspectRatio="xMidYMid meet"
            role="img"
            aria-label={view === 'matrix' ? '矩阵网格配列' : '物理配列'}
          >
            {data.keys.map((k, index) => {
              const isMapped = k.row !== null && k.col !== null && mappedSet.has(`${k.row},${k.col}`);
              if (view === 'matrix' && !isMapped) return null;
              const colour = isMapped ? fill : EMPTY_FILL;

              if (k.r) {
                const pts = rotatedCorners(k)
                  .map(([px, py]) => `${geom.ox + px * geom.unit},${geom.oy + py * geom.unit}`)
                  .join(' ');
                return (
                  <polygon
                    key={index}
                    points={pts}
                    fill={colour}
                    stroke="var(--key-border)"
                    strokeWidth={1}
                  />
                );
              }

              const x = geom.ox + k.x * geom.unit + geom.gap;
              const y = geom.oy + k.y * geom.unit + geom.gap;
              const w = k.w * geom.unit - geom.gap * 2;
              const h = k.h * geom.unit - geom.gap * 2;
              return (
                <rect
                  key={index}
                  x={x}
                  y={y}
                  width={w}
                  height={h}
                  rx={geom.radius}
                  fill={colour}
                  stroke="var(--key-border)"
                  strokeWidth={1}
                />
              );
            })}

            {showMatrix && geom.unit > 13
              ? data.keys.map((k, index) => {
                  if (k.row === null || k.col === null) return null;
                  const isMapped = mappedSet.has(`${k.row},${k.col}`);
                  if (view === 'matrix' && !isMapped) return null;
                  const colour = isMapped ? fill : EMPTY_FILL;
                  const corners = k.r ? rotatedCorners(k) : null;
                  const cx = corners
                    ? corners.reduce((sum, p) => sum + p[0], 0) / 4
                    : k.x + k.w / 2;
                  const cy = corners
                    ? corners.reduce((sum, p) => sum + p[1], 0) / 4
                    : k.y + k.h / 2;
                  return (
                    <text
                      key={`label-${index}`}
                      x={geom.ox + cx * geom.unit}
                      y={geom.oy + cy * geom.unit}
                      fill={textOnHex(colour)}
                      fontSize={Math.max(6, geom.unit * 0.19)}
                      fontFamily="Consolas, monospace"
                      textAnchor="middle"
                      dominantBaseline="central"
                    >
                      {k.row},{k.col}
                    </text>
                  );
                })
              : null}
          </svg>
        ) : (
          <div className="preset-empty" style={{ minHeight: 220, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            {snapshot?.connected ? '这把键盘的定义里没有可用的配列信息' : '未连接设备'}
          </div>
        )}
      </div>

      <Card>
        <div className="zone-hint">
          「矩阵网格」直接对应固件的行 / 列索引，是排查灯光最可靠的一视图；
          「物理配列」按 KLE 规范还原键位几何（含旋转），但拼贴式定义会左右分家。
        </div>
      </Card>
    </div>
  );
}