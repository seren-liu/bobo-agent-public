export function getLocalDayStamp(dateValue?: string | Date | null): string {
  const date = resolveDate(dateValue);
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

export function getCurrentLocalIsoString() {
  return formatLocalDateTime(new Date());
}

export function buildLocalDateTimeForDay(dayStamp: string, hour = 12, minute = 0, second = 0) {
  const parsed = new Date(`${dayStamp}T00:00:00`);
  if (Number.isNaN(parsed.getTime())) {
    return getCurrentLocalIsoString();
  }
  parsed.setHours(hour, minute, second, 0);
  return formatLocalDateTime(parsed);
}

export function resolveRecognitionConsumedAt(orderTime?: string | null) {
  if (!orderTime) {
    return getCurrentLocalIsoString();
  }

  const parsed = new Date(orderTime);
  if (Number.isNaN(parsed.getTime())) {
    return getCurrentLocalIsoString();
  }

  const today = getLocalDayStamp();
  return getLocalDayStamp(parsed) === today ? formatLocalDateTime(parsed) : getCurrentLocalIsoString();
}

function resolveDate(dateValue?: string | Date | null) {
  if (!dateValue) {
    return new Date();
  }
  if (dateValue instanceof Date) {
    return dateValue;
  }
  const parsed = new Date(dateValue);
  if (Number.isNaN(parsed.getTime())) {
    return new Date();
  }
  return parsed;
}

function formatLocalDateTime(date: Date) {
  const offsetMinutes = -date.getTimezoneOffset();
  const sign = offsetMinutes >= 0 ? '+' : '-';
  const absoluteOffsetMinutes = Math.abs(offsetMinutes);
  const offsetHours = Math.floor(absoluteOffsetMinutes / 60);
  const offsetRemainderMinutes = absoluteOffsetMinutes % 60;

  return `${getLocalDayStamp(date)}T${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}${sign}${pad(offsetHours)}:${pad(offsetRemainderMinutes)}`;
}

function pad(value: number) {
  return String(value).padStart(2, '0');
}
