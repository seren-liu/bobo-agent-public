import { ActivityIndicator, Alert, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { useEffect, useRef, useState } from 'react';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import * as Haptics from 'expo-haptics';

import { DayDetailContent } from '@/components/DayDetailContent';
import { FloatingToast } from '@/components/FloatingToast';
import { boboApi, type DrinkRecord } from '@/lib/api';

export default function DayDetailScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const queryClient = useQueryClient();
  const toastTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const params = useLocalSearchParams<{ date?: string | string[] }>();
  const date = Array.isArray(params.date) ? params.date[0] : params.date;
  const safeDate = date ?? new Date().toISOString().slice(0, 10);
  const [toastMessage, setToastMessage] = useState('');
  const [toastVisible, setToastVisible] = useState(false);

  const dayQuery = useQuery({
    queryKey: ['records', 'day-detail', safeDate],
    queryFn: () => boboApi.getDayRecords(safeDate).then((r) => r.data),
    enabled: !!safeDate,
  });

  const deleteMutation = useMutation({
    mutationFn: async (record: DrinkRecord) => {
      await boboApi.deleteRecord(record.id);
      return record;
    },
    onSuccess: async (record) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['records', 'day'] }),
        queryClient.invalidateQueries({ queryKey: ['records', 'calendar'] }),
        queryClient.invalidateQueries({ queryKey: ['records', 'recent'] }),
        queryClient.invalidateQueries({ queryKey: ['records', 'stats'] }),
        queryClient.invalidateQueries({ queryKey: ['records', 'day-detail'] }),
        queryClient.invalidateQueries({ queryKey: ['records', 'day-detail', safeDate] }),
      ]);
      showToast(`已删除「${record.brand} ${record.name}」`);
      void Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success);
    },
    onError: (error) => {
      Alert.alert('删除失败', error instanceof Error ? error.message : '请稍后再试');
      void Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error);
    },
  });

  const openManualAdd = () => {
    router.push({
      pathname: '/manual-add',
      params: { consumedAt: `${safeDate}T12:00:00Z` },
    });
  };

  const openDeleteSheet = (record: DrinkRecord) => {
    void Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light);

    Alert.alert(
      '确认删除这条记录？',
      `${record.brand} ${record.name}\n删除后会从当天时间线和统计中移除。`,
      [
        {
          text: '取消',
          style: 'cancel',
        },
        {
          text: '确认删除',
          style: 'destructive',
          onPress: () => deleteMutation.mutate(record),
        },
      ]
    );
  };

  const showToast = (message: string) => {
    setToastMessage(message);
    setToastVisible(true);

    if (toastTimerRef.current) {
      clearTimeout(toastTimerRef.current);
    }

    toastTimerRef.current = setTimeout(() => {
      setToastVisible(false);
    }, 2200);
  };

  useEffect(() => {
    return () => {
      if (toastTimerRef.current) {
        clearTimeout(toastTimerRef.current);
      }
    };
  }, []);

  return (
    <View style={styles.page}>
      <View style={[styles.backgroundOrb, styles.backgroundOrbTop]} />
      <View style={[styles.backgroundOrb, styles.backgroundOrbBottom]} />

      <ScrollView
        contentContainerStyle={[
          styles.scroll,
          {
            paddingTop: insets.top + 10,
            paddingBottom: Math.max(insets.bottom, 28) + 28,
          },
        ]}
        showsVerticalScrollIndicator={false}
      >
        <View style={styles.header}>
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="返回日历"
            onPress={() => router.back()}
            style={({ pressed }) => [styles.iconButton, pressed && styles.iconButtonPressed]}
          >
            <Ionicons name="chevron-back" size={22} color="#1C1C1E" />
          </Pressable>

          <View style={styles.headerCopy}>
            <Text style={styles.headerEyebrow}>Calendar Detail</Text>
            <Text style={styles.headerTitle}>日期详情</Text>
          </View>

          <Pressable
            accessibilityRole="button"
            accessibilityLabel="新增饮品"
            onPress={openManualAdd}
            style={({ pressed }) => [styles.iconButton, pressed && styles.iconButtonPressed]}
          >
            <Ionicons name="add" size={22} color="#1C1C1E" />
          </Pressable>
        </View>

        {dayQuery.isLoading ? (
          <View style={styles.stateBlock}>
            <ActivityIndicator size="large" color="#ADFF2F" />
            <Text style={styles.stateText}>正在加载这一天的饮品记录...</Text>
          </View>
        ) : dayQuery.isError ? (
          <View style={styles.stateCard}>
            <Text style={styles.stateTitle}>加载失败</Text>
            <Text style={styles.stateText}>网络或服务暂时不可用，请稍后再试。</Text>
            <Pressable
              accessibilityRole="button"
              onPress={() => dayQuery.refetch()}
              style={({ pressed }) => [styles.retryButton, pressed && styles.retryButtonPressed]}
            >
              <Text style={styles.retryButtonText}>重新加载</Text>
            </Pressable>
          </View>
        ) : (
          <DayDetailContent
            date={safeDate}
            detail={dayQuery.data}
            onAddPress={openManualAdd}
            onRecordLongPress={openDeleteSheet}
          />
        )}
      </ScrollView>

      <FloatingToast
        visible={toastVisible}
        message={toastMessage}
        bottom={Math.max(insets.bottom, 20) + 12}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  page: {
    flex: 1,
    backgroundColor: '#FAF7F2',
  },
  scroll: {
    paddingHorizontal: 18,
  },
  backgroundOrb: {
    position: 'absolute',
    borderRadius: 999,
  },
  backgroundOrbTop: {
    width: 240,
    height: 240,
    top: -70,
    right: -40,
    backgroundColor: 'rgba(255,183,197,0.20)',
  },
  backgroundOrbBottom: {
    width: 220,
    height: 220,
    bottom: 80,
    left: -90,
    backgroundColor: 'rgba(173,255,47,0.12)',
  },
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    marginBottom: 18,
  },
  headerCopy: {
    flex: 1,
  },
  headerEyebrow: {
    fontSize: 11,
    fontWeight: '700',
    letterSpacing: 1.1,
    color: '#A07152',
    textTransform: 'uppercase',
  },
  headerTitle: {
    marginTop: 4,
    fontSize: 28,
    fontWeight: '800',
    color: '#111827',
  },
  iconButton: {
    width: 44,
    height: 44,
    borderRadius: 22,
    backgroundColor: 'rgba(255,255,255,0.86)',
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.95)',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 6 },
    shadowOpacity: 0.08,
    shadowRadius: 16,
    elevation: 4,
  },
  iconButtonPressed: {
    opacity: 0.88,
    transform: [{ scale: 0.97 }],
  },
  stateBlock: {
    minHeight: 300,
    alignItems: 'center',
    justifyContent: 'center',
    gap: 12,
  },
  stateCard: {
    minHeight: 280,
    borderRadius: 28,
    backgroundColor: '#FFFFFF',
    borderWidth: 1,
    borderColor: '#F0E7E2',
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 24,
  },
  stateTitle: {
    fontSize: 22,
    fontWeight: '800',
    color: '#172033',
  },
  stateText: {
    marginTop: 8,
    fontSize: 13,
    lineHeight: 20,
    color: '#6B7280',
    textAlign: 'center',
  },
  retryButton: {
    marginTop: 18,
    borderRadius: 16,
    backgroundColor: '#172033',
    paddingHorizontal: 18,
    paddingVertical: 12,
  },
  retryButtonPressed: {
    opacity: 0.88,
  },
  retryButtonText: {
    color: '#FFFFFF',
    fontSize: 14,
    fontWeight: '800',
  },
});
