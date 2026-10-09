export function formatPercent(value: number | null | undefined): string {
  if (value == null || isNaN(value)) return '0.0%';
  return `${(value * 100).toFixed(1)}%`;
}

export function formatDelta(value: number | null | undefined): string {
  if (value == null || isNaN(value)) return '+0.00%';
  const sign = value >= 0 ? '+' : '';
  return `${sign}${(value * 100).toFixed(2)}%`;
}

export function formatNumber(value: number | null | undefined): string {
  if (value == null || isNaN(value)) return '0';
  return new Intl.NumberFormat('en-US').format(value);
}

export function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null || isNaN(seconds)) return '0.0s';
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const mins = Math.floor(seconds / 60);
  const secs = seconds % 60;
  return `${mins}m ${secs.toFixed(0)}s`;
}

export function formatMs(ms: number | null | undefined): string {
  if (ms == null || isNaN(ms)) return '0ms';
  if (ms < 1000) return `${ms.toFixed(0)}ms`;
  return `${(ms / 1000).toFixed(2)}s`;
}

export function classNames(...classes: (string | false | undefined | null)[]): string {
  return classes.filter(Boolean).join(' ');
}
