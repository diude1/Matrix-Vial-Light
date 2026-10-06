import { useEffect, useState } from 'react';
import { clamp, hexToHsv, hsvToHex, hsvToRgb, rgbToHsv } from '../lib/color';
import { logLocal } from '../lib/api';
import type { Hsv } from '../lib/types';

interface CurrentColorProps {
  hsv: Hsv;
  disabled?: boolean;
  /** 输入框里的文字变成合法颜色时（失焦 / 回车） */
  onCommit: (hsv: Hsv) => void;
}

/**
 * 「当前颜色」读数 + 直输面板。
 *
 * **为什么需要**：色相条只能拖出色相，饱和度/取值是另外两条滑块 ——
 * 想精确复现一个颜色时无从下手。这里把 hex、HSV、RGB 三种写法同时显示，
 * 并且**都���以手工输入**，改哪一栏都实时联动另外两栏。
 *
 * 色值一律走 `#rrggbb`；也接受不带 `#`、三位简写（`f80`），与原生
 * `<input type="color">` 的行为一致。
 */
export function CurrentColor({ hsv, disabled, onCommit }: CurrentColorProps) {
  const rgb = hsvToRgb(hsv[0], hsv[1], hsv[2]);
  const hex = hsvToHex(hsv[0], hsv[1], hsv[2]);

  // 正在编辑的三个文本框各自独立存草稿，失焦才提交
  const [hexText, setHexText] = useState(hex);
  const [rgbText, setRgbText] = useState(`${rgb[0]}, ${rgb[1]}, ${rgb[2]}`);
  const [hsvText, setHsvText] = useState(`${hsv[0]}, ${hsv[1]}, ${hsv[2]}`);

  // 外部（SSE 快照 / 拖动滑块）变了才回填，正在输入时不打断
  useEffect(() => setHexText(hex), [hex]);
  useEffect(() => setRgbText(`${rgb[0]}, ${rgb[1]}, ${rgb[2]}`),
            [rgb[0], rgb[1], rgb[2]]);
  useEffect(() => setHsvText(`${hsv[0]}, ${hsv[1]}, ${hsv[2]}`),
            [hsv[0], hsv[1], hsv[2]]);

  const fail = (text: string, what: string) => {
    logLocal('err', `${what} 格式不对：${text}（示例 ${hex}）`);
  };

  const applyHex = (raw: string) => {
    try {
      onCommit(hexToHsv(raw));
    } catch (err) {
      fail(raw, 'hex');
      setHexText(hex);
    }
  };

  const applyRgb = (raw: string) => {
    const parts = raw.split(/[,，\s]+/).filter(Boolean);
    if (parts.length !== 3 || parts.some((p) => !/^\d{1,3}$/.test(p))) {
      fail(raw, 'RGB');
      setRgbText(`${rgb[0]}, ${rgb[1]}, ${rgb[2]}`);
      return;
    }
    onCommit(rgbToHsv(clamp(+parts[0]), clamp(+parts[1]), clamp(+parts[2])));
  };

  const applyHsv = (raw: string) => {
    const parts = raw.split(/[,，\s]+/).filter(Boolean);
    if (parts.length !== 3 || parts.some((p) => !/^\d{1,3}$/.test(p))) {
      fail(raw, 'HSV');
      setHsvText(`${hsv[0]}, ${hsv[1]}, ${hsv[2]}`);
      return;
    }
    onCommit([clamp(+parts[0]), clamp(+parts[1]), clamp(+parts[2])]);
  };

  const common = {
    disabled,
    spellCheck: false,
    autoComplete: 'off' as const,
    maxLength: 7,
  };

  return (
    <div className="curcolor">
      <span
        className="curcolor-chip"
        style={{ background: hex }}
        title={hex}
        aria-label={`当前颜色 ${hex}`}
      />
      <label className="curcolor-field" title="十六进制，可直接输入">
        <span>hex</span>
        <input
          {...common}
          className="hex-input"
          value={hexText}
          onChange={(ev) => setHexText(ev.target.value)}
          onBlur={() => applyHex(hexText)}
          onKeyDown={(ev) => {
            if (ev.key === 'Enter') applyHex(hexText);
          }}
        />
      </label>
      <label className="curcolor-field" title="色相,饱和度,取值（0-255）">
        <span>HSV</span>
        <input
          {...common}
          className="mono-input"
          value={hsvText}
          onChange={(ev) => setHsvText(ev.target.value)}
          onBlur={() => applyHsv(hsvText)}
          onKeyDown={(ev) => {
            if (ev.key === 'Enter') applyHsv(hsvText);
          }}
        />
      </label>
      <label className="curcolor-field" title="红,绿,蓝（0-255）">
        <span>RGB</span>
        <input
          {...common}
          className="mono-input"
          value={rgbText}
          onChange={(ev) => setRgbText(ev.target.value)}
          onBlur={() => applyRgb(rgbText)}
          onKeyDown={(ev) => {
            if (ev.key === 'Enter') applyRgb(rgbText);
          }}
        />
      </label>
    </div>
  );
}
