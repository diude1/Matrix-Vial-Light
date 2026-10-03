/* 信息页：设备信息 + 原始报文 + 日志。
 *
 * 对应原 ``_build_info_tab`` / ``update_info`` / ``send_raw``。设备信息走
 * ``snapshot.device``（连接后服务端自动描述）；「刷新」走 ``/api/info/refresh``。
 */

import { useMemo, useState } from 'react';
import { actPost, fmtClock, logLocal } from '../lib/api';
import type { DeviceDescribe } from '../lib/types';
import { Card } from '../components/Card';
import { useSession } from '../store/useSession';

const ORDER: [keyof DeviceDescribe, string][] = [
  ['name', '键盘名称'],
  ['manufacturer', '厂商'],
  ['product', '产品'],
  ['vendor_id', 'VID'],
  ['product_id', 'PID'],
  ['serial', '序列号'],
  ['via_protocol', 'VIA 协议版本'],
  ['vial_protocol', 'Vial 协议版本'],
  ['keyboard_uid', '键盘 UID'],
  ['vialrgb_flag', 'VialRGB 标志'],
  ['definition_lighting', '定义里的 lighting 字段'],
  ['lighting_backend', '启用后端'],
  ['rgb_protocol', 'VialRGB 协议'],
  ['rgb_max_brightness', '最大亮度'],
  ['num_leds', 'LED 数量'],
  ['matrix', '矩阵'],
  ['definition_keys', '定义键数'],
  ['amk_rgb_matrix', 'amk_rgb_matrix'],
  ['indicator', '指示灯定义'],
  ['zones', '分区 LED 数'],
  ['path', 'HID 路径'],
];

export function InfoPage() {
  const { snapshot, logs } = useSession();
  const device = snapshot?.device ?? null;
  const backend = snapshot?.backend ?? null;

  const [raw, setRaw] = useState('08 80');
  const [rawResult, setRawResult] = useState<string>('—');

  const infoText = useMemo(() => {
    if (!device) return '（未连接设备）';
    const lines: string[] = [];
    for (const [key, label] of ORDER) {
      let val = device[key];
      if ((key === 'vendor_id' || key === 'product_id') && val !== null && val !== undefined) {
        val = '0x' + Number(val).toString(16).toUpperCase().padStart(4, '0');
      }
      if (val === undefined || val === null) val = '';
      lines.push(`${label.padEnd(22, '　')} ${val}`);
    }
    if (device.definition_error) {
      lines.push('');
      lines.push('⚠ 键盘定义读取失败（不影响其他功能，重插一次通常可恢复）：');
      lines.push(`   ${device.definition_error}`);
    }
    if (device.supported_effects && device.supported_effects.length) {
      lines.push('');
      lines.push('固件支持的 VialRGB 灯效：');
      device.supported_effects.forEach((id) => {
        lines.push(`   ${String(id).padStart(3, ' ')}  ${id}`);
      });
    }
    lines.push('');
    lines.push('探测过程：');
    (device.detect_log || []).forEach(([name, note]) => {
      lines.push(`   ${name.padEnd(9, ' ')} ${note}`);
    });
    return lines.join('\n');
  }, [device]);

  const sendRaw = async () => {
    const trimmed = raw.trim();
    if (!trimmed) return;
    const result = await actPost<string>('/api/raw', { hex: trimmed });
    if (result !== undefined) setRawResult('← ' + result);
  };

  return (
    <div className="split">
      <div className="stack">
        <Card title="设备信息">
          <div className="btn-row" style={{ marginBottom: 10 }}>
            <span className="pill">{backend ? (snapshot?.backend_label || backend) : '未连接'}</span>
            <span className="spacer" />
            <button
              className="btn"
              type="button"
              disabled={!snapshot?.connected}
              onClick={() => actPost('/api/info/refresh')}
            >
              刷新
            </button>
          </div>
          <pre className="mono-text">{infoText}</pre>
        </Card>

        <Card title="原始报文">
          <div className="zone-hint">
            直接向键盘发送十六进制报文，格式如「08 80」（空格分隔）。用于排查固件行为。
          </div>
          <div className="raw-row">
            <input
              type="text"
              value={raw}
              onChange={(ev) => setRaw(ev.target.value)}
              onKeyDown={(ev) => {
                if (ev.key === 'Enter') sendRaw();
              }}
              disabled={!snapshot?.connected}
              placeholder="08 80"
            />
            <button className="btn primary" type="button" disabled={!snapshot?.connected} onClick={sendRaw}>
              发送
            </button>
          </div>
          <div className="zone-hint strong" style={{ marginTop: 8 }}>{rawResult}</div>
        </Card>
      </div>

      <Card title="日志" hint={`${logs.length} 条`}>
        <pre className="mono-text" style={{ minHeight: 420 }}>
          {logs.length
            ? logs.map((entry) => `[${fmtClock(entry.ts)}] ${entry.text}`).join('\n')
            : '（暂无日志）'}
        </pre>
        <div className="zone-hint">
          这里与底部日志面板同源；原始报文台可对任意 HID 接口试发报文。
          注意：本工具会对协议参数已知的设备直接读写灯光状态，不要发格式错误的报文。
        </div>
      </Card>
    </div>
  );
}