/* 与后端 ``vial_light/webapp/session.py`` 的 state_dict() 一一对应的类型。
 *
 * 所有接口统一返回 ``{"ok": true, "data": ...}`` 或 ``{"ok": false, "error": "..."}``。
 * 这里的类型只描述 ``data`` 部分。
 */

// ------------------------------------------------------------------ 信封
export interface OkEnvelope<T> {
  ok: true;
  data: T;
}

export interface ErrorEnvelope {
  ok: false;
  error: string;
}

export type Envelope<T> = OkEnvelope<T> | ErrorEnvelope;

// ------------------------------------------------------------------ SSE
export type LogLevel = 'info' | 'ok' | 'warn' | 'err';

export interface LogEntry {
  ts: number;
  level: LogLevel;
  text: string;
}

export interface SseEvent {
  kind: 'log' | 'state' | 'busy';
  ts: number;
  data: LogEntry | Snapshot | { count: number };
}

// ------------------------------------------------------------------ 基础
export type ZoneId = 'key' | 'acc';
export type Backend = 'amk' | 'vialrgb' | 'via' | 'strip' | null;

/** HSV 三元组，三个分量都是 0-255（与 colors.py 一致）。 */
export type Hsv = [number, number, number];

// ------------------------------------------------------------------ 设备
export interface DeviceInfo {
  key: string;
  name: string;
  manufacturer: string | null;
  product: string | null;
  vendor_id: number;
  product_id: number;
  serial: string | null;
  usage_page: number;
  usage: number;
}

export interface DeviceKey {
  vendor_id: number;
  product_id: number;
  keyboard_uid: string | null;
  backend: Backend;
}

export interface AmkStripDescribe {
  index: number;
  start: number;
  count: number;
  mode: number;
  config: number | null;
  mode_editable: boolean;
}

/** ``VialDevice.describe()`` 的返回。字段很多，只标常用的。 */
export interface DeviceDescribe {
  name: string;
  manufacturer: string | null;
  product: string | null;
  vendor_id: number;
  product_id: number;
  serial: string | null;
  path: string | null;
  via_protocol: number | null;
  vial_protocol: number | null;
  keyboard_uid: string | null;
  vialrgb_flag: boolean | null;
  rgb_protocol: number | null;
  rgb_max_brightness: number | null;
  lighting_backend: Backend;
  definition_lighting: string | null;
  matrix: { rows?: number; cols?: number } | null;
  num_leds: number | null;
  supported_effects: number[] | null;
  definition_keys: number | null;
  detect_log: [string, string][] | null;
  zones: Record<string, number> | null;
  amk_protocol_version: number | null;
  amk_rgb_param_supported: boolean | null;
  amk_strips: AmkStripDescribe[] | null;
  amk_rgb_matrix: unknown;
  indicator: unknown;
  definition_error: string | null;
}

// ------------------------------------------------------------------ 灯光
export interface LightState {
  backend: Backend;
  effect: number;
  speed: number;
  hue: number;
  sat: number;
  val: number;
  brightness_max: number;
  /** 服务端算好的 #rrggbb */
  hex: string;
  /** ``effect_label()``，不含编号 */
  label: string;
  /** 还没下发到设备的改动（拖滑块时会出现） */
  pending?: Partial<Record<'effect' | 'speed' | 'hue' | 'sat' | 'val', number>>;
}

export interface LightRange {
  effect_ids: number[];
  speed: [number, number];
}

export interface EffectOption {
  id: number;
  label: string;
  desc: string;
}

// ------------------------------------------------------------------ 分区
export interface ZoneState {
  supported: ZoneId[];
  capable: boolean;
  reason: string;
  colors: Record<ZoneId, Hsv>;
  counts: Record<ZoneId, number>;
  hint: string;
}

// ------------------------------------------------------------------ 配件灯条
/** 灯条里的一颗灯（``offset`` 是在本灯条内的序号，0 起）。 */
export interface StripLedInfo {
  offset: number;
  hue: number;
  sat: number;
  val: number;
  on: boolean;
  speed: number;
}

export interface StripInfo {
  index: number;
  start: number;
  count: number;
  mode: number;
  mode_label: string;
  mode_name: string;
  editable: boolean;
  hue: number;
  sat: number;
  val: number;
  speed: number;
  on: boolean;
  on_count: number;
  read_count: number;
  uniform: boolean;
  hex: string;
  /** 逐灯明细，界面按 offset 画成一颗颗灯 */
  leds: StripLedInfo[];
}

// ------------------------------------------------------------------ 逐键
export interface PerKeyLed {
  /** 显示序号（0 起连续），颜色缓冲按它索引 */
  i: number;
  /** 固件真实全局灯号 */
  g: number;
  x: number;
  y: number;
  row: number | null;
  col: number | null;
  zone: ZoneId;
  /** AMK 配件灯只能显示，不能逐灯写 */
  writable: boolean;
}

export interface PerKeyState {
  supported: boolean;
  custom: boolean;
  leds: PerKeyLed[];
  colors: Hsv[];
  known: number[];
  count: number;
  backend?: Backend;
}

// ------------------------------------------------------------------ 配列
export interface LayoutKey {
  x: number;
  y: number;
  w: number;
  h: number;
  row: number | null;
  col: number | null;
  r: number;
  rx: number;
  ry: number;
}

export interface LayoutView {
  min_x: number;
  min_y: number;
  max_x: number;
  max_y: number;
  w: number;
  h: number;
  keys: LayoutKey[];
  mapped: [number, number][];
}

export interface LayoutInfo {
  name: string;
  matrix: { rows?: number; cols?: number };
  kle: LayoutView;
  grid: LayoutView;
  montage: boolean;
  key_count: number;
  warn: string;
}

// ------------------------------------------------------------------ 方案库
export interface PresetItem {
  name: string;
  summary: string;
  updated: number;
  device: Partial<DeviceKey>;
}

export interface PresetsState {
  path: string | null;
  error: string | null;
  names: string[];
  items: PresetItem[];
}

// ------------------------------------------------------------------ 快照
export interface Snapshot {
  connected: boolean;
  busy: number;
  transport: string | null;
  backend: Backend;
  backend_short: string;
  backend_label: string;
  device: DeviceDescribe | null;
  device_key: DeviceKey | null;
  live: boolean;
  dirty: boolean;
  light: LightState | null;
  light_range: LightRange;
  zone: ZoneState;
  strips: StripInfo[];
  perkey: PerKeyState;
  layout: LayoutInfo | null;
  presets: PresetsState;
}