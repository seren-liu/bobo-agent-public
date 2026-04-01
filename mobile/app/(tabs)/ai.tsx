import { useEffect, useMemo, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
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
import { boboApi, type AgentMessage, type AgentThread, type MemoryItem, type MemoryProfile, type MenuSearchItem } from '@/lib/api';
import { getApiBaseUrl } from '@/lib/runtimeConfig';
import { useAuthStore } from '@/stores/authStore';

type ChatMessage = {
  role: 'user' | 'assistant';
  content: string;
  menuResults?: MenuSearchItem[];
};

type ChatThread = AgentThread & {
  isLocal?: boolean;
};

const DEV_LOGIN_ENABLED = process.env.EXPO_PUBLIC_ENABLE_DEV_LOGIN === 'true';
const FALLBACK_THREAD_TITLE = '本地临时会话';

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
  return thread.isLocal ? FALLBACK_THREAD_TITLE : '未命名会话';
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
    return ['尚未加载画像'];
  }
  const chips: string[] = [];
  const drink = profile.drink_preferences ?? {};
  const budget = profile.budget_preferences ?? {};
  const interaction = profile.interaction_preferences ?? {};

  const defaultSugar = drink.default_sugar as string | undefined;
  const defaultIce = drink.default_ice as string | undefined;
  const replyStyle = interaction.reply_style as string | undefined;
  const ceiling = budget.soft_price_ceiling as string | number | undefined;

  if (defaultSugar || defaultIce) {
    chips.push([defaultSugar, defaultIce].filter(Boolean).join(' / '));
  }
  if (Array.isArray(drink.preferred_brands) && drink.preferred_brands.length > 0) {
    chips.push(`偏好品牌: ${(drink.preferred_brands as string[]).slice(0, 2).join('、')}`);
  }
  if (Array.isArray(drink.preferred_categories) && drink.preferred_categories.length > 0) {
    chips.push(`偏好品类: ${(drink.preferred_categories as string[]).slice(0, 2).join('、')}`);
  }
  if (typeof ceiling === 'number' || typeof ceiling === 'string') {
    chips.push(`预算上限: ¥${ceiling}`);
  }
  if (replyStyle) {
    chips.push(`回复风格: ${replyStyle}`);
  }

  return chips.length ? chips : ['画像已加载，但还没有可展示的偏好'];
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
  const [showMemoryPanel, setShowMemoryPanel] = useState(false);
  const [threads, setThreads] = useState<ChatThread[]>([]);
  const [activeThreadId, setActiveThreadId] = useState<string | null>(null);
  const [threadMessages, setThreadMessages] = useState<Record<string, ChatMessage[]>>({});
  const [memoryProfile, setMemoryProfile] = useState<MemoryProfile | null>(null);
  const [memoryItems, setMemoryItems] = useState<MemoryItem[]>([]);
  const loadedThreadIdsRef = useRef(new Set<string>());

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

      if (items.length > 0) {
        setThreadMessages((prev) => ({ ...prev, [threadId]: items }));
      } else if (!threadMessages[threadId]) {
        setThreadMessages((prev) => ({ ...prev, [threadId]: [] }));
      }
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
        if (!threadMessages[nextThreadId]) {
          setThreadMessages((prev) => ({ ...prev, [nextThreadId]: prev[nextThreadId] ?? [] }));
        }
        await loadThreadMessages(nextThread);
      } else {
        const localThread = createLocalThread();
        const localThreadId = threadIdentity(localThread);
        setThreads([localThread]);
        setActiveThreadId(localThreadId);
        setThreadMessages((prev) => ({ ...prev, [localThreadId]: [] }));
      }
    } catch (error) {
      setThreadLoadError(error instanceof Error ? error.message : 'thread load failed');
      const localThread = createLocalThread();
      const localThreadId = threadIdentity(localThread);
      setThreads([localThread]);
      setActiveThreadId(localThreadId);
      setThreadMessages((prev) => ({ ...prev, [localThreadId]: [] }));
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

  const refreshEverything = async () => {
    if (!accessToken) {
      return;
    }

    try {
      setInitializing(true);
      await bootstrapThreads();
    } finally {
      setInitializing(false);
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

  const ensureThread = async (): Promise<ChatThread> => {
    if (activeThread) {
      return activeThread;
    }

    try {
      const { data } = await boboApi.createAgentThread('新会话');
      const thread = { ...data, isLocal: false } as ChatThread;
      setThreads((prev) => [thread, ...prev]);
      const threadId = threadIdentity(thread);
      setActiveThreadId(threadId);
      setThreadMessages((prev) => ({ ...prev, [threadId]: prev[threadId] ?? [] }));
      return thread;
    } catch {
      const localThread = createLocalThread();
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
    if (!threadMessages[threadId]) {
      setThreadMessages((prev) => ({ ...prev, [threadId]: [] }));
    }
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
      const localThread = createLocalThread();
      const threadId = threadIdentity(localThread);
      setThreads((prev) => [localThread, ...prev]);
      setActiveThreadId(threadId);
      setThreadMessages((prev) => ({ ...prev, [threadId]: [] }));
    }
  };

  const persistMemoryProfile = async (patch: Partial<MemoryProfile>) => {
    try {
      const { data } = await boboApi.patchAgentProfile(patch);
      setMemoryProfile(data);
      await loadMemoryState();
    } catch (error) {
      setMemoryLoadError(error instanceof Error ? error.message : 'profile update failed');
    }
  };

  const handleResetProfile = () => {
    Alert.alert('重置画像', '确定要清空当前画像吗？这会重置默认偏好。', [
      { text: '取消', style: 'cancel' },
      {
        text: '重置',
        style: 'destructive',
        onPress: async () => {
          try {
            const { data } = await boboApi.resetAgentProfile();
            setMemoryProfile(data);
            await loadMemoryState();
          } catch (error) {
            setMemoryLoadError(error instanceof Error ? error.message : 'profile reset failed');
          }
        },
      },
    ]);
  };

  const handleMemoryAction = (memory: MemoryItem, action: 'delete' | 'disable') => {
    Alert.alert(action === 'delete' ? '删除记忆' : '停用记忆', `确定要${action === 'delete' ? '删除' : '停用'}这条记忆吗？`, [
      { text: '取消', style: 'cancel' },
      {
        text: action === 'delete' ? '删除' : '停用',
        style: 'destructive',
        onPress: async () => {
          try {
            if (action === 'delete') {
              await boboApi.deleteAgentMemory(memory.id);
            } else {
              await boboApi.disableAgentMemory(memory.id);
            }
            await loadMemoryState();
          } catch (error) {
            setMemoryLoadError(error instanceof Error ? error.message : 'memory action failed');
          }
        },
      },
    ]);
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
      const threadId = threadIdentity(thread);
      setThreadMessages((prev) => {
        const current = prev[threadId] ?? [];
        return {
          ...prev,
          [threadId]: [...current, { role: 'user', content: text }],
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
        throw new Error(body || `HTTP ${res.status}`);
      }

      const raw = await res.text();
      let assistant = '';
      let menuResults: MenuSearchItem[] = [];

      for (const line of raw.split('\n')) {
        if (!line.startsWith('data: ')) {
          continue;
        }
        const payloadText = line.slice(6).trim();
        if (!payloadText) {
          continue;
        }
        try {
          const payload = JSON.parse(payloadText);
          if (payload.type === 'text') {
            assistant += String(payload.content ?? '');
          }
          if (payload.type === 'error') {
            assistant += `\n[error] ${String(payload.error ?? '')}`;
          }
          if (payload.type === 'tool_result' && payload.tool === 'search_menu') {
            const output = payload.output;
            const parsed = typeof output === 'string' ? JSON.parse(output) : output;
            if (parsed?.results && Array.isArray(parsed.results)) {
              menuResults = parsed.results;
            }
          }
        } catch {
          // Ignore malformed event lines from SSE fallback payloads.
        }
      }

      setThreadMessages((prev) => {
        const current = prev[threadId] ?? [];
        const nextMessage: ChatMessage = {
          role: 'assistant',
          content: assistant.trim() || '(No response)',
          menuResults,
        };
        const next = [...current, nextMessage];
        return { ...prev, [threadId]: next };
      });

      await Promise.all([refreshActiveThread(threadId), loadMemoryState()]);
    } catch (err) {
      const threadId = activeThreadId ?? threadIdentity(await ensureThread());
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

  const profileChips = summarizeProfile(memoryProfile);
  const activeThreadMessageCount =
    activeThread && activeThreadId
      ? activeThread.message_count ?? threadMessages[activeThreadId]?.length ?? 0
      : 0;

  return (
    <View style={styles.container}>
      <View style={styles.backgroundOrbOne} />
      <View style={styles.backgroundOrbTwo} />
      <ScrollView
        style={styles.pageScroll}
        contentContainerStyle={[styles.pageContent, { paddingTop: insets.top + 10, paddingBottom: insets.bottom + 124 }]}
        showsVerticalScrollIndicator={false}
        keyboardShouldPersistTaps="handled"
      >
        <LinearGradient
          colors={['#FFD7E2', '#FFC2CF', '#FFF3B0']}
          start={{ x: 0, y: 0 }}
          end={{ x: 1, y: 1 }}
          style={styles.heroCard}
        >
          <View style={styles.heroTopRow}>
            <View style={styles.heroTextBlock}>
              <Text style={styles.eyebrow}>Bobo Intelligence</Text>
              <Text style={styles.title}>AI Chat</Text>
              <Text style={styles.subtitle}>
                {activeThread ? `${threadTitle(activeThread)} · ${formatDateTime(threadUpdatedAt(activeThread))}` : '正在准备会话'}
              </Text>
            </View>
            <View style={styles.heroIconBadge}>
              <Ionicons name="sparkles" size={18} color="#172033" />
            </View>
          </View>

          <View style={styles.metricRow}>
            <View style={styles.metricCard}>
              <Text style={styles.metricLabel}>当前会话</Text>
              <Text style={styles.metricValue}>{activeThreadMessageCount}</Text>
              <Text style={styles.metricMeta}>messages</Text>
            </View>
            <View style={styles.metricCard}>
              <Text style={styles.metricLabel}>长期记忆</Text>
              <Text style={styles.metricValue}>{memoryItems.length}</Text>
              <Text style={styles.metricMeta}>entries</Text>
            </View>
            <View style={styles.metricCard}>
              <Text style={styles.metricLabel}>状态</Text>
              <Text style={styles.metricValueSmall}>{sending ? 'Thinking' : 'Ready'}</Text>
              <Text style={styles.metricMeta}>assistant</Text>
            </View>
          </View>

          <View style={styles.headerActions}>
            <Pressable style={styles.headerChipStrong} onPress={handleNewThread}>
              <Ionicons name="add" size={14} color="#172033" />
              <Text style={styles.headerChipStrongText}>新会话</Text>
            </Pressable>
            <Pressable style={styles.headerChipSoft} onPress={refreshEverything}>
              <Text style={styles.headerChipSoftText}>{initializing ? '刷新中' : '刷新'}</Text>
            </Pressable>
            <Pressable style={styles.headerChipSoft} onPress={() => setShowMemoryPanel((prev) => !prev)}>
              <Text style={styles.headerChipSoftText}>{showMemoryPanel ? '收起记忆' : '展开记忆'}</Text>
            </Pressable>
          </View>
        </LinearGradient>

        <View style={styles.sectionBlock}>
          <View style={styles.sectionHeader}>
            <Text style={styles.sectionTitle}>会话列表</Text>
            <Text style={styles.sectionCaption}>向右滑动切换最近线程</Text>
          </View>
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.threadStrip}>
            {threads.map((thread) => {
              const threadId = threadIdentity(thread);
              const selected = threadId === activeThreadId;
              return (
                <Pressable
                  key={threadId}
                  onPress={() => {
                    void handleSelectThread(thread);
                  }}
                  style={[styles.threadCard, selected && styles.threadCardActive]}
                >
                  {selected ? (
                    <LinearGradient
                      colors={['#172033', '#2A3350']}
                      start={{ x: 0, y: 0 }}
                      end={{ x: 1, y: 1 }}
                      style={styles.threadCardGradient}
                    >
                      <Text style={[styles.threadCardTitle, styles.threadCardTitleActive]} numberOfLines={1}>
                        {threadTitle(thread)}
                      </Text>
                      <Text style={[styles.threadCardMeta, styles.threadCardMetaActive]}>
                        {thread.message_count ?? threadMessages[threadId]?.length ?? 0} 条消息
                      </Text>
                      <Text style={styles.threadCardTimestamp}>{formatDateTime(threadUpdatedAt(thread))}</Text>
                    </LinearGradient>
                  ) : (
                    <>
                      <Text style={styles.threadCardTitle} numberOfLines={1}>
                        {threadTitle(thread)}
                      </Text>
                      <Text style={styles.threadCardMeta}>
                        {thread.message_count ?? threadMessages[threadId]?.length ?? 0} 条消息
                      </Text>
                      <Text style={styles.threadCardTimestamp}>{formatDateTime(threadUpdatedAt(thread))}</Text>
                    </>
                  )}
                </Pressable>
              );
            })}
          </ScrollView>
        </View>

        <View style={[styles.memoryPanel, showMemoryPanel && styles.memoryPanelExpanded]}>
          <View style={styles.sectionHeader}>
            <View>
              <Text style={styles.sectionTitle}>记忆画像</Text>
              <Text style={styles.sectionCaption}>让回答更像你的偏好，而不是通用模板</Text>
            </View>
            <View style={styles.sectionActions}>
              <Pressable style={styles.inlineChip} onPress={() => void loadMemoryState()}>
                <Text style={styles.inlineChipText}>刷新</Text>
              </Pressable>
              {showMemoryPanel ? (
                <Pressable style={styles.inlineChip} onPress={handleResetProfile}>
                  <Text style={styles.inlineChipText}>重置画像</Text>
                </Pressable>
              ) : null}
            </View>
          </View>

          <View style={styles.profileChipWrap}>
            {profileChips.slice(0, showMemoryPanel ? profileChips.length : 3).map((chip) => (
              <View key={chip} style={styles.profileChip}>
                <Text style={styles.profileChipText}>{chip}</Text>
              </View>
            ))}
          </View>

          {showMemoryPanel ? (
            <>
              <View style={styles.quickActions}>
                <Pressable
                  style={styles.inlineChipAccent}
                  onPress={() =>
                    void persistMemoryProfile({
                      drink_preferences: {
                        ...(memoryProfile?.drink_preferences ?? {}),
                        default_sugar: '少糖',
                        default_ice: '少冰',
                      },
                    })
                  }
                >
                  <Text style={styles.inlineChipAccentText}>默认少糖少冰</Text>
                </Pressable>
                <Pressable
                  style={styles.inlineChipAccent}
                  onPress={() =>
                    void persistMemoryProfile({
                      interaction_preferences: {
                        ...(memoryProfile?.interaction_preferences ?? {}),
                        reply_style: 'brief',
                      },
                    })
                  }
                >
                  <Text style={styles.inlineChipAccentText}>回复简洁</Text>
                </Pressable>
              </View>

              <View style={styles.memoryList}>
                {memoryItems.length ? (
                  memoryItems.slice(0, 6).map((memory) => (
                    <View key={memory.id} style={styles.memoryCard}>
                      <View style={styles.memoryCardTop}>
                        <Text style={styles.memoryContent} numberOfLines={3}>
                          {memory.content}
                        </Text>
                        <Text style={styles.memoryBadge}>{memory.memory_type}</Text>
                      </View>
                      <Text style={styles.memoryMeta}>
                        {memory.scope} · {memory.source_kind} · 置信度 {(memory.confidence ?? 0.5).toFixed(2)}
                      </Text>
                      <View style={styles.memoryActions}>
                        <Pressable style={styles.inlineChip} onPress={() => handleMemoryAction(memory, 'disable')}>
                          <Text style={styles.inlineChipText}>停用</Text>
                        </Pressable>
                        <Pressable style={[styles.inlineChip, styles.inlineDangerChip]} onPress={() => handleMemoryAction(memory, 'delete')}>
                          <Text style={[styles.inlineChipText, styles.inlineDangerChipText]}>删除</Text>
                        </Pressable>
                      </View>
                    </View>
                  ))
                ) : (
                  <Text style={styles.tip}>{memoryLoadError ? `记忆加载失败：${memoryLoadError}` : '当前没有长期记忆。'}</Text>
                )}
              </View>
            </>
          ) : null}
        </View>

        {threadLoadError ? <Text style={styles.warnText}>会话列表降级使用本地模式：{threadLoadError}</Text> : null}
        {memoryLoadError ? <Text style={styles.warnText}>记忆面板当前为降级状态：{memoryLoadError}</Text> : null}

        <View style={styles.chatPanel}>
          <View style={styles.chatPanelHeader}>
            <View>
              <Text style={styles.chatPanelTitle}>对话流</Text>
              <Text style={styles.chatPanelCaption}>
                {activeMessages.length ? `已载入 ${activeMessages.length} 条消息` : '还没有内容，先问点什么吧'}
              </Text>
            </View>
            <View style={styles.chatStatusPill}>
              <View style={[styles.chatStatusDot, sending && styles.chatStatusDotBusy]} />
              <Text style={styles.chatStatusText}>{sending ? '思考中' : '在线'}</Text>
            </View>
          </View>

          <View style={styles.chatArea}>
          {initializing && !activeMessages.length ? (
            <View style={styles.emptyState}>
              <ActivityIndicator color="#172033" size="small" />
              <Text style={styles.tip}>正在恢复最近会话...</Text>
            </View>
          ) : null}

          {!initializing && !activeMessages.length ? (
            <View style={styles.welcomeCard}>
              <Text style={styles.welcomeEyebrow}>Starter Prompts</Text>
              <Text style={styles.welcomeTitle}>让 Bobo 按你的口味推荐</Text>
              <Text style={styles.welcomeText}>试试问它今天该喝什么、预算内推荐，或者让它记住你的甜度与冰量偏好。</Text>
            </View>
          ) : null}

          {activeMessages.map((message, idx) => (
            <View
              key={`${activeThreadId ?? 'thread'}-${idx}-${message.role}`}
              style={[styles.messageRow, message.role === 'user' ? styles.messageRowUser : styles.messageRowAi]}
            >
              <View style={styles.avatarDotWrap}>
                <View style={[styles.avatarDot, message.role === 'user' ? styles.avatarDotUser : styles.avatarDotAi]}>
                  <Ionicons
                    name={message.role === 'user' ? 'person' : 'sparkles'}
                    size={12}
                    color={message.role === 'user' ? '#172033' : '#FFFFFF'}
                  />
                </View>
              </View>
              <View style={[styles.bubble, message.role === 'user' ? styles.userBubble : styles.aiBubble]}>
                <Text style={message.role === 'user' ? styles.userText : styles.aiText}>{message.content}</Text>
                {message.role === 'assistant' && message.menuResults?.length ? (
                  <View style={styles.menuResults}>
                    {message.menuResults.map((item) => (
                      <View key={item.id} style={styles.menuCard}>
                        <View style={styles.menuCardTop}>
                          <Text style={styles.menuName}>
                            {item.brand} {item.name}
                          </Text>
                          {typeof item.price === 'number' ? (
                            <Text style={styles.menuPrice}>¥{Number(item.price).toFixed(0)}</Text>
                          ) : null}
                        </View>
                        <Text style={styles.menuMeta}>{item.size || '常规杯'}</Text>
                        {item.description ? <Text style={styles.menuDescription}>{item.description}</Text> : null}
                      </View>
                    ))}
                  </View>
                ) : null}
              </View>
            </View>
          ))}

          {sending ? (
            <View style={[styles.messageRow, styles.messageRowAi]}>
              <View style={styles.avatarDotWrap}>
                <View style={[styles.avatarDot, styles.avatarDotAi]}>
                  <Ionicons name="sparkles" size={12} color="#FFFFFF" />
                </View>
              </View>
              <View style={[styles.bubble, styles.aiBubble, styles.thinkingBubble]}>
                <ActivityIndicator color="#172033" size="small" />
                <Text style={styles.aiText}>Bobo 正在整理回答…</Text>
              </View>
            </View>
          ) : null}
          </View>
        </View>

        <View style={styles.inputBar}>
          <View style={styles.inputShell}>
            <TextInput
              value={input}
              onChangeText={setInput}
              placeholder="问问 Bobo 今天喝什么..."
              placeholderTextColor="#9CA3AF"
              style={styles.input}
              editable={!sending}
              onSubmitEditing={send}
            />
            <Text style={styles.inputHint}>支持偏好、预算、品牌、记忆相关提问</Text>
          </View>
          <AppButton
            label="Send"
            onPress={send}
            disabled={!input.trim() || sending}
            loading={sending}
            style={[styles.sendBtn, (!input.trim() || sending) && styles.sendBtnDisabled]}
          />
        </View>
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#FFF8FA',
  },
  pageScroll: {
    flex: 1,
  },
  pageContent: {
    paddingHorizontal: 16,
  },
  backgroundOrbOne: {
    position: 'absolute',
    top: 76,
    right: -36,
    width: 150,
    height: 150,
    borderRadius: 999,
    backgroundColor: 'rgba(255, 183, 197, 0.34)',
  },
  backgroundOrbTwo: {
    position: 'absolute',
    top: 260,
    left: -54,
    width: 132,
    height: 132,
    borderRadius: 999,
    backgroundColor: 'rgba(214, 255, 114, 0.22)',
  },
  heroCard: {
    borderRadius: 28,
    padding: 18,
    marginBottom: 14,
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.75)',
    shadowColor: '#FFB7C5',
    shadowOffset: { width: 0, height: 14 },
    shadowOpacity: 0.22,
    shadowRadius: 30,
    elevation: 9,
  },
  heroTopRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    gap: 12,
    marginBottom: 14,
  },
  heroTextBlock: {
    flex: 1,
  },
  eyebrow: {
    fontSize: 11,
    fontWeight: '800',
    color: 'rgba(23, 32, 51, 0.64)',
    letterSpacing: 1.1,
    textTransform: 'uppercase',
    marginBottom: 6,
  },
  title: {
    fontSize: 32,
    fontWeight: '800',
    color: '#172033',
    marginBottom: 6,
  },
  subtitle: {
    fontSize: 13,
    color: 'rgba(23, 32, 51, 0.74)',
    lineHeight: 19,
  },
  heroIconBadge: {
    width: 38,
    height: 38,
    borderRadius: 19,
    backgroundColor: 'rgba(255,255,255,0.62)',
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.78)',
  },
  metricRow: {
    flexDirection: 'row',
    gap: 10,
    marginBottom: 14,
  },
  metricCard: {
    flex: 1,
    borderRadius: 18,
    paddingHorizontal: 12,
    paddingVertical: 12,
    backgroundColor: 'rgba(255,255,255,0.58)',
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.74)',
  },
  metricLabel: {
    fontSize: 11,
    color: 'rgba(23, 32, 51, 0.58)',
    fontWeight: '700',
    marginBottom: 8,
  },
  metricValue: {
    fontSize: 24,
    fontWeight: '800',
    color: '#172033',
  },
  metricValueSmall: {
    fontSize: 18,
    fontWeight: '800',
    color: '#172033',
  },
  metricMeta: {
    marginTop: 2,
    fontSize: 11,
    color: 'rgba(23, 32, 51, 0.52)',
  },
  headerActions: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
  },
  headerChipStrong: {
    borderRadius: 999,
    paddingHorizontal: 14,
    paddingVertical: 10,
    backgroundColor: '#D6FF72',
    borderWidth: 1,
    borderColor: 'rgba(154, 230, 0, 0.72)',
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
  },
  headerChipStrongText: {
    fontSize: 12,
    fontWeight: '800',
    color: '#172033',
  },
  headerChipSoft: {
    borderRadius: 999,
    paddingHorizontal: 14,
    paddingVertical: 10,
    backgroundColor: 'rgba(255,255,255,0.62)',
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.72)',
  },
  headerChipSoftText: {
    fontSize: 12,
    fontWeight: '700',
    color: '#172033',
  },
  sectionBlock: {
    marginBottom: 12,
  },
  threadStrip: {
    gap: 10,
    paddingBottom: 2,
    paddingRight: 12,
  },
  threadCard: {
    minWidth: 140,
    maxWidth: 188,
    borderRadius: 20,
    paddingHorizontal: 14,
    paddingVertical: 14,
    backgroundColor: 'rgba(255,255,255,0.78)',
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.92)',
    shadowColor: '#EAB7C5',
    shadowOffset: { width: 0, height: 8 },
    shadowOpacity: 0.1,
    shadowRadius: 20,
    elevation: 4,
  },
  threadCardActive: {
    backgroundColor: 'transparent',
    borderColor: 'transparent',
    paddingHorizontal: 0,
    paddingVertical: 0,
  },
  threadCardGradient: {
    minWidth: 140,
    maxWidth: 188,
    borderRadius: 20,
    paddingHorizontal: 14,
    paddingVertical: 14,
  },
  threadCardTitle: {
    fontSize: 13,
    fontWeight: '800',
    color: '#172033',
  },
  threadCardTitleActive: {
    color: '#FFFFFF',
  },
  threadCardMeta: {
    marginTop: 6,
    fontSize: 11,
    color: '#6B7280',
  },
  threadCardMetaActive: {
    color: 'rgba(255,255,255,0.78)',
  },
  threadCardTimestamp: {
    marginTop: 18,
    fontSize: 10,
    color: 'rgba(107, 114, 128, 0.85)',
  },
  memoryPanel: {
    borderRadius: 24,
    padding: 14,
    backgroundColor: 'rgba(255,255,255,0.74)',
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.9)',
    marginBottom: 10,
    shadowColor: '#F4C2CF',
    shadowOffset: { width: 0, height: 8 },
    shadowOpacity: 0.08,
    shadowRadius: 18,
    elevation: 3,
  },
  memoryPanelExpanded: {
    paddingBottom: 16,
  },
  sectionHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
    gap: 10,
    marginBottom: 12,
  },
  sectionTitle: {
    fontSize: 16,
    fontWeight: '800',
    color: '#172033',
  },
  sectionCaption: {
    marginTop: 3,
    fontSize: 12,
    color: '#6B7280',
  },
  sectionActions: {
    flexDirection: 'row',
    gap: 8,
  },
  inlineChip: {
    borderRadius: 999,
    paddingHorizontal: 10,
    paddingVertical: 7,
    backgroundColor: 'rgba(246,247,251,0.95)',
  },
  inlineChipText: {
    fontSize: 12,
    fontWeight: '700',
    color: '#172033',
  },
  inlineChipAccent: {
    borderRadius: 999,
    paddingHorizontal: 12,
    paddingVertical: 9,
    backgroundColor: '#EEF9C7',
    borderWidth: 1,
    borderColor: 'rgba(173,255,47,0.48)',
  },
  inlineChipAccentText: {
    fontSize: 12,
    fontWeight: '800',
    color: '#172033',
  },
  inlineDangerChip: {
    backgroundColor: '#FEE2E2',
  },
  inlineDangerChipText: {
    color: '#991B1B',
  },
  profileChipWrap: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
  },
  quickActions: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
    marginTop: 14,
    marginBottom: 10,
  },
  profileChip: {
    borderRadius: 14,
    paddingHorizontal: 12,
    paddingVertical: 10,
    backgroundColor: '#FFFDF7',
    borderWidth: 1,
    borderColor: 'rgba(255, 210, 159, 0.44)',
  },
  profileChipText: {
    fontSize: 13,
    lineHeight: 18,
    color: '#172033',
  },
  memoryList: {
    gap: 10,
    marginTop: 4,
  },
  memoryCard: {
    borderRadius: 18,
    padding: 12,
    backgroundColor: 'rgba(255,255,255,0.82)',
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.94)',
  },
  memoryCardTop: {
    flexDirection: 'row',
    gap: 10,
    alignItems: 'flex-start',
    justifyContent: 'space-between',
  },
  memoryContent: {
    flex: 1,
    fontSize: 13,
    lineHeight: 19,
    color: '#172033',
  },
  memoryBadge: {
    fontSize: 11,
    fontWeight: '800',
    color: '#AD5E00',
    backgroundColor: '#FFF4D4',
    borderRadius: 999,
    paddingHorizontal: 8,
    paddingVertical: 4,
    overflow: 'hidden',
  },
  memoryMeta: {
    marginTop: 8,
    fontSize: 12,
    color: '#6B7280',
  },
  memoryActions: {
    flexDirection: 'row',
    gap: 8,
    marginTop: 10,
  },
  warnText: {
    fontSize: 12,
    color: '#B45309',
    marginBottom: 8,
    paddingHorizontal: 4,
  },
  chatPanel: {
    borderRadius: 28,
    backgroundColor: 'rgba(255,255,255,0.78)',
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.92)',
    overflow: 'hidden',
    shadowColor: '#D8DEE9',
    shadowOffset: { width: 0, height: 14 },
    shadowOpacity: 0.12,
    shadowRadius: 26,
    elevation: 6,
    marginBottom: 12,
  },
  chatPanelHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingHorizontal: 16,
    paddingTop: 16,
    paddingBottom: 10,
  },
  chatPanelTitle: {
    fontSize: 16,
    fontWeight: '800',
    color: '#172033',
  },
  chatPanelCaption: {
    marginTop: 4,
    fontSize: 12,
    color: '#6B7280',
  },
  chatStatusPill: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    borderRadius: 999,
    paddingHorizontal: 10,
    paddingVertical: 7,
    backgroundColor: '#F8FAFC',
    borderWidth: 1,
    borderColor: '#ECF1F5',
  },
  chatStatusDot: {
    width: 8,
    height: 8,
    borderRadius: 999,
    backgroundColor: '#58C27D',
  },
  chatStatusDotBusy: {
    backgroundColor: '#F59E0B',
  },
  chatStatusText: {
    fontSize: 11,
    fontWeight: '700',
    color: '#172033',
  },
  chatArea: {
    paddingHorizontal: 14,
    paddingBottom: 18,
  },
  emptyState: {
    paddingVertical: 16,
    gap: 8,
    alignItems: 'center',
  },
  welcomeCard: {
    borderRadius: 22,
    padding: 16,
    marginTop: 4,
    marginBottom: 14,
    backgroundColor: '#FFFDF7',
    borderWidth: 1,
    borderColor: 'rgba(255, 214, 114, 0.34)',
  },
  welcomeEyebrow: {
    fontSize: 11,
    fontWeight: '800',
    color: '#AD5E00',
    letterSpacing: 0.9,
    textTransform: 'uppercase',
    marginBottom: 8,
  },
  welcomeTitle: {
    fontSize: 18,
    fontWeight: '800',
    color: '#172033',
    marginBottom: 8,
  },
  welcomeText: {
    fontSize: 13,
    lineHeight: 20,
    color: '#4B5563',
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
  messageRowAi: {
    justifyContent: 'flex-start',
  },
  avatarDotWrap: {
    width: 28,
    alignItems: 'center',
    paddingTop: 4,
  },
  avatarDot: {
    width: 26,
    height: 26,
    borderRadius: 13,
    alignItems: 'center',
    justifyContent: 'center',
  },
  avatarDotUser: {
    backgroundColor: '#D6FF72',
  },
  avatarDotAi: {
    backgroundColor: '#172033',
  },
  bubble: {
    borderRadius: 20,
    paddingHorizontal: 14,
    paddingVertical: 12,
    maxWidth: '86%',
  },
  userBubble: {
    backgroundColor: '#172033',
    borderTopRightRadius: 8,
  },
  aiBubble: {
    backgroundColor: '#F3F6FA',
    borderTopLeftRadius: 8,
    borderWidth: 1,
    borderColor: '#E9EEF5',
  },
  thinkingBubble: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  menuResults: {
    marginTop: 10,
    gap: 8,
  },
  menuCard: {
    borderRadius: 14,
    padding: 10,
    backgroundColor: '#FFFFFF',
    borderWidth: 1,
    borderColor: '#EDF0F5',
  },
  menuCardTop: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    gap: 8,
    alignItems: 'center',
  },
  menuName: {
    flex: 1,
    fontSize: 14,
    fontWeight: '700',
    color: '#172033',
  },
  menuPrice: {
    fontSize: 13,
    fontWeight: '700',
    color: '#B45309',
  },
  menuMeta: {
    marginTop: 4,
    fontSize: 12,
    color: '#6B7280',
  },
  menuDescription: {
    marginTop: 6,
    fontSize: 12,
    lineHeight: 18,
    color: '#374151',
  },
  userText: {
    color: '#FFFFFF',
    fontSize: 14,
    lineHeight: 20,
  },
  aiText: {
    color: '#172033',
    fontSize: 14,
    lineHeight: 21,
  },
  inputBar: {
    flexDirection: 'row',
    alignItems: 'flex-end',
    gap: 10,
    marginTop: 2,
  },
  inputShell: {
    flex: 1,
    borderRadius: 22,
    backgroundColor: 'rgba(255,255,255,0.94)',
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.98)',
    paddingHorizontal: 14,
    paddingTop: 12,
    paddingBottom: 10,
    shadowColor: '#E6E9F0',
    shadowOffset: { width: 0, height: 10 },
    shadowOpacity: 0.12,
    shadowRadius: 20,
    elevation: 6,
  },
  input: {
    minHeight: 24,
    fontSize: 14,
    color: '#172033',
    paddingVertical: 0,
  },
  inputHint: {
    marginTop: 8,
    fontSize: 11,
    color: '#9CA3AF',
  },
  sendBtn: {
    minWidth: 92,
    marginBottom: 2,
  },
  sendBtnDisabled: {
    opacity: 0.4,
  },
  tip: {
    fontSize: 14,
    color: '#6B7280',
  },
  loginBtn: {
    marginTop: 10,
  },
  errorText: {
    marginTop: 10,
    color: '#B91C1C',
    fontSize: 13,
  },
});
