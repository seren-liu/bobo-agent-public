import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';
import AsyncStorage from '@react-native-async-storage/async-storage';
import * as SecureStore from 'expo-secure-store';
import { Platform } from 'react-native';

interface AuthState {
  accessToken: string | null;
  refreshToken: string | null;
  userId: string | null;
  nickname: string | null;
  setSession: (session: {
    accessToken: string;
    refreshToken: string;
    userId?: string | null;
    nickname?: string | null;
  }) => void;
  logout: () => void;
}

const secureStorage = {
  getItem: async (key: string) => {
    const current = await SecureStore.getItemAsync(key);
    if (current) {
      return current;
    }

    const legacy = await AsyncStorage.getItem(key);
    if (!legacy) {
      return null;
    }

    try {
      const parsed = JSON.parse(legacy);
      if (parsed && typeof parsed === 'object') {
        const legacyState = (parsed as { state?: Record<string, unknown> }).state ?? {};
        if ('token' in legacyState && !('accessToken' in legacyState)) {
          const migrated = {
            ...parsed,
            state: {
              accessToken: (legacyState.token as string | null | undefined) ?? null,
              refreshToken: (legacyState.refreshToken as string | null | undefined) ?? null,
              userId: (legacyState.userId as string | null | undefined) ?? null,
            },
          };
          const next = JSON.stringify(migrated);
          await SecureStore.setItemAsync(key, next);
          await AsyncStorage.removeItem(key);
          return next;
        }
      }
    } catch {
      // Fall through to raw migration below.
    }

    await SecureStore.setItemAsync(key, legacy);
    await AsyncStorage.removeItem(key);
    return legacy;
  },
  setItem: async (key: string, value: string) => {
    await SecureStore.setItemAsync(key, value);
    await AsyncStorage.removeItem(key);
  },
  removeItem: async (key: string) => {
    await SecureStore.deleteItemAsync(key);
    await AsyncStorage.removeItem(key);
  },
};

const storage = Platform.OS === 'web' ? AsyncStorage : secureStorage;
const persistTokens = Platform.OS !== 'web';

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      accessToken: null,
      refreshToken: null,
      userId: null,
      nickname: null,
      setSession: (session) => {
        set({
          accessToken: session.accessToken,
          refreshToken: session.refreshToken,
          userId: session.userId ?? null,
          nickname: session.nickname ?? null,
        });
      },
      logout: () => {
        set({ accessToken: null, refreshToken: null, userId: null, nickname: null });
      },
    }),
    {
      name: 'bobo-auth',
      storage: createJSONStorage(() => storage),
      partialize: (state) => ({
        accessToken: persistTokens ? state.accessToken : null,
        refreshToken: persistTokens ? state.refreshToken : null,
        userId: state.userId,
        nickname: state.nickname,
      }),
    }
  )
);
