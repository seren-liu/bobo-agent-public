import { useEffect, useMemo, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Animated,
  Easing,
  Keyboard,
  Modal,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import { Ionicons } from '@expo/vector-icons';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { AppButton } from '@/components/AppButton';
import {
  boboApi,
  type AgentMessage,
  type AgentThread,
  type MemoryItem,
  type MemoryProfile,
  type MenuSearchItem,
} from '@/lib/api';
import { getApiBaseUrl } from '@/lib/runtimeConfig';
import { useAuthStore } from '@/stores/authStore';

type ChatMessage = {
  id?: string;
  role: 'user' | 'assistant';
  content: string;
  menuResults?: MenuSearchItem[];
};

type ChatThread = AgentThread & {
  isLocal?: boolean;
};

const DEV_LOGIN_ENABLED = process.env.EXPO_PUBLIC_ENABLE_DEV_LOGIN === 'true';
const FALLBACK_THREAD_TITLE = '临时对话';
const MEMORY_PREVIEW_LIMIT = 3;
const FLOATING_TAB_BAR_SPACE = 96;

function toArray<T>(response: unknown): T[] {
  if (!response) {
    return [];
  }
  if (Array.isArray(response)) {
    return response as T[];
  }
  if (typeof response === 'object') {
    const candidates = response as Record<string, unknown>;
    for (const key of ['items', 'threads', 'messages', 'memories', 'data', 'results']) {
      const value = candidates[key];
      if (Array.isArray(value)) {
        return value as T[];
      }
    }
  }
  return [];
}

function threadIdentity(thread: ChatThread): string {
  return thread.thread_key || thread.thread_id || thread.id;
}

function threadUpdatedAt(thread: ChatThread): string {
  return thread.updated_at || thread.last_agent_message_at || thread.last_user_message_at || thread.created_at || '';
}

function threadTitle(thread: ChatThread): string {
  const title = thread.title?.trim();
  if (title) {
    return title;
  }
  return formatDateTime(threadUpdatedAt(thread));
}

function normalizeChatMessage(message: AgentMessage): ChatMessage | null {
  if (!message.content) {
    return null;
  }
  if (message.role !== 'user' && message.role !== 'assistant') {
    return null;
  }
  return {
    role: message.role,
    content: message.content,
  };
}

function formatDateTime(value?: string | null): string {
  if (!value) {
    return '未更新';
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat('zh-CN', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date);
}

function summarizeProfile(profile: MemoryProfile | null): string[] {
  if (!profile) {
    return [];
  }

  const chips: string[] = [];
  const drink = profile.drink_preferences ?? {};
  const budget = profile.budget_preferences ?? {};

  const defaultSugar = drink.default_sugar as string | undefined;
  const defaultIce = drink.default_ice as string | undefined;
  const ceiling = budget.soft_price_ceiling as string | number | undefined;

  if (defaultSugar || defaultIce) {
    chips.push([defaultSugar, defaultIce].filter(Boolean).join(' / '));
  }
  if (Array.isArray(drink.preferred_brands) && drink.preferred_brands.length > 0) {
    chips.push(`偏好品牌 ${drink.preferred_brands.slice(0, 2).join('、')}`);
  }
  if (Array.isArray(drink.preferred_categories) && drink.preferred_categories.length > 0) {
    chips.push(`常点 ${drink.preferred_categories.slice(0, 2).join('、')}`);
  }
  if (typeof ceiling === 'number' || typeof ceiling === 'string') {
    chips.push(`预算 ¥${ceiling} 内`);
  }

  return chips;
}

function isPendingAssistantMessage(message: ChatMessage): boolean {
  return message.role === 'assistant' && !message.content.trim() && !(message.menuResults?.length);
}

function pickStringArray(value: unknown, limit = 3): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.map((item) => String(item).trim()).filter(Boolean).slice(0, limit);
}

function parseApiErrorMessage(raw: string, fallbackStatus?: number): string {
  const text = raw.trim();
  if (!text) {
    return fallbackStatus ? `HTTP ${fallbackStatus}` : 'unknown error';
  }
  try {
    const payload = JSON.parse(text) as { detail?: unknown; message?: unknown; error?: unknown };
    const detail = payload.detail ?? payload.message ?? payload.error;
    if (typeof detail === 'string' && detail.trim()) {
      return detail.trim();
    }
  } catch {
    // Fall through to raw text.
  }
  return text;
}

function summarizePortrait(profile: MemoryProfile | null): Array<{ label: string; value: string }> {
  if (!profile) {
    return [];
  }

  const drink = profile.drink_preferences ?? {};
  const interaction = profile.interaction_preferences ?? {};
  const budget = profile.budget_preferences ?? {};
  const health = profile.health_preferences ?? {};

  const portrait: Array<{ label: string; value: string }> = [];

  const sugar = typeof drink.default_sugar === 'string' ? drink.default_sugar : '';
  const ice = typeof drink.default_ice === 'string' ? drink.default_ice : '';
  if (sugar || ice) {
    portrait.push({ label: '默认口味', value: [sugar, ice].filter(Boolean).join(' / ') });
  }

  const brands = pickStringArray(drink.preferred_brands, 3);
  if (brands.length) {
    portrait.push({ label: '偏好品牌', value: brands.join('、') });
  }

  const categories = pickStringArray(drink.preferred_categories, 3);
  if (categories.length) {
    portrait.push({ label: '常喝类型', value: categories.join('、') });
  }

  const softCeiling = budget.soft_price_ceiling;
  if (typeof softCeiling === 'number' || typeof softCeiling === 'string') {
    portrait.push({ label: '预算区间', value: `通常控制在 ¥${softCeiling} 内` });
  }

  const disliked = pickStringArray(drink.disliked_flavors, 3);
  if (disliked.length) {
    portrait.push({ label: '避雷口味', value: disliked.join('、') });
  }

  const style = pickStringArray(interaction.response_style, 2);
  if (style.length) {
    portrait.push({ label: '回答风格', value: style.join('、') });
  }

  const healthGoals = pickStringArray(health.goals, 2);
  if (healthGoals.length) {
    portrait.push({ label: '关注点', value: healthGoals.join('、') });
  }

  return portrait;
}

function createLocalThread(title = FALLBACK_THREAD_TITLE): ChatThread {
  return {
    id: `local-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    title,
    status: 'local',
    message_count: 0,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    isLocal: true,
  };
}

export default function AiScreen() {
  const insets = useSafeAreaInsets();
  const accessToken = useAuthStore((s) => s.accessToken);
  const refreshToken = useAuthStore((s) => s.refreshToken);
  const logout = useAuthStore((s) => s.logout);
  const setSession = useAuthStore((s) => s.setSession);
  const userId = useAuthStore((s) => s.userId) ?? 'dev';

  const [loginPending, setLoginPending] = useState(false);
  const [loginError, setLoginError] = useState<string | null>(null);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [initializing, setInitializing] = useState(false);
  const [threadLoadError, setThreadLoadError] = useState<string | null>(null);
  const [memoryLoadError, setMemoryLoadError] = useState<string | null>(null);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [threads, setThreads] = useState<ChatThread[]>([]);
  const [activeThreadId, setActiveThreadId] = useState<string | null>(null);
  const [threadMessages, setThreadMessages] = useState<Record<string, ChatMessage[]>>({});
  const [memoryProfile, setMemoryProfile] = useState<MemoryProfile | null>(null);
  const [memoryItems, setMemoryItems] = useState<MemoryItem[]>([]);
  const [keyboardHeight, setKeyboardHeight] = useState(0);
  const loadedThreadIdsRef = useRef(new Set<string>());
  const pageScrollRef = useRef<ScrollView | null>(null);
  const shimmerProgress = useRef(new Animated.Value(0)).current;

  const baseUrl = useMemo(() => getApiBaseUrl(), []);

  const activeThread = useMemo(
    () => threads.find((thread) => threadIdentity(thread) === activeThreadId) ?? null,
    [activeThreadId, threads]
  );

  const activeMessages = useMemo(() => {
    if (!activeThreadId) {
      return [];
    }
    return threadMessages[activeThreadId] ?? [];
  }, [activeThreadId, threadMessages]);

  const profileHighlights = useMemo(() => summarizeProfile(memoryProfile), [memoryProfile]);
  const portraitRows = useMemo(() => summarizePortrait(memoryProfile), [memoryProfile]);
  const memoryHighlights = useMemo(
    () => memoryItems.map((memory) => memory.content).filter(Boolean).slice(0, MEMORY_PREVIEW_LIMIT),
    [memoryItems]
  );

  const composerBottomOffset = keyboardHeight > 0 ? Math.max(12, keyboardHeight - insets.bottom + 8) : FLOATING_TAB_BAR_SPACE;
  const shimmerTranslate = shimmerProgress.interpolate({
    inputRange: [0, 1],
    outputRange: [-220, 220],
  });

  const loadMemoryState = async () => {
    try {
      const [profileRes, memoriesRes] = await Promise.allSettled([
        boboApi.getAgentProfile(),
        boboApi.listAgentMemories(),
      ]);
      const failures: string[] = [];

      if (profileRes.status === 'fulfilled') {
        setMemoryProfile(profileRes.value.data);
      } else {
        failures.push('画像');
      }

      if (memoriesRes.status === 'fulfilled') {
        setMemoryItems(toArray<MemoryItem>(memoriesRes.value.data));
      } else {
        failures.push('记忆');
      }

      setMemoryLoadError(failures.length > 0 ? `部分记忆接口暂不可用：${failures.join('、')}` : null);
    } catch (error) {
      setMemoryLoadError(error instanceof Error ? error.message : 'memory load failed');
    }
  };

  const loadThreadMessages = async (thread: ChatThread) => {
    const threadId = threadIdentity(thread);
    if (!threadId || loadedThreadIdsRef.current.has(threadId)) {
      return;
    }

    try {
      const { data } = await boboApi.getAgentThreadMessages(threadId);
      const items = toArray<AgentMessage>(data)
        .map(normalizeChatMessage)
        .filter((item): item is ChatMessage => Boolean(item));

      setThreadMessages((prev) => ({ ...prev, [threadId]: items }));
      loadedThreadIdsRef.current.add(threadId);
    } catch {
      if (!threadMessages[threadId]) {
        setThreadMessages((prev) => ({ ...prev, [threadId]: [] }));
      }
    }
  };

  const bootstrapThreads = async () => {
    if (!accessToken) {
      return;
    }

    setInitializing(true);
    setThreadLoadError(null);

    try {
      const { data } = await boboApi.listAgentThreads();
      const remoteThreads = toArray<ChatThread>(data).sort((left, right) =>
        threadUpdatedAt(right).localeCompare(threadUpdatedAt(left))
      );

      if (remoteThreads.length > 0) {
        setThreads(remoteThreads);
        const nextThread = remoteThreads[0];
        const nextThreadId = threadIdentity(nextThread);
        setActiveThreadId(nextThreadId);
        setThreadMessages((prev) => ({ ...prev, [nextThreadId]: prev[nextThreadId] ?? [] }));
        await loadThreadMessages(nextThread);
      } else {
        setThreads([]);
        setActiveThreadId(null);
      }

    } catch (error) {
      setThreadLoadError(error instanceof Error ? error.message : 'thread load failed');
      setThreads([]);
      setActiveThreadId(null);
    } finally {
      setInitializing(false);
    }

    await loadMemoryState();
  };

  useEffect(() => {
    if (!accessToken) {
      return;
    }
    void bootstrapThreads();
    // Intentionally only re-run when auth changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [accessToken]);

  useEffect(() => {
    const showEvent = Platform.OS === 'ios' ? 'keyboardWillShow' : 'keyboardDidShow';
    const hideEvent = Platform.OS === 'ios' ? 'keyboardWillHide' : 'keyboardDidHide';

    const showSub = Keyboard.addListener(showEvent, (event) => {
      setKeyboardHeight(event.endCoordinates.height);
    });
    const hideSub = Keyboard.addListener(hideEvent, () => {
      setKeyboardHeight(0);
    });

    return () => {
      showSub.remove();
      hideSub.remove();
    };
  }, []);

  useEffect(() => {
    const timer = setTimeout(() => {
      pageScrollRef.current?.scrollToEnd({ animated: true });
    }, 80);
    return () => clearTimeout(timer);
  }, [keyboardHeight, activeMessages.length, sending]);

  useEffect(() => {
    if (!sending) {
      shimmerProgress.stopAnimation();
      shimmerProgress.setValue(0);
      return;
    }

    const loop = Animated.loop(
      Animated.timing(shimmerProgress, {
        toValue: 1,
        duration: 1450,
        easing: Easing.inOut(Easing.ease),
        useNativeDriver: true,
      })
    );

    loop.start();
    return () => {
      loop.stop();
      shimmerProgress.stopAnimation();
      shimmerProgress.setValue(0);
    };
  }, [sending, shimmerProgress]);

  const upsertStreamingAssistant = (threadId: string, messageId: string, updater: (current: ChatMessage) => ChatMessage) => {
    setThreadMessages((prev) => {
      const current = prev[threadId] ?? [];
      const index = current.findIndex((message) => message.id === messageId);
      if (index === -1) {
        return prev;
      }
      const next = [...current];
      next[index] = updater(next[index] ?? { id: messageId, role: 'assistant', content: '' });
      return { ...prev, [threadId]: next };
    });
  };

  const loginAsDev = async () => {
    try {
      setLoginPending(true);
      setLoginError(null);
      const { data } = await boboApi.login('dev', 'dev123456');
      setSession({
        accessToken: data.access_token,
        refreshToken: data.refresh_token,
        userId: data.user_id,
      });
    } catch {
      setLoginError('Dev login failed. Please ensure backend has user dev/dev123456.');
    } finally {
      setLoginPending(false);
    }
  };

  const refreshActiveThread = async (threadId: string) => {
    const thread = threads.find((item) => threadIdentity(item) === threadId);
    if (!thread) {
      return;
    }
    try {
      const { data } = await boboApi.getAgentThreadMessages(threadId);
      const items = toArray<AgentMessage>(data)
        .map(normalizeChatMessage)
        .filter((item): item is ChatMessage => Boolean(item));
      setThreadMessages((prev) => ({ ...prev, [threadId]: items }));
      loadedThreadIdsRef.current.add(threadId);
    } catch {
      // Leave local cache untouched when backend thread APIs are not ready.
    }
  };

  const ensureThread = async (): Promise<ChatThread | null> => {
    if (activeThread) {
      return activeThread;
    }

    try {
      const { data } = await boboApi.createAgentThread('新会话');
      const thread = { ...data, isLocal: false } as ChatThread;
      const threadId = threadIdentity(thread);
      setThreads((prev) => [thread, ...prev]);
      setActiveThreadId(threadId);
      setThreadMessages((prev) => ({ ...prev, [threadId]: prev[threadId] ?? [] }));
      return thread;
    } catch {
      const localThread = createLocalThread('新会话');
      const threadId = threadIdentity(localThread);
      setThreads((prev) => [localThread, ...prev]);
      setActiveThreadId(threadId);
      setThreadMessages((prev) => ({ ...prev, [threadId]: prev[threadId] ?? [] }));
      return localThread;
    }
  };

  const handleSelectThread = async (thread: ChatThread) => {
    const threadId = threadIdentity(thread);
    setActiveThreadId(threadId);
    setThreadMessages((prev) => ({ ...prev, [threadId]: prev[threadId] ?? [] }));
    await loadThreadMessages(thread);
  };

  const handleNewThread = async () => {
    if (!accessToken) {
      return;
    }

    try {
      const { data } = await boboApi.createAgentThread('新会话');
      const thread = { ...data, isLocal: false } as ChatThread;
      const threadId = threadIdentity(thread);
      setThreads((prev) => [thread, ...prev]);
      setActiveThreadId(threadId);
      setThreadMessages((prev) => ({ ...prev, [threadId]: [] }));
      loadedThreadIdsRef.current.delete(threadId);
    } catch {
      const localThread = createLocalThread('新会话');
      const threadId = threadIdentity(localThread);
      setThreads((prev) => [localThread, ...prev]);
      setActiveThreadId(threadId);
      setThreadMessages((prev) => ({ ...prev, [threadId]: [] }));
    }
  };

  const send = async () => {
    const text = input.trim();
    if (!text || sending || !accessToken) {
      return;
    }

    setInput('');
    setSending(true);

    try {
      const thread = await ensureThread();
      if (!thread) {
        setSending(false);
        return;
      }

      const threadId = threadIdentity(thread);
      const assistantMessageId = `assistant-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      setThreadMessages((prev) => {
        const current = prev[threadId] ?? [];
        return {
          ...prev,
          [threadId]: [
            ...current,
            { role: 'user', content: text },
            { id: assistantMessageId, role: 'assistant', content: '' },
          ],
        };
      });

      const makeRequest = (bearerToken: string) =>
        fetch(`${baseUrl}/bobo/agent/chat`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            Authorization: `Bearer ${bearerToken}`,
          },
          body: JSON.stringify({
            message: text,
            thread_id: threadId,
            user_id: userId,
            max_steps: 8,
          }),
        });

      let res = await makeRequest(accessToken);

      if (res.status === 401 && refreshToken) {
        try {
          const { data } = await boboApi.refresh(refreshToken);
          setSession({
            accessToken: data.access_token,
            refreshToken: data.refresh_token,
            userId: data.user_id,
          });
          res = await makeRequest(data.access_token);
        } catch {
          logout();
        }
      }

      if (!res.ok) {
        const body = await res.text();
        throw new Error(parseApiErrorMessage(body, res.status));
      }

      const reader = res.body?.getReader();
      const decoder = new TextDecoder('utf-8');
      let buffer = '';
      let assistant = '';
      let menuResults: MenuSearchItem[] = [];
      const handlePayloadLine = (line: string) => {
        if (!line.startsWith('data: ')) {
          return;
        }
        const payloadText = line.slice(6).trim();
        if (!payloadText) {
          return;
        }
        try {
          const payload = JSON.parse(payloadText);
          if (payload.type === 'text') {
            assistant += String(payload.content ?? '');
            upsertStreamingAssistant(threadId, assistantMessageId, (current) => ({
              ...current,
              content: assistant,
            }));
          }
          if (payload.type === 'error') {
            assistant += `\n[error] ${String(payload.error ?? '')}`;
            upsertStreamingAssistant(threadId, assistantMessageId, (current) => ({
              ...current,
              content: assistant,
            }));
          }
          if (payload.type === 'tool_result' && payload.tool === 'search_menu') {
            const output = payload.output;
            const parsed = typeof output === 'string' ? JSON.parse(output) : output;
            if (parsed?.results && Array.isArray(parsed.results)) {
              menuResults = parsed.results;
              upsertStreamingAssistant(threadId, assistantMessageId, (current) => ({
                ...current,
                menuResults,
              }));
            }
          }
        } catch {
          // Ignore malformed event lines from SSE fallback payloads.
        }
      };

      if (reader) {
        while (true) {
          const { done, value } = await reader.read();
          if (done) {
            buffer += decoder.decode();
            break;
          }
          buffer += decoder.decode(value, { stream: true });
          const parts = buffer.split('\n');
          buffer = parts.pop() ?? '';
          for (const line of parts) {
            handlePayloadLine(line);
          }
        }
      } else {
        const raw = await res.text();
        for (const line of raw.split('\n')) {
          handlePayloadLine(line);
        }
      }

      if (buffer.trim()) {
        for (const line of buffer.split('\n')) {
          handlePayloadLine(line);
        }
      }

      upsertStreamingAssistant(threadId, assistantMessageId, (current) => ({
        ...current,
        content: assistant.trim() || '(No response)',
        menuResults,
      }));

      await Promise.all([refreshActiveThread(threadId), loadMemoryState()]);
    } catch (err) {
      const ensuredThread = activeThread ?? (await ensureThread());
      const threadId = ensuredThread ? threadIdentity(ensuredThread) : 'fallback';
      setThreadMessages((prev) => {
        const current = prev[threadId] ?? [];
        return {
          ...prev,
          [threadId]: [
            ...current,
            {
              role: 'assistant',
              content: `请求失败：${err instanceof Error ? err.message : 'unknown error'}`,
            } as ChatMessage,
          ],
        };
      });
    } finally {
      setSending(false);
    }
  };

  if (!accessToken) {
    return (
      <View style={[styles.container, { paddingTop: insets.top + 24 }]}>
        <Text style={styles.title}>AI Chat</Text>
        <Text style={styles.tip}>You are not logged in yet.</Text>
        {DEV_LOGIN_ENABLED ? (
          <AppButton
            label={loginPending ? 'Logging in...' : 'Dev Login (dev/dev123456)'}
            onPress={loginAsDev}
            disabled={loginPending}
            loading={loginPending}
            style={styles.loginBtn}
          />
        ) : (
          <Text style={styles.tip}>Dev login is disabled in this build.</Text>
        )}
        {loginError ? <Text style={styles.errorText}>{loginError}</Text> : null}
      </View>
    );
  }

  return (
    <View style={styles.container}>
      <View style={styles.backgroundGlowTop} />
      <View style={styles.backgroundGlowBottom} />

      <ScrollView
        ref={pageScrollRef}
        style={styles.pageScroll}
        contentContainerStyle={[styles.pageContent, { paddingTop: insets.top + 10, paddingBottom: composerBottomOffset + 120 }]}
        showsVerticalScrollIndicator={false}
        keyboardShouldPersistTaps="handled"
      >
        <LinearGradient
          colors={['#FFF1E7', '#FDDDE7', '#FFF6CF']}
          start={{ x: 0, y: 0 }}
          end={{ x: 1, y: 1 }}
          style={styles.minimalHero}
        >
          <View style={styles.minimalTopbar}>
            <View style={styles.minimalBrandBlock}>
              <Text style={styles.eyebrow}>Bobo Intelligence</Text>
              <Text style={styles.title}>AI Chat</Text>
            </View>
            <Pressable style={styles.sidebarTrigger} onPress={() => setSidebarOpen(true)}>
              <Ionicons name="menu" size={18} color="#14213D" />
            </Pressable>
          </View>
        </LinearGradient>

        {!activeMessages.length ? (
          <View style={styles.emptyStage}>
            <View style={styles.welcomeCard}>
              <Text style={styles.welcomeEyebrow}>Start Here</Text>
              <Text style={styles.welcomeTitle}>今天想喝什么？</Text>
              <View style={styles.promptGrid}>
                {['预算 20 元，今天喝什么', '记住我默认少糖少冰', '最近别推荐太甜的', '想喝果茶，但不要太贵'].map((prompt) => (
                  <Pressable key={prompt} style={styles.promptCard} onPress={() => setInput(prompt)}>
                    <Text style={styles.promptCardText}>{prompt}</Text>
                  </Pressable>
                ))}
              </View>
            </View>
          </View>
        ) : null}

        {(threadLoadError || memoryLoadError) && (
          <View style={styles.syncNote}>
            <Ionicons name="alert-circle-outline" size={14} color="#9A5B13" />
            <Text style={styles.syncNoteText}>{threadLoadError || memoryLoadError}</Text>
          </View>
        )}

        <View style={[styles.chatPanel, !activeMessages.length && styles.chatPanelCompact]}>
          <View style={styles.chatHeader}>
            <View>
              <Text style={styles.chatTitle}>{activeThread ? formatDateTime(threadUpdatedAt(activeThread)) : '新对话'}</Text>
              <Text style={styles.chatCaption}>
                {activeMessages.length ? '继续当前对话，额度会按实际模型消耗累计。' : '开始后会按每日 AI 额度累计消耗。'}
              </Text>
            </View>
            <View style={styles.statusPill}>
              <View style={[styles.statusDot, sending && styles.statusDotBusy]} />
              <Text style={styles.statusText}>{sending ? '思考中' : '在线'}</Text>
            </View>
          </View>

          <View style={styles.chatArea}>
            {initializing && !activeMessages.length ? (
              <View style={styles.emptyState}>
                <ActivityIndicator color="#14213D" size="small" />
                <Text style={styles.tip}>正在恢复最近会话...</Text>
              </View>
            ) : null}

            {activeMessages.map((message, idx) => (
              <View
                key={message.id ?? `${activeThreadId ?? 'thread'}-${idx}-${message.role}`}
                style={[styles.messageRow, message.role === 'user' ? styles.messageRowUser : styles.messageRowAssistant]}
              >
                {message.role === 'assistant' ? (
                  <View style={styles.avatar}>
                    <Ionicons name="sparkles" size={12} color="#FFFFFF" />
                  </View>
                ) : null}

                {isPendingAssistantMessage(message) ? (
                  <View style={styles.thinkingCard}>
                    <View style={styles.thinkingHeader}>
                      <View style={styles.thinkingPulse}>
                        <Ionicons name="sparkles" size={12} color="#14213D" />
                      </View>
                      <View style={styles.thinkingCopy}>
                        <Text style={styles.thinkingTitle}>Bobo 正在思考</Text>
                        <Text style={styles.thinkingSubtitle}>整理偏好、预算和最近上下文</Text>
                      </View>
                    </View>
                    <View style={styles.thinkingSkeleton}>
                      {[0, 1, 2].map((index) => (
                        <View
                          key={index}
                          style={[
                            styles.shimmerBar,
                            index === 0 && styles.shimmerBarWide,
                            index === 1 && styles.shimmerBarMedium,
                            index === 2 && styles.shimmerBarShort,
                          ]}
                        >
                          <Animated.View
                            pointerEvents="none"
                            style={[
                              styles.shimmerSweep,
                              {
                                transform: [{ translateX: shimmerTranslate }],
                              },
                            ]}
                          >
                            <LinearGradient
                              colors={['rgba(255,255,255,0)', 'rgba(255,255,255,0.82)', 'rgba(255,255,255,0)']}
                              start={{ x: 0, y: 0.5 }}
                              end={{ x: 1, y: 0.5 }}
                              style={styles.shimmerGradient}
                            />
                          </Animated.View>
                        </View>
                      ))}
                    </View>
                  </View>
                ) : (
                  <View style={[styles.bubble, message.role === 'user' ? styles.userBubble : styles.assistantBubble]}>
                    <Text style={message.role === 'user' ? styles.userText : styles.assistantText}>{message.content}</Text>

                    {message.role === 'assistant' && message.menuResults?.length ? (
                      <View style={styles.menuResults}>
                        {message.menuResults.map((item) => (
                          <View key={item.id} style={styles.menuCard}>
                            <View style={styles.menuCardTop}>
                              <Text style={styles.menuName}>
                                {item.brand} {item.name}
                              </Text>
                              {typeof item.price === 'number' ? <Text style={styles.menuPrice}>¥{Number(item.price).toFixed(0)}</Text> : null}
                            </View>
                            <Text style={styles.menuMeta}>{item.size || '常规杯'}</Text>
                            {item.description ? <Text style={styles.menuDescription}>{item.description}</Text> : null}
                          </View>
                        ))}
                      </View>
                    ) : null}
                  </View>
                )}
              </View>
            ))}
          </View>
        </View>
      </ScrollView>

      <View style={[styles.composerWrap, { bottom: composerBottomOffset, paddingBottom: insets.bottom + 10 }]}>
        <View style={styles.composerCard}>
          <View style={styles.inputShell}>
            <TextInput
              value={input}
              onChangeText={setInput}
              placeholder="问问 Bobo 今天喝什么..."
              placeholderTextColor="#8B909C"
              style={styles.input}
              editable={!sending}
              onSubmitEditing={send}
            />
            <Text style={styles.inputHint}>
              对话和记忆成本会统一计入每日 AI 额度；额度不足时会直接提醒你。
            </Text>
          </View>
          <AppButton
            label="发送"
            onPress={send}
            disabled={!input.trim() || sending}
            loading={sending}
            style={[styles.sendBtn, (!input.trim() || sending) && styles.sendBtnDisabled]}
          />
        </View>
      </View>

      <Modal visible={sidebarOpen} animationType="fade" transparent onRequestClose={() => setSidebarOpen(false)}>
        <View style={styles.sidebarOverlay}>
          <Pressable style={styles.sidebarBackdrop} onPress={() => setSidebarOpen(false)} />
          <View style={[styles.sidebarPanel, { paddingTop: insets.top + 18, paddingBottom: insets.bottom + 24 }]}>
            <View style={styles.sidebarHeader}>
              <View>
                <Text style={styles.sidebarTitle}>对话侧边栏</Text>
                <Text style={styles.sidebarSubtitle}>历史和偏好都放这里，首页只保留输入。</Text>
              </View>
              <Pressable style={styles.sidebarClose} onPress={() => setSidebarOpen(false)}>
                <Ionicons name="close" size={18} color="#14213D" />
              </Pressable>
            </View>

            <Pressable
              style={styles.sidebarPrimaryAction}
              onPress={() => {
                setSidebarOpen(false);
                void handleNewThread();
              }}
            >
              <Ionicons name="add" size={16} color="#14213D" />
              <Text style={styles.sidebarPrimaryActionText}>新会话</Text>
            </Pressable>
            <ScrollView
              style={styles.sidebarScroll}
              showsVerticalScrollIndicator={false}
              contentContainerStyle={styles.sidebarScrollContent}
            >
              <View style={styles.sidebarSection}>
                <Text style={styles.sidebarSectionTitle}>用户画像</Text>
                <Text style={styles.sidebarSectionHint}>Bobo 当前会带着这些稳定偏好来回答你。</Text>
                <View style={styles.memoryChipWrap}>
                  {(profileHighlights.length ? profileHighlights : ['还没有稳定偏好']).map((chip) => (
                    <View key={chip} style={styles.memoryChip}>
                      <Text style={styles.memoryChipText}>{chip}</Text>
                    </View>
                  ))}
                </View>
                <View style={styles.portraitCard}>
                  <View style={styles.portraitHeadline}>
                    <View style={styles.portraitIcon}>
                      <Ionicons name="person-circle-outline" size={16} color="#14213D" />
                    </View>
                    <Text style={styles.portraitTitle}>画像摘要</Text>
                  </View>
                  <View style={styles.portraitList}>
                    {portraitRows.length ? (
                      portraitRows.map((item) => (
                        <View key={`${item.label}-${item.value}`} style={styles.portraitRow}>
                          <Text style={styles.portraitLabel}>{item.label}</Text>
                          <Text style={styles.portraitValue}>{item.value}</Text>
                        </View>
                      ))
                    ) : (
                      <Text style={styles.memoryMutedText}>还没有足够的对话和记录来形成稳定画像。</Text>
                    )}
                  </View>
                </View>
              </View>

              <View style={styles.sidebarSection}>
                <Text style={styles.sidebarSectionTitle}>长期记忆</Text>
                <View style={styles.memoryDetailList}>
                  {memoryHighlights.length ? (
                    memoryHighlights.map((item) => (
                      <View key={item} style={styles.memoryNote}>
                        <View style={styles.memoryNoteDot} />
                        <Text style={styles.memoryNoteText}>{item}</Text>
                      </View>
                    ))
                  ) : (
                    <Text style={styles.memoryMutedText}>当前没有更多长期记忆摘要。</Text>
                  )}
                </View>
              </View>

              <View style={styles.sidebarSection}>
                <Text style={styles.sidebarSectionTitle}>上下文</Text>
                <View style={styles.sidebarStatRow}>
                  <View style={styles.sidebarStatCard}>
                    <Text style={styles.sidebarStatLabel}>当前线程消息</Text>
                    <Text style={styles.sidebarStatValue}>{activeMessages.length}</Text>
                  </View>
                  <View style={styles.sidebarStatCard}>
                    <Text style={styles.sidebarStatLabel}>长期记忆条目</Text>
                    <Text style={styles.sidebarStatValue}>{memoryItems.length}</Text>
                  </View>
                </View>
              </View>

              <View style={styles.sidebarSection}>
                <Text style={styles.sidebarSectionTitle}>最近会话</Text>
                <View style={styles.sidebarThreadList}>
                  <Pressable
                    style={[
                      styles.sidebarThreadItem,
                      styles.sidebarThreadEntryNew,
                    ]}
                    onPress={() => {
                      setSidebarOpen(false);
                      void handleNewThread();
                    }}
                  >
                    <View style={styles.sidebarThreadEntryHeader}>
                      <View style={styles.sidebarThreadEntryIcon}>
                        <Ionicons name="add" size={14} color="#14213D" />
                      </View>
                      <Text style={styles.sidebarThreadTitle}>新会话</Text>
                    </View>
                    <Text style={styles.sidebarThreadMeta}>开启一个新的对话线程</Text>
                  </Pressable>

                  {threads.map((thread) => {
                    const threadId = threadIdentity(thread);
                    const selected = threadId === activeThreadId;
                    return (
                      <Pressable
                        key={threadId}
                        style={[styles.sidebarThreadItem, selected && styles.sidebarThreadItemActive]}
                        onPress={() => {
                          setSidebarOpen(false);
                          void handleSelectThread(thread);
                        }}
                      >
                        <Text style={[styles.sidebarThreadTitle, selected && styles.sidebarThreadTitleActive]} numberOfLines={1}>
                          {formatDateTime(threadUpdatedAt(thread))}
                        </Text>
                        <Text style={[styles.sidebarThreadMeta, selected && styles.sidebarThreadMetaActive]}>
                          {formatDateTime(threadUpdatedAt(thread))}
                        </Text>
                      </Pressable>
                    );
                  })}
                </View>
              </View>
            </ScrollView>
          </View>
        </View>
      </Modal>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#FBF7F2',
  },
  pageScroll: {
    flex: 1,
  },
  pageContent: {
    paddingHorizontal: 16,
  },
  backgroundGlowTop: {
    position: 'absolute',
    top: 88,
    right: -26,
    width: 156,
    height: 156,
    borderRadius: 999,
    backgroundColor: 'rgba(255, 183, 139, 0.22)',
  },
  backgroundGlowBottom: {
    position: 'absolute',
    top: 300,
    left: -44,
    width: 144,
    height: 144,
    borderRadius: 999,
    backgroundColor: 'rgba(244, 171, 196, 0.18)',
  },
  heroCard: {
    borderRadius: 30,
    padding: 18,
    marginBottom: 14,
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.82)',
    shadowColor: '#E8BEA5',
    shadowOffset: { width: 0, height: 18 },
    shadowOpacity: 0.16,
    shadowRadius: 28,
    elevation: 8,
  },
  minimalHero: {
    borderRadius: 32,
    paddingHorizontal: 16,
    paddingTop: 14,
    paddingBottom: 10,
    marginBottom: 10,
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.82)',
    shadowColor: '#E8BEA5',
    shadowOffset: { width: 0, height: 18 },
    shadowOpacity: 0.16,
    shadowRadius: 28,
    elevation: 8,
    minHeight: 96,
  },
  heroTopRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    gap: 12,
    marginBottom: 14,
  },
  heroCopy: {
    flex: 1,
  },
  minimalTopbar: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
    gap: 12,
    marginBottom: 0,
  },
  minimalBrandBlock: {
    flex: 1,
  },
  sidebarTrigger: {
    width: 36,
    height: 36,
    borderRadius: 18,
    backgroundColor: 'rgba(255,255,255,0.74)',
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.9)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  eyebrow: {
    fontSize: 10,
    fontWeight: '800',
    color: 'rgba(20, 33, 61, 0.52)',
    letterSpacing: 1,
    textTransform: 'uppercase',
    marginBottom: 4,
  },
  title: {
    fontSize: 28,
    fontWeight: '800',
    color: '#14213D',
    marginBottom: 4,
  },
  subtitle: {
    fontSize: 13,
    lineHeight: 19,
    color: 'rgba(20, 33, 61, 0.7)',
  },
  heroBadge: {
    width: 40,
    height: 40,
    borderRadius: 20,
    backgroundColor: 'rgba(255,255,255,0.7)',
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.88)',
  },
  signalStack: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
    marginBottom: 12,
  },
  signalPillStrong: {
    borderRadius: 18,
    paddingHorizontal: 14,
    paddingVertical: 11,
    backgroundColor: '#14213D',
    minWidth: 120,
  },
  signalLabelStrong: {
    fontSize: 11,
    fontWeight: '700',
    color: 'rgba(255,255,255,0.7)',
    marginBottom: 4,
  },
  signalValueStrong: {
    fontSize: 18,
    fontWeight: '800',
    color: '#FFFFFF',
  },
  signalPill: {
    borderRadius: 18,
    paddingHorizontal: 14,
    paddingVertical: 11,
    backgroundColor: 'rgba(255,255,255,0.65)',
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.82)',
    minWidth: 108,
  },
  signalLabel: {
    fontSize: 11,
    fontWeight: '700',
    color: 'rgba(20, 33, 61, 0.58)',
    marginBottom: 4,
  },
  signalValue: {
    fontSize: 18,
    fontWeight: '800',
    color: '#14213D',
  },
  heroHint: {
    fontSize: 13,
    lineHeight: 20,
    color: 'rgba(20, 33, 61, 0.76)',
    marginBottom: 14,
  },
  heroActions: {
    flexDirection: 'row',
    gap: 10,
  },
  primaryAction: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    borderRadius: 999,
    paddingHorizontal: 16,
    paddingVertical: 11,
    backgroundColor: '#E6FF7A',
    borderWidth: 1,
    borderColor: 'rgba(170, 209, 54, 0.72)',
  },
  primaryActionText: {
    fontSize: 13,
    fontWeight: '800',
    color: '#14213D',
  },
  secondaryAction: {
    borderRadius: 999,
    paddingHorizontal: 16,
    paddingVertical: 11,
    backgroundColor: 'rgba(255,255,255,0.68)',
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.86)',
  },
  secondaryActionText: {
    fontSize: 13,
    fontWeight: '700',
    color: '#14213D',
  },
  emptyStage: {
    marginBottom: 12,
  },
  section: {
    marginBottom: 12,
  },
  sectionHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
    gap: 12,
    marginBottom: 10,
  },
  sectionTitle: {
    fontSize: 16,
    fontWeight: '800',
    color: '#14213D',
  },
  sectionCaption: {
    marginTop: 4,
    fontSize: 12,
    color: '#6B7280',
  },
  threadRail: {
    gap: 10,
    paddingRight: 10,
  },
  threadChip: {
    width: 156,
    borderRadius: 22,
    paddingHorizontal: 14,
    paddingVertical: 14,
    backgroundColor: 'rgba(255,255,255,0.8)',
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.9)',
    shadowColor: '#E7D9D1',
    shadowOffset: { width: 0, height: 8 },
    shadowOpacity: 0.08,
    shadowRadius: 20,
    elevation: 4,
  },
  threadChipActive: {
    backgroundColor: '#18233D',
    borderColor: '#18233D',
  },
  threadChipTitle: {
    fontSize: 13,
    fontWeight: '800',
    color: '#14213D',
    marginBottom: 10,
  },
  threadChipTitleActive: {
    color: '#FFFFFF',
  },
  threadChipMeta: {
    fontSize: 11,
    color: '#6B7280',
  },
  threadChipMetaActive: {
    color: 'rgba(255,255,255,0.74)',
  },
  memoryCard: {
    borderRadius: 26,
    padding: 16,
    backgroundColor: 'rgba(255,255,255,0.78)',
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.9)',
    marginBottom: 12,
    shadowColor: '#E7D9D1',
    shadowOffset: { width: 0, height: 10 },
    shadowOpacity: 0.08,
    shadowRadius: 22,
    elevation: 4,
  },
  memoryChipWrap: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
  },
  memoryChip: {
    borderRadius: 999,
    paddingHorizontal: 12,
    paddingVertical: 9,
    backgroundColor: '#FFF9EC',
    borderWidth: 1,
    borderColor: 'rgba(255, 214, 120, 0.44)',
  },
  memoryChipText: {
    fontSize: 13,
    color: '#14213D',
  },
  memoryDetailList: {
    marginTop: 14,
    gap: 10,
  },
  portraitCard: {
    marginTop: 14,
    borderRadius: 18,
    padding: 13,
    backgroundColor: '#F7F3ED',
    borderWidth: 1,
    borderColor: 'rgba(226, 213, 196, 0.9)',
  },
  portraitHeadline: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    marginBottom: 10,
  },
  portraitIcon: {
    width: 28,
    height: 28,
    borderRadius: 14,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: '#FFF8EA',
    borderWidth: 1,
    borderColor: 'rgba(240, 203, 140, 0.6)',
  },
  portraitTitle: {
    fontSize: 12,
    fontWeight: '800',
    color: '#7A828E',
    textTransform: 'uppercase',
    letterSpacing: 0.5,
  },
  portraitList: {
    gap: 10,
  },
  portraitRow: {
    gap: 4,
  },
  portraitLabel: {
    fontSize: 12,
    fontWeight: '700',
    color: '#7A828E',
  },
  portraitValue: {
    fontSize: 13,
    lineHeight: 19,
    color: '#243042',
  },
  memoryNote: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 10,
  },
  memoryNoteDot: {
    width: 7,
    height: 7,
    borderRadius: 999,
    backgroundColor: '#F1B54C',
    marginTop: 6,
  },
  memoryNoteText: {
    flex: 1,
    fontSize: 13,
    lineHeight: 19,
    color: '#495364',
  },
  memoryMutedText: {
    fontSize: 13,
    color: '#6B7280',
  },
  syncNote: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    borderRadius: 14,
    paddingHorizontal: 12,
    paddingVertical: 10,
    backgroundColor: '#FFF4DF',
    marginBottom: 12,
  },
  syncNoteText: {
    flex: 1,
    fontSize: 12,
    lineHeight: 18,
    color: '#9A5B13',
  },
  chatPanel: {
    borderRadius: 30,
    backgroundColor: 'rgba(255,255,255,0.84)',
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.9)',
    overflow: 'hidden',
    shadowColor: '#D8DEE9',
    shadowOffset: { width: 0, height: 16 },
    shadowOpacity: 0.12,
    shadowRadius: 26,
    elevation: 7,
  },
  chatHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    gap: 12,
    paddingHorizontal: 16,
    paddingTop: 16,
    paddingBottom: 10,
  },
  chatTitle: {
    fontSize: 15,
    fontWeight: '800',
    color: '#14213D',
  },
  chatCaption: {
    marginTop: 4,
    fontSize: 11,
    lineHeight: 16,
    color: '#6B7280',
  },
  statusPill: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    borderRadius: 999,
    paddingHorizontal: 10,
    paddingVertical: 7,
    backgroundColor: '#F8FAFC',
    borderWidth: 1,
    borderColor: '#E8EDF3',
  },
  statusDot: {
    width: 8,
    height: 8,
    borderRadius: 999,
    backgroundColor: '#55B46A',
  },
  statusDotBusy: {
    backgroundColor: '#F1B54C',
  },
  statusText: {
    fontSize: 11,
    fontWeight: '700',
    color: '#14213D',
  },
  chatArea: {
    paddingHorizontal: 14,
    paddingBottom: 20,
  },
  emptyState: {
    paddingVertical: 18,
    gap: 8,
    alignItems: 'center',
  },
  welcomeCard: {
    borderRadius: 20,
    paddingHorizontal: 14,
    paddingVertical: 13,
    marginTop: 4,
    backgroundColor: '#FFF9F1',
    borderWidth: 1,
    borderColor: 'rgba(255, 214, 120, 0.34)',
  },
  welcomeEyebrow: {
    fontSize: 10,
    fontWeight: '800',
    color: '#A86A18',
    letterSpacing: 0.9,
    textTransform: 'uppercase',
    marginBottom: 6,
  },
  welcomeTitle: {
    fontSize: 16,
    fontWeight: '800',
    color: '#14213D',
    marginBottom: 10,
  },
  promptGrid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
    justifyContent: 'space-between',
  },
  promptCard: {
    borderRadius: 16,
    paddingHorizontal: 12,
    paddingVertical: 11,
    width: '48%',
    backgroundColor: 'rgba(255,255,255,0.74)',
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.92)',
  },
  promptCardText: {
    fontSize: 13,
    lineHeight: 18,
    color: '#14213D',
    fontWeight: '700',
  },
  chatPanelCompact: {
    minHeight: 120,
  },
  messageRow: {
    flexDirection: 'row',
    gap: 10,
    marginBottom: 12,
    alignItems: 'flex-start',
  },
  messageRowUser: {
    justifyContent: 'flex-end',
  },
  messageRowAssistant: {
    justifyContent: 'flex-start',
  },
  avatar: {
    width: 26,
    height: 26,
    borderRadius: 13,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: '#14213D',
    marginTop: 4,
  },
  bubble: {
    maxWidth: '84%',
    borderRadius: 22,
    paddingHorizontal: 14,
    paddingVertical: 12,
  },
  userBubble: {
    backgroundColor: '#E8FF86',
    borderTopRightRadius: 8,
  },
  assistantBubble: {
    backgroundColor: '#FFFFFF',
    borderTopLeftRadius: 8,
    borderWidth: 1,
    borderColor: '#EEF1F4',
  },
  thinkingCard: {
    width: '84%',
    maxWidth: '84%',
    minWidth: 196,
    borderRadius: 22,
    paddingHorizontal: 14,
    paddingVertical: 13,
    backgroundColor: 'rgba(255,255,255,0.98)',
    borderWidth: 1,
    borderColor: '#EEF1F4',
    shadowColor: '#D8DEE9',
    shadowOffset: { width: 0, height: 8 },
    shadowOpacity: 0.1,
    shadowRadius: 18,
    elevation: 4,
    overflow: 'hidden',
  },
  thinkingHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    marginBottom: 12,
  },
  thinkingPulse: {
    width: 30,
    height: 30,
    borderRadius: 15,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: '#F3F6FB',
    borderWidth: 1,
    borderColor: '#E6ECF5',
  },
  thinkingCopy: {
    flex: 1,
    gap: 3,
  },
  thinkingTitle: {
    fontSize: 14,
    fontWeight: '700',
    color: '#243042',
  },
  thinkingSubtitle: {
    fontSize: 11,
    lineHeight: 16,
    color: '#7A828E',
  },
  thinkingSkeleton: {
    gap: 8,
  },
  shimmerBar: {
    height: 10,
    borderRadius: 999,
    backgroundColor: '#EEF2F7',
    overflow: 'hidden',
  },
  shimmerBarWide: {
    width: '88%',
  },
  shimmerBarMedium: {
    width: '72%',
  },
  shimmerBarShort: {
    width: '58%',
  },
  shimmerSweep: {
    ...StyleSheet.absoluteFillObject,
    width: 160,
  },
  shimmerGradient: {
    flex: 1,
  },
  userText: {
    fontSize: 14,
    lineHeight: 21,
    color: '#14213D',
  },
  assistantText: {
    fontSize: 14,
    lineHeight: 21,
    color: '#243042',
  },
  menuResults: {
    marginTop: 12,
    gap: 10,
  },
  menuCard: {
    borderRadius: 16,
    padding: 12,
    backgroundColor: '#F8FAFC',
    borderWidth: 1,
    borderColor: '#EBF0F4',
  },
  menuCardTop: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    gap: 12,
    alignItems: 'flex-start',
  },
  menuName: {
    flex: 1,
    fontSize: 13,
    fontWeight: '800',
    color: '#14213D',
  },
  menuPrice: {
    fontSize: 13,
    fontWeight: '800',
    color: '#A86A18',
  },
  menuMeta: {
    marginTop: 6,
    fontSize: 12,
    color: '#6B7280',
  },
  menuDescription: {
    marginTop: 6,
    fontSize: 12,
    lineHeight: 18,
    color: '#4B5563',
  },
  composerWrap: {
    position: 'absolute',
    left: 0,
    right: 0,
    paddingHorizontal: 14,
    paddingTop: 8,
    backgroundColor: 'transparent',
  },
  composerCard: {
    borderRadius: 24,
    padding: 10,
    flexDirection: 'row',
    alignItems: 'flex-end',
    gap: 10,
    backgroundColor: 'rgba(255,255,255,0.92)',
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.96)',
    shadowColor: '#D8DEE9',
    shadowOffset: { width: 0, height: 8 },
    shadowOpacity: 0.12,
    shadowRadius: 20,
    elevation: 8,
  },
  inputShell: {
    flex: 1,
    borderRadius: 18,
    paddingHorizontal: 14,
    paddingTop: 12,
    paddingBottom: 10,
    backgroundColor: '#F7F8FA',
  },
  input: {
    minHeight: 22,
    maxHeight: 96,
    fontSize: 15,
    color: '#14213D',
  },
  inputHint: {
    marginTop: 6,
    fontSize: 11,
    lineHeight: 16,
    color: '#7A828E',
  },
  sendBtn: {
    minWidth: 88,
    borderRadius: 18,
  },
  sendBtnDisabled: {
    opacity: 0.45,
  },
  tip: {
    fontSize: 13,
    color: '#6B7280',
    lineHeight: 19,
  },
  errorText: {
    color: '#C2410C',
    marginTop: 12,
  },
  loginBtn: {
    marginTop: 18,
  },
  sidebarOverlay: {
    flex: 1,
    flexDirection: 'row',
    justifyContent: 'flex-end',
    backgroundColor: 'rgba(20, 33, 61, 0.16)',
  },
  sidebarBackdrop: {
    flex: 1,
  },
  sidebarPanel: {
    width: '86%',
    maxWidth: 380,
    backgroundColor: '#FFFDF9',
    paddingHorizontal: 18,
    borderTopLeftRadius: 28,
    borderBottomLeftRadius: 28,
    borderLeftWidth: 1,
    borderColor: 'rgba(230, 219, 208, 0.88)',
    shadowColor: '#AE9B8B',
    shadowOffset: { width: -10, height: 0 },
    shadowOpacity: 0.14,
    shadowRadius: 28,
    elevation: 12,
  },
  sidebarHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
    gap: 12,
    marginBottom: 18,
  },
  sidebarTitle: {
    fontSize: 22,
    fontWeight: '800',
    color: '#14213D',
    marginBottom: 6,
  },
  sidebarSubtitle: {
    fontSize: 12,
    lineHeight: 18,
    color: '#6B7280',
  },
  sidebarClose: {
    width: 38,
    height: 38,
    borderRadius: 19,
    backgroundColor: '#F5F2ED',
    alignItems: 'center',
    justifyContent: 'center',
  },
  sidebarPrimaryAction: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 8,
    borderRadius: 18,
    paddingVertical: 14,
    backgroundColor: '#E6FF7A',
    borderWidth: 1,
    borderColor: 'rgba(170, 209, 54, 0.72)',
    marginBottom: 18,
  },
  sidebarPrimaryActionText: {
    fontSize: 14,
    fontWeight: '800',
    color: '#14213D',
  },
  sidebarScroll: {
    flex: 1,
  },
  sidebarScrollContent: {
    paddingBottom: 8,
  },
  sidebarSection: {
    marginBottom: 18,
  },
  sidebarSectionTitle: {
    fontSize: 14,
    fontWeight: '800',
    color: '#14213D',
    marginBottom: 10,
  },
  sidebarSectionHint: {
    marginTop: -2,
    marginBottom: 10,
    fontSize: 12,
    lineHeight: 18,
    color: '#6B7280',
  },
  sidebarStatRow: {
    flexDirection: 'row',
    gap: 10,
  },
  sidebarStatCard: {
    flex: 1,
    borderRadius: 18,
    padding: 12,
    backgroundColor: '#F7F3ED',
  },
  sidebarStatLabel: {
    fontSize: 11,
    fontWeight: '700',
    color: '#7A828E',
    marginBottom: 6,
  },
  sidebarStatValue: {
    fontSize: 17,
    fontWeight: '800',
    color: '#14213D',
  },
  sidebarThreadList: {
    gap: 10,
    paddingBottom: 6,
  },
  sidebarThreadItem: {
    borderRadius: 18,
    paddingHorizontal: 14,
    paddingVertical: 13,
    backgroundColor: '#F7F3ED',
  },
  sidebarThreadEntryNew: {
    backgroundColor: '#FFF9E8',
    borderWidth: 1,
    borderColor: 'rgba(241, 181, 76, 0.28)',
  },
  sidebarThreadEntryHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 10,
    marginBottom: 6,
  },
  sidebarThreadEntryIcon: {
    width: 22,
    height: 22,
    borderRadius: 11,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: '#E6FF7A',
    borderWidth: 1,
    borderColor: 'rgba(170, 209, 54, 0.6)',
  },
  sidebarThreadItemActive: {
    backgroundColor: '#18233D',
  },
  sidebarThreadTitle: {
    fontSize: 13,
    fontWeight: '800',
    color: '#14213D',
    marginBottom: 6,
  },
  sidebarThreadTitleActive: {
    color: '#FFFFFF',
  },
  sidebarThreadMeta: {
    fontSize: 11,
    color: '#6B7280',
  },
  sidebarThreadMetaActive: {
    color: 'rgba(255,255,255,0.74)',
  },
});
