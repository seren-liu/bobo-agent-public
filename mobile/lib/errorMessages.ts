import axios from 'axios';

export function getApiErrorMessage(error: unknown, fallback = '请稍后重试') {
  if (axios.isAxiosError(error)) {
    const detail = error.response?.data?.detail;
    if (typeof detail === 'string' && detail.trim()) {
      return detail.trim();
    }

    const message = error.response?.data?.message;
    if (typeof message === 'string' && message.trim()) {
      return message.trim();
    }

    if (typeof error.message === 'string' && error.message.trim()) {
      return error.message.trim();
    }
  }

  if (error instanceof Error && error.message.trim()) {
    return error.message.trim();
  }

  return fallback;
}

export function getFriendlyRecordSaveError(error: unknown, fallback = '保存失败，请稍后重试') {
  const raw = getApiErrorMessage(error, fallback);

  const dailyLimitMatch = raw.match(/(\d{4}-\d{2}-\d{2}).*最多只能保存\s*(\d+)\s*条饮品记录/);
  if (dailyLimitMatch) {
    const [, dayStamp, limit] = dailyLimitMatch;
    return `${formatDayStamp(dayStamp)}最多可记录 ${limit} 杯饮品。你可以换个日期，或者先删除当天旧记录再保存。`;
  }

  if (raw.includes('最多只能保存') && raw.includes('饮品记录')) {
    return '这一天的饮品记录已经到上限了。你可以换个日期，或者先删除当天旧记录再保存。';
  }

  return raw;
}

function formatDayStamp(dayStamp: string) {
  const parsed = new Date(`${dayStamp}T12:00:00`);
  if (Number.isNaN(parsed.getTime())) {
    return '这一天';
  }

  return new Intl.DateTimeFormat('zh-CN', {
    month: 'long',
    day: 'numeric',
  }).format(parsed);
}
