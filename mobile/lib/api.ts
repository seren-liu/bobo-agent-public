import axios from 'axios';
import { useAuthStore } from '@/stores/authStore';
import { getApiBaseUrl } from '@/lib/runtimeConfig';

const BASE_URL = getApiBaseUrl();

const createClient = () =>
  axios.create({
    baseURL: BASE_URL,
    headers: { 'Content-Type': 'application/json' },
    timeout: 15000,
  });

export const api = createClient();
const authApi = createClient();

export interface AuthSessionResponse {
  access_token: string;
  refresh_token: string;
  token_type: 'bearer';
  user_id: string;
  nickname: string;
}

// Attach JWT before every request
api.interceptors.request.use((config) => {
  const accessToken = useAuthStore.getState().accessToken;
  if (accessToken) {
    config.headers = config.headers ?? {};
    (config.headers as any).Authorization = `Bearer ${accessToken}`;
  }
  return config;
});

api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const originalRequest: any = error.config;
    const status = error.response?.status;

    if (!originalRequest || status !== 401 || originalRequest._retry) {
      return Promise.reject(error);
    }

    const { refreshToken, logout, setSession, userId } = useAuthStore.getState();
    if (!refreshToken) {
      return Promise.reject(error);
    }

    originalRequest._retry = true;

    try {
      const { data } = await authApi.post<AuthSessionResponse>('/bobo/auth/refresh', {
        refresh_token: refreshToken,
      });

      setSession({
        accessToken: data.access_token,
        refreshToken: data.refresh_token,
        userId: data.user_id || userId || null,
      });

      originalRequest.headers = originalRequest.headers ?? {};
      originalRequest.headers.Authorization = `Bearer ${data.access_token}`;
      return api(originalRequest);
    } catch (refreshError: any) {
      if (refreshError?.response?.status === 401) {
        logout();
      }
      return Promise.reject(refreshError);
    }
  }
);

// ─── Typed helpers ───────────────────────────────────────────────────────────

export interface DrinkRecord {
  id: string;
  brand: string;
  name: string;
  size?: string;
  sugar?: string;
  ice?: string;
  mood?: string | null;
  price: number;
  photo_url?: string;
  photos?: Array<{
    url: string;
    sort_order: number;
    created_at?: string | null;
  }>;
  source: 'manual' | 'photo' | 'screenshot' | 'agent';
  notes?: string | null;
  consumed_at: string;
  created_at: string;
}

export interface DayDetail {
  date: string;
  records: DrinkRecord[];
  photos: string[];
  total: number;
}

export interface RecentRecordsResponse {
  records: DrinkRecord[];
}

export interface ConfirmItem {
  brand: string;
  name: string;
  size?: string;
  sugar?: string;
  ice?: string;
  mood?: string;
  price: number;
  photo_url?: string;
  photos?: Array<{
    url: string;
    sort_order: number;
  }>;
  source: 'manual' | 'photo' | 'screenshot' | 'agent';
  notes?: string;
  consumed_at?: string;
  menu_id?: string | null;
}

export interface StatsResponse {
  total_amount: number;
  total_count: number;
  brand_dist: Array<{ brand: string; count: number; pct: number }>;
  weekly_trend: Array<{ week: string; count: number }>;
  sugar_pref: Array<{ sugar: string | null; count: number }>;
  ice_pref: Array<{ ice: string | null; count: number }>;
  daily_density: Record<string, number>;
}

export interface VisionItem {
  brand: string | null;
  name: string | null;
  size: string | null;
  sugar: string | null;
  ice: string | null;
  price: number | null;
  confidence: number;
}

export interface VisionResult {
  items: VisionItem[];
  source_type: 'photo' | 'screenshot';
  order_time: string | null;
  error?: 'recognition_failed' | 'parse_error' | null;
  degraded?: boolean;
  fallback_mode?: 'manual_entry' | null;
  retryable?: boolean | null;
  message?: string | null;
}

export interface UploadUrlRequest {
  filename: string;
  contentType: string;
  fileSize: number;
  width: number;
  height: number;
  sourceType: 'photo' | 'screenshot' | 'manual';
}

export interface MenuSearchItem {
  id: string;
  brand: string;
  name: string;
  size?: string | null;
  price?: number | null;
  description?: string | null;
  score: number;
}

export interface MenuSearchResponse {
  results: MenuSearchItem[];
}

export interface AgentThread {
  id: string;
  thread_id?: string;
  thread_key?: string;
  title?: string | null;
  status?: string;
  message_count?: number;
  last_user_message_at?: string | null;
  last_agent_message_at?: string | null;
  last_summary_at?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  archived_at?: string | null;
}

export interface AgentMessage {
  id?: string;
  thread_id?: string;
  role: 'user' | 'assistant' | 'system' | 'tool' | string;
  content: string;
  content_type?: string;
  request_id?: string | null;
  tool_name?: string | null;
  tool_call_id?: string | null;
  source?: string;
  created_at?: string | null;
}

export interface AgentSummary {
  id?: string;
  thread_id?: string;
  summary_type?: string;
  summary_text: string;
  open_slots?: string[];
  covered_message_count?: number;
  token_estimate?: number | null;
  created_at?: string | null;
}

export interface MemoryProfile {
  user_id?: string;
  profile_version?: number;
  display_preferences?: Record<string, unknown>;
  drink_preferences?: Record<string, unknown>;
  interaction_preferences?: Record<string, unknown>;
  budget_preferences?: Record<string, unknown>;
  health_preferences?: Record<string, unknown>;
  memory_updated_at?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface MemoryItem {
  id: string;
  user_id?: string;
  memory_type: string;
  scope: string;
  content: string;
  normalized_fact?: Record<string, unknown> | null;
  source_kind: string;
  source_ref?: string | null;
  confidence?: number;
  salience?: number;
  status?: string;
  expires_at?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  last_used_at?: string | null;
}

export interface AgentThreadListResponse {
  items?: AgentThread[];
  threads?: AgentThread[];
  data?: AgentThread[];
  results?: AgentThread[];
}

export interface AgentThreadMessagesResponse {
  items?: AgentMessage[];
  messages?: AgentMessage[];
  data?: AgentMessage[];
  results?: AgentMessage[];
}

export interface AgentMemoryListResponse {
  items?: MemoryItem[];
  memories?: MemoryItem[];
  data?: MemoryItem[];
  results?: MemoryItem[];
}

export const boboApi = {
  health: () => api.get<{ ok: boolean }>('/bobo/health'),

  login: (username: string, password: string) =>
    authApi.post<AuthSessionResponse>('/bobo/auth/login', {
      username,
      password,
    }),

  register: (name: string, nickname: string, email: string, password: string) =>
    authApi.post<AuthSessionResponse>('/bobo/auth/register', {
      name,
      nickname,
      email,
      password,
    }),

  refresh: (refreshToken: string) =>
    authApi.post<AuthSessionResponse>('/bobo/auth/refresh', {
      refresh_token: refreshToken,
    }),

  getDayRecords: (date: string) =>
    api.get<DayDetail>(`/bobo/records/day?date=${date}`),

  deleteRecord: (recordId: string) =>
    api.delete(`/bobo/records/${encodeURIComponent(recordId)}`),

  getRecentRecords: (limit = 5) =>
    api.get<RecentRecordsResponse>('/bobo/records/recent', {
      params: { limit },
    }),

  confirmRecords: (items: ConfirmItem[]) =>
    api.post('/bobo/records/confirm', { items }),

  getCalendar: (year: number, month: number) =>
    api.get<Record<string, { brand: string; color: string }[]>>(
      `/bobo/records/calendar?year=${year}&month=${month}`
    ),

  getStats: (period: 'week' | 'month' | 'all' = 'month', date?: string) =>
    api.get<StatsResponse>(`/bobo/records/stats`, {
      params: { period, date },
    }),

  searchMenu: (q: string, brand?: string, top_k = 5) =>
    api.get<MenuSearchResponse>('/bobo/menu/search', {
      params: { q, brand, top_k },
    }),

  listAgentThreads: () => api.get<AgentThreadListResponse>('/bobo/agent/threads'),

  createAgentThread: (title?: string) =>
    api.post<AgentThread>('/bobo/agent/threads', title ? { title } : {}),

  getAgentThread: (threadId: string) =>
    api.get<AgentThread>(`/bobo/agent/threads/${encodeURIComponent(threadId)}`),

  getAgentThreadMessages: (threadId: string) =>
    api.get<AgentThreadMessagesResponse>(`/bobo/agent/threads/${encodeURIComponent(threadId)}/messages`),

  archiveAgentThread: (threadId: string) =>
    api.post(`/bobo/agent/threads/${encodeURIComponent(threadId)}/archive`),

  clearAgentThread: (threadId: string) =>
    api.post(`/bobo/agent/threads/${encodeURIComponent(threadId)}/clear`),

  getAgentProfile: () => api.get<MemoryProfile>('/bobo/agent/profile'),

  patchAgentProfile: (patch: Partial<MemoryProfile>) =>
    api.patch<MemoryProfile>('/bobo/agent/profile', patch),

  resetAgentProfile: () => api.post<MemoryProfile>('/bobo/agent/profile/reset'),

  listAgentMemories: () => api.get<AgentMemoryListResponse>('/bobo/agent/memories'),

  deleteAgentMemory: (memoryId: string) =>
    api.delete(`/bobo/agent/memories/${encodeURIComponent(memoryId)}`),

  disableAgentMemory: (memoryId: string) =>
    api.post(`/bobo/agent/memories/${encodeURIComponent(memoryId)}/disable`),

  getUploadUrl: ({ filename, contentType, fileSize, width, height, sourceType }: UploadUrlRequest) =>
    api.post<{ upload_url: string; file_url: string }>('/bobo/upload-url', {
      filename,
      content_type: contentType,
      file_size: fileSize,
      width,
      height,
      source_type: sourceType,
    }),

  recognize: (imageUrl: string, sourceType: 'photo' | 'screenshot') =>
    api.post<VisionResult>('/bobo/vision/recognize', {
      image_url: imageUrl,
      source_type: sourceType,
    }),
};
