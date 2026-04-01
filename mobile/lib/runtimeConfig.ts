import Constants from 'expo-constants';

const DEFAULT_API_BASE_URL = 'http://localhost:8000';

function normalizeBaseUrl(value?: string | null): string | null {
  const trimmed = value?.trim();
  if (!trimmed) {
    return null;
  }
  return trimmed.replace(/\/+$/, '');
}

function extractHost(candidate?: string | null): string | null {
  const normalized = candidate?.trim().replace(/^https?:\/\//, '').split('/')[0];
  if (!normalized) {
    return null;
  }

  const host = normalized.split(':')[0]?.trim();
  if (!host || host === 'localhost' || host === '127.0.0.1') {
    return null;
  }

  return host;
}

export function getApiBaseUrl(): string {
  const explicit = normalizeBaseUrl(process.env.EXPO_PUBLIC_API_URL);
  if (explicit) {
    return explicit;
  }

  const hostCandidates = [
    Constants.expoConfig?.hostUri,
    (Constants as any).expoGoConfig?.debuggerHost,
    (Constants as any).manifest2?.extra?.expoClient?.hostUri,
    (Constants as any).manifest?.debuggerHost,
  ];

  for (const candidate of hostCandidates) {
    const host = extractHost(candidate);
    if (host) {
      return `http://${host}:8000`;
    }
  }

  return DEFAULT_API_BASE_URL;
}
