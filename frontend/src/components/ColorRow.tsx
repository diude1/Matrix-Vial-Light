import { useEffect, useState } from 'react';
import { hexToHsv, hsvHex, PALETTE } from '../lib/color';
import { addCustom, isSaved, paletteFull, removeCustom, useCustomPalette } from '../lib/palette';
import { logLocal } from '../lib/api';
import type { Hsv } from '../lib/types';

interface ColorRowProps {
  hsv: Hsv;
  disabled?: boolean;
  /** 输入框里的文字改成了合法颜色 */
  onPick: (hsv: Hsv) => void;
  /** 点色板 / 拾色器 —— 通常需要立刻下发 */
  onPickNow: (hsv: Hsv) => void;
}

/**
 * 一行颜色控件：色块 + hex 输入 + 系统拾色器 + 快捷色。
 *
 * 快捷色分两组：前面 8 个是固定的默认色（不可删），后面是用户自己存的自定义色
 * （悬停出现 × 可删），末尾一个「+」把**当前颜色**存成自定义色。
 * 自定义色存在 localStorage，全局共享。
 */
export function ColorRow({ hsv, disabled, onPick, onPickNow }: ColorRowProps) {
  const [text, setText] = useState(hsvHex(hsv));
  const custom = useCustomPalette();

  // 外部（SSE 快照）变了才回填，正在输入时不打断
  useEffect(() => {
    setText(hsvHex(hsv));
  }, [hsv[0], hsv[1], hsv[2]]);

  const commit = (raw: string) => {
    try {
      const next = hexToHsv(raw);
      setText(hsvHex(next));
      onPickNow(next);
    } catch (err) {
      logLocal('err', String((err as Error).message || err));
      setText(hsvHex(hsv));
    }
  };

  const current = hsvHex(hsv);
  const full = paletteFull();
  /** 当前颜色已经在色板里（含 8 个默认色）→ 「+」置灰，避免点了没反应 */
  const saved = isSaved(hsv);

  const onAdd = () => {
    if (addCustom(hsv)) logLocal('ok', `已把 ${current} 存进自定义色`);
  };

  return (
    <div className="color-row">
      <input
        type="color"
        value={current}
        disabled={disabled}
        style={{
          width: 44,
          height: 30,
          padding: 0,
          border: 'none',
          background: 'transparent',
          cursor: disabled ? 'not-allowed' : 'pointer',
        }}
        onChange={(ev) => commit(ev.target.value)}
      />
      <input
        type="text"
        className="hex-input"
        maxLength={7}
        value={text}
        disabled={disabled}
        onChange={(ev) => setText(ev.target.value)}
        onBlur={() => commit(text)}
        onKeyDown={(ev) => {
          if (ev.key === 'Enter') commit(text);
        }}
      />
      <div className="dots">
        {PALETTE.map((item) => (
          <button
            key={item.name}
            type="button"
            className="dot"
            title={item.name}
            disabled={disabled}
            style={{ background: hsvHex(item.hsv) }}
            onClick={() => onPickNow(item.hsv)}
          />
        ))}

        {custom.map((item, idx) => {
          const hex = hsvHex(item);
          return (
            <span className="dot-wrap" key={hex}>
              <button
                type="button"
                className="dot"
                title={`自定义色 ${hex}`}
                disabled={disabled}
                style={{ background: hex }}
                onClick={() => onPickNow(item)}
              />
              <button
                type="button"
                className="dot-del"
                title="删除这个自定义色"
                disabled={disabled}
                onClick={(ev) => {
                  ev.stopPropagation();
                  removeCustom(idx);
                  logLocal('info', `已删除自定义色 ${hex}`);
                }}
              >
                ×
              </button>
            </span>
          );
        })}

        <button
          type="button"
          className="dot dot-add"
          title={
            full ? '自定义色已存满，先删几个'
              : saved ? `${current} 已经在色板里了`
                : `把当前颜色 ${current} 存为自定义色`
          }
          disabled={disabled || full || saved}
          onClick={onAdd}
        >
          +
        </button>
      </div>
    </div>
  );
}
