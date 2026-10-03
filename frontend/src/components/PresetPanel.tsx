import { useEffect, useState } from 'react';
import { actPost, fmtTime, hex4 } from '../lib/api';
import { Card } from './Card';
import { useSession } from '../store/useSession';

/**
 * 方案库面板（外壳级，五个页签都显示）。
 *
 * 一条「方案」= 一整套灯光设置：轴灯灯效 / 速度 / 颜色 + 两侧代表色 +
 * 逐键颜色 + 每条配件灯条的档位 / 颜色 / 速度 / **逐灯配色**。存在
 * ``%APPDATA%\vial-matrix-light\presets.json``。
 */
export function PresetPanel() {
  const { snapshot } = useSession();
  const items = snapshot?.presets.items ?? [];
  const path = snapshot?.presets.path ?? null;
  const error = snapshot?.presets.error ?? null;

  const [selected, setSelected] = useState<string | null>(null);
  const [name, setName] = useState('');

  // 方案被删掉 / 改名后，选中项要跟着失效
  useEffect(() => {
    if (selected && !items.some((item) => item.name === selected)) setSelected(null);
  }, [items, selected]);

  const save = async (overwrite: boolean) => {
    const trimmed = name.trim();
    if (!trimmed) return;
    const result = await actPost<{ name: string }>('/api/presets/save', {
      name: trimmed,
      overwrite,
    });
    if (result?.name) {
      setSelected(result.name);
      setName(result.name);
    }
  };

  const rename = async () => {
    if (!selected) return;
    const input = window.prompt('新的方案名：', selected);
    if (input === null) return;
    const next = input.trim();
    if (!next || next === selected) return;
    const actual = await actPost<string>('/api/presets/rename', {
      name: selected,
      new: next,
    });
    if (actual) setSelected(actual);
  };

  const run = async (action: 'apply' | 'delete' | 'duplicate') => {
    if (!selected) return;
    const body = { name: selected };
    if (action === 'apply') {
      actPost('/api/presets/apply', body);
    } else if (action === 'delete') {
      if (!window.confirm(`确定删除方案「${selected}」？`)) return;
      const ok = await actPost<boolean>('/api/presets/delete', body);
      if (ok !== undefined) setSelected(null);
    } else {
      const created = await actPost<string>('/api/presets/duplicate', body);
      if (created) setSelected(created);
    }
  };

  const move = (delta: number) => {
    if (!selected) return;
    actPost('/api/presets/move', { name: selected, delta });
  };

  const current = items.find((item) => item.name === selected) || null;
  const hasSelection = !!current;

  return (
    <Card title="预设方案" hint="整套灯光一键存取">
      <div className="raw-row">
        <input
          type="text"
          placeholder="方案名，例如「夜深人静」"
          maxLength={40}
          value={name}
          onChange={(ev) => setName(ev.target.value)}
        />
        <button className="btn" type="button" onClick={() => save(false)}>
          保存为新方案
        </button>
        <button className="btn" type="button" disabled={!name.trim()} onClick={() => save(true)}>
          覆盖当前
        </button>
      </div>

      <div style={{ marginTop: 10 }}>
        <div className="preset-list">
          {items.length ? (
            items.map((item) => (
              <div
                key={item.name}
                className={'preset-item' + (item.name === selected ? ' active' : '')}
                onClick={() => {
                  setSelected(item.name);
                  setName(item.name);
                }}
              >
                <div className="p-name">{item.name}</div>
                <div className="p-sum">{item.summary || ''}</div>
                <div className="p-time">更新于 {fmtTime(item.updated)}</div>
              </div>
            ))
          ) : (
            <div className="preset-empty">还没有方案。调好灯光后起个名字保存。</div>
          )}
        </div>
      </div>

      <div style={{ margin: '10px 0 4px' }}>
        {current ? (
          <div className="notice ok">
            {current.name} —— {current.summary || '（空方案）'}
            {current.device?.vendor_id
              ? `（后端 ${current.device.backend}，VID:PID ${hex4(
                  current.device.vendor_id,
                )}:${hex4(current.device.product_id)}）`
              : ''}
          </div>
        ) : (
          <div className="notice faint">（未选中方案）</div>
        )}
      </div>

      <div className="btn-row">
        <button
          className="btn primary"
          type="button"
          disabled={!hasSelection}
          onClick={() => run('apply')}
        >
          应用
        </button>
        <button className="btn" type="button" disabled={!hasSelection} onClick={rename}>
          重命名
        </button>
        <button
          className="btn"
          type="button"
          disabled={!hasSelection}
          onClick={() => run('delete')}
        >
          删除
        </button>
        <button
          className="btn"
          type="button"
          disabled={!hasSelection}
          onClick={() => run('duplicate')}
        >
          复制
        </button>
        <button
          className="btn tiny"
          type="button"
          disabled={!hasSelection}
          onClick={() => move(-1)}
        >
          ↑ 上移
        </button>
        <button
          className="btn tiny"
          type="button"
          disabled={!hasSelection}
          onClick={() => move(1)}
        >
          ↓ 下移
        </button>
      </div>

      {path ? (
        <div className="zone-hint">
          方案文件：{path}
          {error ? `（${error}）` : ''}
        </div>
      ) : null}
    </Card>
  );
}
