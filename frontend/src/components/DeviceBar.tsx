import { useCallback, useEffect, useState } from 'react';
import { actGet, actPost, logLocal } from '../lib/api';
import type { DeviceInfo } from '../lib/types';
import { useSession } from '../store/useSession';

/** 设备栏：枚举 / 选择 / 连接 / 断开 + 忙闲指示。 */
export function DeviceBar() {
  const { snapshot, devices, setDevices, busy } = useSession();
  const [index, setIndex] = useState(0);
  const [allRaw, setAllRaw] = useState(false);

  const connected = !!snapshot?.connected;

  const refreshDevices = useCallback(
    async (raw = allRaw) => {
      const list = await actGet<DeviceInfo[]>(`/api/devices?all=${raw ? '1' : '0'}`, true);
      if (!list) {
        logLocal('err', '枚举 HID 设备失败');
        return;
      }
      setDevices(list);
      setIndex((prev) => (prev < list.length ? prev : 0));
      logLocal(
        list.length ? 'ok' : 'warn',
        list.length
          ? `发现 ${list.length} 个 raw HID 接口`
          : '没有发现 Vial / VIA raw HID 设备',
      );
    },
    [allRaw, setDevices],
  );

  // 首屏枚举一次
  useEffect(() => {
    refreshDevices(false);
    // 只在挂载时跑一次；后续由按钮 / 勾选框触发
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const connect = () => {
    actPost('/api/connect', { index, all_raw: allRaw });
  };

  return (
    <section className="devicebar">
      <label className="field-label" htmlFor="device-select">
        设备
      </label>
      <select
        id="device-select"
        className="grow-select"
        disabled={connected}
        value={devices.length ? String(index) : ''}
        onChange={(ev) => setIndex(Number(ev.target.value) || 0)}
      >
        {devices.length ? (
          devices.map((dev, i) => (
            <option key={dev.key} value={i}>
              {dev.name}
            </option>
          ))
        ) : (
          <option value="">（没有找到 Vial / VIA raw HID 设备）</option>
        )}
      </select>
      <button
        className="btn"
        type="button"
        disabled={connected}
        onClick={() => refreshDevices()}
      >
        刷新列表
      </button>
      <label className="check">
        <input
          type="checkbox"
          checked={allRaw}
          disabled={connected}
          onChange={(ev) => {
            const next = ev.target.checked;
            setAllRaw(next);
            refreshDevices(next);
          }}
        />
        <span>显示所有 raw HID</span>
      </label>
      <button className="btn primary" type="button" disabled={connected} onClick={connect}>
        连接
      </button>
      <button
        className="btn"
        type="button"
        disabled={!connected}
        onClick={() => actPost('/api/disconnect')}
      >
        断开
      </button>
      <span className="spacer" />
      {busy > 0 ? (
        <span className="busy">
          <span className="spin" />
          工作中…
        </span>
      ) : null}
    </section>
  );
}
