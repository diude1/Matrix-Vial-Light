/* 灯光页：轴灯的全局灯效 / 速度 / 亮度 / 颜色 + 快捷预设 + 方案库。
 *
 * 对应原 ``_build_light_tab``。这一页只管**轴灯**（AMK 的 0x80–0x83 整组设色）；
 * 配件灯是另一套独立状态，在分区页编辑。
 */

import { useEffect, useMemo, useState } from 'react';
import { actGet, actPost, logLocal } from '../lib/api';
import type { EffectOption, Hsv } from '../lib/types';
import { Card } from '../components/Card';
import { ColorRow } from '../components/ColorRow';
import { HueStrip } from '../components/HueStrip';
import { Slider } from '../components/Slider';
import { useSession } from '../store/useSession';

/** 与 ``effects.QUICK_PRESETS`` 一致：六档快捷预设，按后端取编号。
 *  ``via`` 通道没有快捷档位（原版映射里就没有 via 这一项）。 */
type QuickMap = { amk: number | null; strip: number | null; vialrgb: number | null };

const QUICK_PRESETS: [string, QuickMap][] = [
  ['纯色', { amk: 1, strip: 2, vialrgb: 2 }],
  ['呼吸', { amk: null, strip: 6, vialrgb: 6 }],
  ['彩虹', { amk: null, strip: 4, vialrgb: 13 }],
  ['渐变', { amk: null, strip: 1, vialrgb: 4 }],
  ['静态', { amk: 1, strip: 2, vialrgb: 2 }],
  ['自定义（逐灯）', { amk: null, strip: 0, vialrgb: 1 }],
];

export function LightPage() {
  const { snapshot } = useSession();
  const [effects, setEffects] = useState<EffectOption[]>([]);

  const connected = !!snapshot?.connected;
  const light = snapshot?.light ?? null;
  const range = snapshot?.light_range;
  const backend = snapshot?.backend ?? null;

  // 灯效列表随连接变化（不同后端编号体系完全不同）
  useEffect(() => {
    if (!connected) {
      setEffects([]);
      return;
    }
    actGet<EffectOption[]>('/api/effects', true).then((list) => setEffects(list || []));
  }, [connected, backend, snapshot?.device_key?.keyboard_uid]);

  /** 速度上限：AMK 只有 0–3，VIA 通常 0–3，VialRGB 到 255。 */
  const speedMax = range ? range.speed[1] : 255;

  const effectIndex = useMemo(() => {
    if (!light) return -1;
    return effects.findIndex((item) => item.id === light.effect);
  }, [effects, light]);

  const setLight = (patch: Record<string, number>, apply: boolean) => {
    actPost('/api/light', { ...patch, apply });
  };

  /** 拖动中：走服务端的 40ms 节流；松手：立刻下发一次。 */
  const onDrag = (patch: Record<string, number>) => setLight(patch, false);
  const onCommit = (patch: Record<string, number>) => setLight(patch, true);

  const applyPreset = (mapping: QuickMap) => {
    if (!backend) {
      logLocal('warn', '尚未连接设备，预设无法下发');
      return;
    }
    if (backend !== 'amk' && backend !== 'strip' && backend !== 'vialrgb') {
      logLocal('warn', `「${backend}」通道没有对应的快捷档位，请手动选灯效`);
      return;
    }
    const effect = mapping[backend];
    if (effect === null) {
      logLocal('warn', `「${backend}」通道没有对应的该档灯效，换一档试试`);
      return;
    }
    actPost('/api/light', { effect, apply: true });
  };

  const stepEffect = (delta: number) => {
    if (!effects.length || !light) return;
    const idx = effectIndex < 0 ? 0 : effectIndex;
    const next = Math.max(0, Math.min(effects.length - 1, idx + delta));
    if (next === idx) return;
    actPost('/api/light', { effect: effects[next].id, apply: true });
  };

  const setBright = (val: number) => actPost('/api/light', { val, apply: true });

  const pickHsv = (hsv: Hsv) => onCommit({ hue: hsv[0], sat: hsv[1], val: hsv[2] });

  const hint = backendHint(backend);

  return (
    <div className="stack">
      {hint ? <div className="notice faint">{hint}</div> : null}

      <Card title="全局轴灯" hint={snapshot?.backend_label || ''}>
        <div className="btn-row">
          <span className="field-label">模式</span>
          <button className="btn" type="button" disabled={!connected} onClick={() => stepEffect(-1)}>
            上一档
          </button>
          <select
            className="effect-select"
            disabled={!connected || !effects.length}
            value={effectIndex >= 0 ? String(effectIndex) : ''}
            onChange={(ev) => {
              const idx = Number(ev.target.value);
              if (!Number.isNaN(idx) && effects[idx]) {
                actPost('/api/light', { effect: effects[idx].id, apply: true });
              }
            }}
          >
            {effects.length ? (
              effects.map((item, idx) => (
                <option key={item.id} value={idx}>
                  {item.label}
                </option>
              ))
            ) : (
              <option value="">（未连接设备）</option>
            )}
          </select>
          <button className="btn" type="button" disabled={!connected} onClick={() => stepEffect(1)}>
            下一档
          </button>
          <span className="pill">
            {effects.length && effectIndex >= 0 ? `${effectIndex + 1} / ${effects.length}` : '—'}
          </span>
        </div>
        <div className="zone-hint">
          {light ? `当前灯效：${light.label || '—'}` : '未连接设备时无法读取灯效'}
        </div>

        <div style={{ marginTop: 10 }}>
          <Slider
            label="亮度"
            min={0}
            max={255}
            value={light ? light.val : 0}
            disabled={!connected}
            onInput={(v) => onDrag({ val: v })}
            onCommit={(v) => onCommit({ val: v })}
          />
          <Slider
            label="速度"
            min={range ? range.speed[0] : 0}
            max={speedMax}
            value={light ? light.speed : 0}
            disabled={!connected}
            onInput={(v) => onDrag({ speed: v })}
            onCommit={(v) => onCommit({ speed: v })}
          />
        </div>
      </Card>

      <Card title="颜色">
        <div className="btn-row">
          <span className="field-label">色相</span>
          <span style={{ flex: 1, minWidth: 220 }}>
            <HueStrip
              hue={light ? light.hue : 0}
              sat={light ? light.sat : 255}
              val={light ? light.val : 255}
              disabled={!connected}
              onPick={(hue) => onDrag({ hue })}
            />
          </span>
          <span className="slider-row" style={{ margin: 0 }}>
            <span className="val">{light ? light.hue : 0}</span>
          </span>
        </div>

        <Slider
          label="饱和度"
          min={0}
          max={255}
          value={light ? light.sat : 0}
          disabled={!connected}
          onInput={(v) => onDrag({ sat: v })}
          onCommit={(v) => onCommit({ sat: v })}
        />

        <div className="btn-row" style={{ marginTop: 6 }}>
          <span className="field-label">取值</span>
          <ColorRow
            hsv={light ? [light.hue, light.sat, light.val] : [0, 0, 0]}
            disabled={!connected}
            onPick={() => undefined}
            onPickNow={pickHsv}
          />
        </div>
      </Card>

      <Card title="预设与操作">
        <div className="btn-row">
          <span className="field-label">预设</span>
          {QUICK_PRESETS.map(([name, mapping]) => (
            <button
              key={name}
              className="btn"
              type="button"
              disabled={!connected}
              onClick={() => applyPreset(mapping)}
            >
              {name}
            </button>
          ))}
        </div>

        <div className="btn-row" style={{ marginTop: 10 }}>
          <span className="field-label">动作</span>
          <button className="btn" type="button" disabled={!connected} onClick={() => setBright(0)}>
            关灯
          </button>
          <button className="btn" type="button" disabled={!connected} onClick={() => setBright(255)}>
            最亮
          </button>
          <button
            className="btn"
            type="button"
            disabled={!connected}
            onClick={() => actPost('/api/light/read')}
          >
            从键盘读取
          </button>

          <label className="check">
            <input
              type="checkbox"
              checked={!!snapshot?.live}
              disabled={!connected}
              onChange={(ev) => actPost('/api/live', { live: ev.target.checked })}
            />
            <span>实时下发</span>
          </label>

          <span className="spacer" />
          <span className={'status-tag ' + (snapshot?.dirty ? 'dirty' : 'synced')}>
            {snapshot?.dirty ? '● 有待应用修改' : '已与设备同步'}
          </span>
          <button
            className="btn"
            type="button"
            disabled={!connected}
            onClick={() => actPost('/api/light/save')}
          >
            保存到固件
          </button>
          <button
            className="btn primary"
            type="button"
            disabled={!connected}
            onClick={() => actPost('/api/light/apply')}
          >
            应用到设备
          </button>
        </div>

        <div className="zone-hint">
          实时下发打开时，拖动滑块会在 40ms 内合并成一次写入；关掉它则只改界面，
          点「应用到设备」才写到键盘。
        </div>
      </Card>
    </div>
  );
}

/** 各后端的通道说明 —— 与原 ``_hint_text()`` 一致。 */
function backendHint(backend: string | null): string {
  if (backend === 'amk') {
    return 'AMK 灯光通道：这一页只管轴灯（0x80–0x83 整组设色），灯效 1–45、速度 0–3。'
      + '配件灯是另一套独立状态，到「分区」页可以单独给它选灯效和颜色（本机固件只有'
      + '「自定义」档能吃下自选颜色）。';
  }
  if (backend === 'vialrgb') {
    return 'VialRGB 官方协议：支持逐键直接控制（见「逐键」页），若固件把轴灯与配件灯'
      + '拆成两组 LED，还能在「分区」页分开设置。';
  }
  if (backend === 'via') {
    return 'VIA 标准照明通道：速度通常只有 0–3 四档，不支持逐键与分区。';
  }
  return '';
}