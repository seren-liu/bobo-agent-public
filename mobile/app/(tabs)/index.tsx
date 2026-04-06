import { useRef, useMemo, useState } from 'react';
import {
  View,
  Text,
  ScrollView,
  Pressable,
  StyleSheet,
  ActivityIndicator,
  Alert,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { LinearGradient } from 'expo-linear-gradient';
import * as ImagePicker from 'expo-image-picker';
import { useQuery } from '@tanstack/react-query';
import { Ionicons } from '@expo/vector-icons';
import { useQueryClient } from '@tanstack/react-query';
import { type Href, useRouter } from 'expo-router';

import { boboApi } from '@/lib/api';
import { mapPickerAssetToUploadable, uploadImageAsset } from '@/lib/uploads';
import { getLocalDayStamp } from '@/lib/dateTime';
import { useAuthStore } from '@/stores/authStore';
import { RecentDrinkCard } from '@/components/RecentDrinkCard';
import {
  RecognitionConfirmSheet,
  type RecognitionConfirmSheetRef,
} from '@/components/RecognitionConfirmSheet';
import { RecognitionProcessingOverlay } from '@/components/RecognitionProcessingOverlay';

// ─── Week strip helpers ───────────────────────────────────────────────────────

function formatDateKey(date: Date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function getWeekDays() {
  const today = new Date();
  const dayOfWeek = today.getDay(); // 0=Sun
  // Start from Monday
  const monday = new Date(today);
  monday.setDate(today.getDate() - ((dayOfWeek + 6) % 7));

  return Array.from({ length: 7 }, (_, i) => {
    const d = new Date(monday);
    d.setDate(monday.getDate() + i);
    const isToday =
      d.getDate() === today.getDate() &&
      d.getMonth() === today.getMonth() &&
      d.getFullYear() === today.getFullYear();
    return {
      day: ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'][i]!,
      date: d.getDate(),
      dateString: formatDateKey(d),
      isFuture: d > today,
      isToday,
    };
  });
}

function greetingFor(name = 'Seren') {
  const h = new Date().getHours();
  const period =
    h < 12 ? 'Good morning' : h < 18 ? 'Good afternoon' : 'Good evening';
  return `${period}, ${name}`;
}

function todayLabel() {
  return new Date().toLocaleDateString('en-US', {
    weekday: 'long',
    month: 'short',
    day: 'numeric',
  });
}

// ─── Quick action items ───────────────────────────────────────────────────────

// ─── Screen ───────────────────────────────────────────────────────────────────

export default function HomeScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const recognitionRef = useRef<RecognitionConfirmSheetRef>(null);
  const queryClient = useQueryClient();
  const logout = useAuthStore((s) => s.logout);
  const userId = useAuthStore((s) => s.userId);
  const nickname = useAuthStore((s) => s.nickname);
  const [recognitionState, setRecognitionState] = useState<{
    visible: boolean;
    sourceType: 'photo' | 'screenshot';
    stage: 'compressing' | 'uploading' | 'recognizing';
    previewUri?: string | null;
  }>({
    visible: false,
    sourceType: 'photo',
    stage: 'compressing',
    previewUri: null,
  });

  const todayStr = useMemo(() => getLocalDayStamp(), []);
  const weekDays = useMemo(getWeekDays, []);

  const todayQuery = useQuery({
    queryKey: ['records', 'day', todayStr],
    queryFn: () => boboApi.getDayRecords(todayStr).then((r) => r.data),
  });

  const recentQuery = useQuery({
    queryKey: ['records', 'recent', 5],
    queryFn: () => boboApi.getRecentRecords(5).then((r) => r.data),
  });

  const todayRecords = todayQuery.data?.records ?? [];
  const total = Number(todayQuery.data?.total ?? 0);
  const drinkCount = todayRecords.length;
  const recentRecords = recentQuery.data?.records ?? [];

  // ── Handlers ──
  const handlePickedAsset = async (
    asset: ImagePicker.ImagePickerAsset,
    sourceType: 'photo' | 'screenshot'
  ) => {
    try {
      setRecognitionState({
        visible: true,
        sourceType,
        stage: 'compressing',
        previewUri: asset.uri,
      });
      const fileUrl = await uploadImageAsset(mapPickerAssetToUploadable(asset), {
        profile: sourceType === 'photo' ? 'recognition-photo' : 'recognition-screenshot',
        sourceType,
        onStageChange: (stage) =>
          setRecognitionState((prev) => ({
            ...prev,
            visible: true,
            sourceType,
            stage,
            previewUri: asset.uri,
          })),
      });
      setRecognitionState((prev) => ({
        ...prev,
        visible: true,
        sourceType,
        stage: 'recognizing',
        previewUri: asset.uri,
      }));
      const recognize = await boboApi.recognize(fileUrl, sourceType);
      if (recognize.data.error) {
        throw new Error(`识别失败：${recognize.data.error}`);
      }
      if (!recognize.data.items?.length) {
        throw new Error('未识别到可用饮品，请尝试更清晰图片');
      }

      setRecognitionState((prev) => ({ ...prev, visible: false }));
      recognitionRef.current?.open({
        sourceType,
        fileUrl,
        orderTime: recognize.data.order_time,
        items: recognize.data.items,
      });

      queryClient.invalidateQueries({ queryKey: ['records', 'day'] });
    } catch (e) {
      setRecognitionState((prev) => ({ ...prev, visible: false }));
      Alert.alert('识别失败', e instanceof Error ? e.message : '请稍后重试');
    }
  };

  const handlePhotoScan = async () => {
    const cameraPerm = await ImagePicker.requestCameraPermissionsAsync();
    if (!cameraPerm.granted) {
      Alert.alert('没有相机权限', '请在系统设置中允许相机访问后再试');
      return;
    }
    const result = await ImagePicker.launchCameraAsync({
      mediaTypes: ['images'],
      quality: 0.85,
    });
    if (!result.canceled && result.assets[0]) {
      await handlePickedAsset(result.assets[0], 'photo');
    }
  };

  const handleScreenshot = async () => {
    const libPerm = await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (!libPerm.granted) {
      Alert.alert('没有相册权限', '请在系统设置中允许相册访问后再试');
      return;
    }
    const pendingResult = await ImagePicker.getPendingResultAsync();
    const firstPending = Array.isArray(pendingResult) ? pendingResult[0] : pendingResult;
    if (
      firstPending &&
      'canceled' in firstPending &&
      !firstPending.canceled &&
      'assets' in firstPending &&
      firstPending.assets?.[0]
    ) {
      await handlePickedAsset(firstPending.assets[0], 'screenshot');
      return;
    }
    const result = await ImagePicker.launchImageLibraryAsync({
      mediaTypes: ['images'],
      quality: 0.85,
    });
    if (!result.canceled && result.assets[0]) {
      await handlePickedAsset(result.assets[0], 'screenshot');
    }
  };

  return (
    <View style={{ flex: 1, backgroundColor: '#FAFAFA' }}>
      <ScrollView
        contentContainerStyle={[
          styles.scroll,
          { paddingTop: insets.top + 12, paddingBottom: 120 },
        ]}
        showsVerticalScrollIndicator={false}
      >
        {/* ── Header ── */}
        <View style={styles.header}>
          <Pressable
            style={styles.headerLeft}
            onPress={() => {
              Alert.alert('Log Out', 'Do you want to log out?', [
                { text: 'Cancel', style: 'cancel' },
                { text: 'Log Out', style: 'destructive', onPress: logout },
              ]);
            }}
          >
            <LinearGradient
              colors={['#FFB7C5', '#FFA5B4']}
              style={styles.avatar}
            >
              <Text style={styles.avatarText}>
                {nickname ? nickname.charAt(0).toUpperCase() : 'U'}
              </Text>
            </LinearGradient>
            <View>
              <Text style={styles.greeting}>{greetingFor(nickname || undefined)}</Text>
              <Text style={styles.dateLabel}>{todayLabel()}</Text>
            </View>
          </Pressable>
          <Pressable style={styles.bellBtn}>
            <Ionicons name="notifications-outline" size={20} color="#8E8E93" />
          </Pressable>
        </View>

        {/* ── Hero Banner ── */}
        <LinearGradient
          colors={['#FFB7C5', '#FFA5B4', '#FFA5B4']}
          start={{ x: 0, y: 0 }}
          end={{ x: 1, y: 1 }}
          style={styles.hero}
        >
          <View style={styles.heroContent}>
            <View style={styles.aiBadge}>
              <Text style={styles.aiBadgeText}>AI Suggestion</Text>
            </View>
            <Text style={styles.heroTitle}>Today's Pick</Text>
            <Text style={styles.heroSub}>
              {drinkCount > 0
                ? `${drinkCount} drink${drinkCount > 1 ? 's' : ''} logged today · ¥${total.toFixed(0)}`
                : 'No drinks logged yet today'}
            </Text>
          </View>
          <Text style={styles.heroEmoji}>🧋</Text>
        </LinearGradient>

        {/* ── Weekly Date Strip ── */}
        <View style={styles.weekCard}>
          <View style={styles.weekRow}>
            {weekDays.map((item) => (
              <Pressable
                key={item.dateString}
                style={[styles.dayItem, item.isToday && styles.dayItemToday]}
                disabled={item.isFuture}
                onPress={() => {
                  if (item.isFuture) return;
                  router.push({
                    pathname: '/day/[date]',
                    params: { date: item.dateString },
                  } as Href);
                }}
              >
                <Text
                  style={[styles.dayName, item.isToday && styles.dayNameToday]}
                >
                  {item.day}
                </Text>
                <Text
                  style={[
                    styles.dayDate,
                    item.isToday && styles.dayDateToday,
                  ]}
                >
                  {item.date}
                </Text>
              </Pressable>
            ))}
          </View>
        </View>

        {/* ── Recent Records ── */}
        <View style={styles.section}>
          <View style={styles.sectionHeader}>
            <Text style={styles.sectionTitle}>Recent Drinks</Text>
            <Text style={styles.sectionMeta}>Latest 5 logs</Text>
          </View>

          {recentQuery.isLoading && (
            <ActivityIndicator
              color="#ADFF2F"
              size="small"
              style={{ marginVertical: 20 }}
            />
          )}

          {recentQuery.isError && (
            <View style={styles.emptyState}>
              <Text style={styles.emptyText}>Failed to load recent drinks</Text>
            </View>
          )}

          {!recentQuery.isLoading && !recentQuery.isError && recentRecords.length === 0 && (
            <View style={styles.emptyState}>
              <Text style={styles.emptyEmoji}>🫙</Text>
              <Text style={styles.emptyText}>No recent drinks yet</Text>
              <Text style={styles.emptyHint}>Your last five logs will show up here</Text>
            </View>
          )}

          {recentRecords.length > 0 && (
            <ScrollView
              horizontal
              showsHorizontalScrollIndicator={false}
              contentContainerStyle={styles.recentScroll}
            >
              {recentRecords.map((record, idx) => (
                <RecentDrinkCard
                  key={record.id}
                  record={record}
                  index={idx}
                  onPress={() =>
                    router.push({
                      pathname: '/day/[date]',
                      params: { date: record.consumed_at.slice(0, 10) },
                    } as Href)
                  }
                />
              ))}
            </ScrollView>
          )}
        </View>

        {/* ── Quick Add ── */}
        <View style={styles.section}>
          <Text style={styles.quickSectionTitle}>Quick Add</Text>
          <View style={styles.quickGrid}>
            <QuickActionCard
              icon="camera-outline"
              label="Photo Scan"
              hint="Snap a drink order"
              accentColor="#7BAF16"
              accentSoft="#EAF5CB"
              onPress={handlePhotoScan}
            />
            <QuickActionCard
              icon="cut-outline"
              label="Screenshot"
              hint="Import from album"
              accentColor="#4F9A7A"
              accentSoft="#DDF3E9"
              onPress={handleScreenshot}
            />
            <QuickActionCard
              icon="create-outline"
              label="Manual Add"
              hint="Log one by hand"
              accentColor="#D48652"
              accentSoft="#FFE7D8"
              onPress={() => router.push('/manual-add' as Href)}
            />
          </View>
        </View>
      </ScrollView>
      <RecognitionProcessingOverlay
        visible={recognitionState.visible}
        sourceType={recognitionState.sourceType}
        stage={recognitionState.stage}
        previewUri={recognitionState.previewUri}
      />
      <RecognitionConfirmSheet ref={recognitionRef} />
    </View>
  );
}

// ─── Quick action card ────────────────────────────────────────────────────────

function QuickActionCard({
  icon,
  label,
  hint,
  accentColor,
  accentSoft,
  onPress,
}: {
  icon: keyof typeof Ionicons.glyphMap;
  label: string;
  hint: string;
  accentColor: string;
  accentSoft: string;
  onPress: () => void;
}) {
  return (
    <Pressable
      onPress={onPress}
      style={({ pressed }) => [
        styles.quickCard,
        {
          backgroundColor: accentSoft,
          borderColor: `${accentColor}24`,
          shadowColor: accentColor,
        },
        pressed && styles.quickCardPressed,
      ]}
    >
      <View style={[styles.quickIconWrap, { backgroundColor: `${accentColor}20` }]}>
        <Ionicons name={icon} size={18} color={accentColor} />
      </View>
      <View style={styles.quickCopy}>
        <Text style={styles.quickLabel}>{label}</Text>
        <Text style={styles.quickHint}>{hint}</Text>
      </View>
    </Pressable>
  );
}

// ─── Styles ───────────────────────────────────────────────────────────────────

const styles = StyleSheet.create({
  scroll: { paddingHorizontal: 20 },

  // Header
  header: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    marginBottom: 20,
  },
  headerLeft: { flexDirection: 'row', alignItems: 'center', gap: 12 },
  avatar: {
    width: 36,
    height: 36,
    borderRadius: 18,
    alignItems: 'center',
    justifyContent: 'center',
    shadowColor: '#FFB7C5',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.4,
    shadowRadius: 8,
    elevation: 4,
  },
  avatarText: { fontSize: 14, fontWeight: '700', color: '#fff' },
  greeting: { fontSize: 16, fontWeight: '600', color: '#1C1C1E' },
  dateLabel: { fontSize: 13, color: '#8E8E93', marginTop: 1 },
  bellBtn: {
    width: 40,
    height: 40,
    borderRadius: 20,
    backgroundColor: 'rgba(255,255,255,0.85)',
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 1,
    borderColor: 'rgba(0,0,0,0.06)',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.06,
    shadowRadius: 8,
    elevation: 2,
  },

  // Hero banner
  hero: {
    borderRadius: 24,
    padding: 20,
    height: 140,
    marginBottom: 20,
    flexDirection: 'row',
    alignItems: 'flex-start',
    justifyContent: 'space-between',
    overflow: 'hidden',
    shadowColor: '#FFB7C5',
    shadowOffset: { width: 0, height: 8 },
    shadowOpacity: 0.35,
    shadowRadius: 20,
    elevation: 8,
  },
  heroContent: { flex: 1 },
  aiBadge: {
    backgroundColor: '#ADFF2F',
    borderRadius: 20,
    paddingHorizontal: 10,
    paddingVertical: 4,
    alignSelf: 'flex-start',
    marginBottom: 8,
    shadowColor: '#ADFF2F',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.4,
    shadowRadius: 6,
    elevation: 4,
  },
  aiBadgeText: { fontSize: 11, fontWeight: '700', color: '#1C1C1E' },
  heroTitle: {
    fontSize: 22,
    fontWeight: '800',
    color: '#fff',
    marginBottom: 4,
    textShadowColor: 'rgba(0,0,0,0.15)',
    textShadowOffset: { width: 0, height: 1 },
    textShadowRadius: 4,
  },
  heroSub: { fontSize: 13, color: 'rgba(255,255,255,0.88)' },
  heroEmoji: {
    fontSize: 52,
    position: 'absolute',
    right: 16,
    bottom: 8,
  },

  // Weekly strip
  weekCard: {
    backgroundColor: 'rgba(255,255,255,0.85)',
    borderRadius: 18,
    padding: 8,
    marginBottom: 24,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.06,
    shadowRadius: 8,
    elevation: 2,
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.9)',
  },
  weekRow: {
    flexDirection: 'row',
    alignItems: 'stretch',
    justifyContent: 'space-between',
    gap: 6,
  },
  dayItem: {
    flex: 1,
    minHeight: 52,
    alignItems: 'center',
    justifyContent: 'center',
    paddingVertical: 6,
    paddingHorizontal: 3,
    borderRadius: 12,
  },
  dayItemToday: { backgroundColor: '#1C1C1E' },
  dayName: {
    fontSize: 9,
    fontWeight: '600',
    color: '#8E8E93',
    marginBottom: 3,
    textAlign: 'center',
  },
  dayNameToday: { color: 'rgba(255,255,255,0.7)' },
  dayDate: {
    fontSize: 14,
    fontWeight: '700',
    color: '#1C1C1E',
    textAlign: 'center',
  },
  dayDateToday: { color: '#fff' },

  // Section
  section: { marginBottom: 24 },
  sectionHeader: {
    marginBottom: 12,
    flexDirection: 'row',
    alignItems: 'baseline',
    justifyContent: 'space-between',
  },
  sectionTitle: {
    fontSize: 18,
    fontWeight: '700',
    color: '#1C1C1E',
  },
  quickSectionTitle: {
    fontSize: 18,
    fontWeight: '700',
    color: '#1C1C1E',
    marginBottom: 16,
  },
  sectionMeta: {
    fontSize: 12,
    fontWeight: '600',
    color: '#8E8E93',
  },
  recentScroll: {
    paddingBottom: 8,
    paddingHorizontal: 2,
    gap: 14,
  },

  // Empty state
  emptyState: {
    alignItems: 'center',
    paddingVertical: 32,
    backgroundColor: 'rgba(255,255,255,0.7)',
    borderRadius: 20,
    borderWidth: 1,
    borderColor: 'rgba(0,0,0,0.04)',
  },
  emptyEmoji: { fontSize: 40, marginBottom: 8 },
  emptyText: { fontSize: 15, color: '#8E8E93', fontWeight: '500' },
  emptyHint: { fontSize: 13, color: '#C7C7CC', marginTop: 4 },

  // Quick add
  quickGrid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    width: '100%',
    justifyContent: 'flex-start',
    columnGap: '4%',
    rowGap: 14,
  },
  quickCard: {
    width: '48%',
    minHeight: 86,
    borderRadius: 24,
    paddingVertical: 14,
    paddingHorizontal: 14,
    flexDirection: 'column',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 12,
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.92)',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 8 },
    shadowOpacity: 0.06,
    shadowRadius: 18,
    elevation: 4,
  },
  quickCardPressed: {
    opacity: 0.88,
    transform: [{ scale: 0.98 }],
  },
  quickIconWrap: {
    width: 42,
    height: 42,
    borderRadius: 15,
    alignItems: 'center',
    justifyContent: 'center',
    alignSelf: 'center',
  },
  quickCopy: {
    alignItems: 'center',
    justifyContent: 'center',
  },
  quickLabel: {
    fontSize: 14,
    lineHeight: 18,
    fontWeight: '700',
    color: '#1C1C1E',
    textAlign: 'center',
  },
  quickHint: {
    marginTop: 3,
    fontSize: 11,
    lineHeight: 15,
    fontWeight: '500',
    color: '#8E8E93',
    textAlign: 'center',
  },
});
