/* 分区页：轴灯卡片（灯效 / 颜色 / 亮度）+ 配件灯条栏 + 动作。
 *
 * 对应原 ``_build_zone_tab``，但按使用反馈做了三处收敛：
 *
 * * **「作用范围」已删除** —— 它只决定卡片能不能点，不改变任何下发行为，
 *   两侧本来就能分别操作，所以现在设备支持哪一侧哪一侧就可用。
 * * **「配件灯（批量）」卡片已删除** —— 批量上色改由灯条栏的「全部灯条同步」
 *   承担（把当前灯条的颜色 / 亮度 / 速度 / 灯效档一起套到全部灯条），
 *   靠它活着的「推送配件灯」「两侧互换」也一并移除。
 * * **灯条栏支持逐灯上色** —— 选中的灯条有几颗就画几颗格子，点一颗只改那一颗。
 *
 * 要点：AMK 后端下轴灯与配件灯是**两套独立状态**，改轴灯不会动到配件灯。
 * 配件灯条栏只在 AMK 后端出现。
 */

import { useEffect, useMemo, useState } from 'react';
import { actGet, actPost, logLocal } from '../lib/api';
import { hsvToHex } from '../lib/color';
import type { EffectOption, Hsv, StripInfo, StripLedInfo, ZoneId } from '../lib/types';
import { Card } from '../components/Card';
import { ColorRow } from '../components/ColorRow';
import { Segmented } from '../components/Segmented';
import { Slider } from '../components/Slider';
import { useSession } from '../store/useSession';

/** 与 ``effects.STRIP_EFFECTS`` 一致。 */
const STRIP_EFFECTS: [number, string, string][] = [
  [0, 'Custom', '自定义（逐灯上色）'],
  [1, 'Gradient', '渐变'],
  [2, 'Static', '静态'],
  [3, 'Blink', '闪烁'],
  [4, 'Rainbow', '彩虹'],
  [5, 'Random', '随机'],
  [6, 'Breath', '呼吸'],
  [7, 'Wipe / Scan', '擦除 / 扫描'],
  [8, 'Circle', '环绕'],
  [9, 'Effect 9', '灯效 9（固件专有）'],
];

const STRIP_SPEED_MAX = 15;

export function ZonePage() {
  const { snapshot } = useSession();
  const zone = snapshot?.zone;
  const strips = snapshot?.strips ?? [];
  const light = snapshot?.light ?? null;
  const connected = !!snapshot?.connected;
  const backend = snapshot?.backend ?? null;
  const isAmk = backend === 'amk';

  const [stripIndex, setStripIndex] = useState(0);
  const [effects, setEffects] = useState<EffectOption[]>([]);
  /** 灯条栏的画笔颜色 —— 点逐灯格子 / 「整条上色」时用的就是它。
   *  它**只**是本地的画笔值，不会因为选色就自动下发整条。 */
  const [paint, setPaint] = useState<Hsv>([0, 0, 0]);

  // 选中的灯条消失 / 首次进入 → 回到第一条
  useEffect(() => {
    if (!strips.length) return;
    if (!strips.some((item) => item.index === stripIndex)) setStripIndex(strips[0].index);
  }, [strips, stripIndex]);

  const strip: StripInfo | null =
    strips.find((item) => item.index === stripIndex) || strips[0] || null;

  // 换灯条时把画笔颜色重置成该灯条的颜色；之后不再被 SSE 快照覆盖，
  // 否则刚选好的颜色会被设备回读值冲掉。
  useEffect(() => {
    if (strip) setPaint([strip.hue, strip.sat, strip.val]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [strip?.index]);

  // 灯效列表随连接变化（不同后端编号体系完全不同）
  useEffect(() => {
    if (!connected) {
      setEffects([]);
      return;
    }
    actGet<EffectOption[]>('/api/effects', true).then((list) => setEffects(list || []));
  }, [connected, backend, snapshot?.device_key?.keyboard_uid]);

  const supported = zone?.supported ?? [];
  const capable = !!zone?.capable;

  /** 设备支持哪一侧，哪一侧就可编辑（不再有「作用范围」开关）。 */
  const zoneEnabled = (target: ZoneId) =>
    connected && capable && supported.includes(target);

  const setZoneColor = (target: ZoneId, hsv: Hsv, apply = true) =>
    actPost('/api/zone/color', {
      zone: target,
      hue: hsv[0],
      sat: hsv[1],
      val: hsv[2],
      apply,
    });

  const notice = () => {
    if (!connected) return { text: '尚未连接设备', cls: 'notice faint' };
    if (capable) {
      return { text: `本设备支持分区控制：${zone?.reason || ''}`, cls: 'notice ok' };
    }
    return {
      text: `本设备不支持分区控制 —— ${zone?.reason || '无独立寻址接口'}。`,
      cls: 'notice warn',
    };
  };
  const info = notice();

  const canEditStrip = !!strip?.editable;

  /** 当前灯条的效果编号 → 下拉里的序号 */
  const effectIndex = useMemo(() => {
    if (!light) return -1;
    return effects.findIndex((item) => item.id === light.effect);
  }, [effects, light]);

  const ledByOffset = useMemo(() => {
    const map = new Map<number, StripLedInfo>();
    (strip?.leds ?? []).forEach((led) => map.set(led.offset, led));
    return map;
  }, [strip]);

  /** 点一颗灯 → 用画笔颜色只给它上色（后端会自动切到自定义档）。 */
  const paintLed = (offset: number) => {
    if (!strip) return;
    actPost('/api/strip/led', {
      index: strip.index,
      offset,
      hue: paint[0],
      sat: paint[1],
      val: paint[2],
    });
  };

  return (
    <div className="stack">
      <Card title="分区控制" hint="全局轴灯与配件灯条分开编辑">
        <div className={info.cls}>{info.text}</div>

        <div className="zone-hint">{zone?.hint || ''}</div>
        {!capable && connected ? (
          <div className="zone-hint strong">分区页的控件在此设备上已置灰，不会生效。</div>
        ) : null}

        {/* ---------------- 配件灯条栏（仅 AMK） ---------------- */}
        {isAmk && strips.length ? (
          <div style={{ marginTop: 14, borderTop: '1px solid var(--border-soft)', paddingTop: 12 }}>
            <div className="btn-row">
              <span className="field-label">配件灯条</span>
              <Segmented<string>
                items={strips.map((item) => ({
                  value: String(item.index),
                  label: `灯条 ${item.index + 1}（${item.count} 颗）`,
                }))}
                value={String(strip ? strip.index : 0)}
                onChange={(value) => setStripIndex(Number(value))}
              />
            </div>

            <div className="btn-row" style={{ marginTop: 8 }}>
              <span className="field-label">画笔颜色</span>
              <ColorRow
                hsv={paint}
                disabled={!connected || !strip}
                onPick={() => undefined}
                onPickNow={setPaint}
              />
              <button
                className="btn"
                type="button"
                disabled={!connected || !strip}
                onClick={() =>
                  actPost('/api/strip/color', {
                    index: strip?.index ?? 0,
                    hue: paint[0],
                    sat: paint[1],
                    val: paint[2],
                    on: paint[2] > 0,
                  })
                }
              >
                整条上色
              </button>
            </div>
            <div className="zone-hint" style={{ marginTop: 4 }}>
              选好颜色后：点下面某一颗 = 只给那一颗上色；点「整条上色」= 整条都刷成这个颜色。
            </div>

            {/* 逐灯上色：有几颗画几颗 */}
            <div className="btn-row" style={{ marginTop: 8 }}>
              <span className="field-label">逐灯上色</span>
              <div className="led-cells">
                {Array.from({ length: strip ? strip.count : 0 }, (_, offset) => {
                  const led = ledByOffset.get(offset);
                  const off = !led || !led.on;
                  return (
                    <button
                      key={offset}
                      type="button"
                      className={off ? 'led-cell off' : 'led-cell'}
                      disabled={!connected || !strip}
                      title={
                        led
                          ? `第 ${offset + 1} 颗：`
                            + (off ? '熄灭' : hsvToHex(led.hue, led.sat, led.val))
                            + '　点一下用「画笔颜色」给它上色'
                          : `第 ${offset + 1} 颗：读不到`
                      }
                      style={off ? undefined : { background: hsvToHex(led.hue, led.sat, led.val) }}
                      onClick={() => paintLed(offset)}
                    />
                  );
                })}
              </div>
            </div>
            <div className="zone-hint" style={{ marginTop: 4 }}>
              {strip
                ? `共 ${strip.count} 颗，点一颗就用上面「画笔颜色」给它单独上色`
                  + (strip.read_count < strip.count
                    ? `（这次只读到 ${strip.read_count} 颗）` : '')
                  + '。当前不是自定义档时，点第一颗会自动切到「0 自定义」。'
                : ''}
            </div>

            <div className="btn-row" style={{ marginTop: 8 }}>
              <span className="field-label">灯效</span>
              <select
                value={strip ? String(strip.mode) : '0'}
                disabled={!connected || !strip}
                onChange={(ev) =>
                  actPost('/api/strip/mode', {
                    index: strip?.index ?? 0,
                    mode: Number(ev.target.value),
                  })
                }
              >
                {STRIP_EFFECTS.map(([id, en, zh]) => (
                  <option key={id} value={id}>
                    {id}　{zh}
                    {en ? `（${en}）` : ''}
                  </option>
                ))}
              </select>
              <button
                className="btn primary"
                type="button"
                disabled={!strip}
                onClick={() =>
                  actPost('/api/strip/mode', {
                    index: strip?.index ?? 0,
                    mode: strip ? strip.mode : 0,
                  })
                }
              >
                应用到该灯条
              </button>
              <button
                className="btn"
                type="button"
                disabled={!strip}
                onClick={() => actPost('/api/strip/sync', { index: strip?.index ?? 0 })}
              >
                全部灯条同步
              </button>
              <span className="spacer" />
              <span className="zone-hint" style={{ margin: 0 }}>
                {strip
                  ? `${strip.on_count}/${strip.count} 颗亮着`
                    + (strip.uniform
                      ? ` · 同色 H${strip.hue} S${strip.sat} V${strip.val}`
                      : ' · 多种颜色')
                  : ''}
              </span>
            </div>

            <Slider
              label="亮度"
              min={0}
              max={255}
              value={strip ? strip.val : 255}
              disabled={!canEditStrip}
              onCommit={(val) => actPost('/api/strip/bright', { index: strip?.index ?? 0, val })}
            />
            <Slider
              label="速度"
              min={0}
              max={STRIP_SPEED_MAX}
              value={strip ? strip.speed : 0}
              disabled={!canEditStrip}
              onCommit={(speed) => actPost('/api/strip/speed', { index: strip?.index ?? 0, speed })}
            />

            <div className={canEditStrip ? 'zone-hint' : 'zone-hint strong'}>
              {strip
                ? canEditStrip
                  ? '当前灯条是「自定义（逐灯上色）」档：颜色、亮度、速度、逐灯格子都作用于当前灯条，改完立刻生效。'
                    + '「全部灯条同步」会把这一条的设置套到所有灯条。'
                  : `当前是「${strip.mode} ${strip.mode_name}」档：这一档的画面由固件自己渲染，`
                    + '亮度 / 速度要切到「0 自定义（逐灯上色）」档才生效（否则会把固件渲染的画面冲掉）；'
                    + '而改颜色、点逐灯格子、整条上色都会**自动**帮你切到自定义档。'
                : ''}
            </div>
          </div>
        ) : null}
      </Card>

      {/* ---------------- 轴灯卡片 ---------------- */}
      {(['key'] as ZoneId[]).map((target) => {
        const hsv: Hsv = zone?.colors?.[target] ?? [0, 0, 0];
        const count = zone?.counts?.[target] ?? 0;
        const enabled = zoneEnabled(target);
        return (
          <Card key={target} title="轴灯（全局）" hint="与「灯光」页指向同一组轴灯">
            <div className="btn-row">
              <span className="field-label">灯效</span>
              <select
                className="effect-select"
                disabled={!enabled || !effects.length}
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
              <span className="zone-hint" style={{ margin: 0 }}>
                {light ? `当前：${light.label || '—'}` : ''}
                {count ? `　·　这一组 ${count} 颗` : ''}
              </span>
            </div>

            <div className="btn-row" style={{ marginTop: 8 }}>
              <span className="field-label">颜色</span>
              <ColorRow
                hsv={hsv}
                disabled={!enabled}
                onPick={(next) => setZoneColor(target, next, false)}
                onPickNow={(next) => setZoneColor(target, next, true)}
              />
            </div>

            <Slider
              label="亮度"
              min={0}
              max={255}
              value={hsv[2]}
              disabled={!enabled}
              onInput={(val) => setZoneColor(target, [hsv[0], hsv[1], val], false)}
              onCommit={(val) => setZoneColor(target, [hsv[0], hsv[1], val], true)}
            />

            <div className="zone-hint">
              当前代表色 {hsvToHex(hsv[0], hsv[1], hsv[2])}
              {enabled ? '' : '　（本设备不支持单独控制轴灯）'}
            </div>
          </Card>
        );
      })}

      <Card title="动作">
        <div className="btn-row">
          <span className="field-label">推送</span>
          <button
            className="btn"
            type="button"
            disabled={!zoneEnabled('key')}
            onClick={() => actPost('/api/zone/push', { zones: ['key'] })}
          >
            推送轴灯
          </button>
          <button
            className="btn primary"
            type="button"
            disabled={!zoneEnabled('key')}
            onClick={() => {
              actPost('/api/zone/push', { zones: ['key'] });
              if (isAmk && strip) {
                actPost('/api/strip/sync', { index: strip.index });
              }
            }}
          >
            全部推送
          </button>
          <span className="spacer" />
          <button
            className="btn"
            type="button"
            disabled={!connected || !capable}
            onClick={() => actPost('/api/zone/sync')}
          >
            从键盘读取
          </button>
          <button
            className="btn"
            type="button"
            disabled={!connected}
            onClick={() => {
              logLocal('info', '轴灯通道诊断：详情见日志与「信息」页的设备描述');
              actPost('/api/info/refresh');
            }}
          >
            轴灯通道诊断
          </button>
        </div>

        {isAmk && strips.length ? (
          <div className="zone-hint">
            「全部推送」＝ 推送轴灯颜色 + 把当前灯条（灯条 {strip ? strip.index + 1 : 1}）的设置
            同步到所有灯条。配件灯条在「0 自定义」之外的档位由固件渲染，
            此时批量改色会把该灯条切到自定义档（日志会提示）。
          </div>
        ) : null}
      </Card>
    </div>
  );
}
