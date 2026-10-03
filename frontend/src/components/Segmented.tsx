interface SegmentedProps<T extends string> {
  items: { value: T; label: string }[];
  value: T;
  onChange: (value: T) => void;
  disabled?: boolean;
}

/** 分段选择（作用范围、配列视图、逐键工具、灯条选择都用它）。 */
export function Segmented<T extends string>({
  items,
  value,
  onChange,
  disabled,
}: SegmentedProps<T>) {
  return (
    <div className="seg">
      {items.map((item) => (
        <button
          key={item.value}
          type="button"
          className={item.value === value ? 'active' : ''}
          disabled={disabled}
          onClick={() => onChange(item.value)}
        >
          {item.label}
        </button>
      ))}
    </div>
  );
}
