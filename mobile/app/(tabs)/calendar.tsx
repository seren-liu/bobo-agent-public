import { useMemo, useState } from 'react';
import { View, Text, StyleSheet, ScrollView, Pressable } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Calendar } from 'react-native-calendars';
import { useQuery } from '@tanstack/react-query';
import { useRouter } from 'expo-router';
import { LinearGradient } from 'expo-linear-gradient';
import { Ionicons } from '@expo/vector-icons';

import { boboApi } from '@/lib/api';
import { useAuthStore } from '@/stores/authStore';
import { AppButton } from '@/components/AppButton';

const DEV_LOGIN_ENABLED = process.env.EXPO_PUBLIC_ENABLE_DEV_LOGIN === 'true';

function monthTitle(date: Date) {
  return new Intl.DateTimeFormat('en-US', {
    month: 'long',
    year: 'numeric',
  }).format(date);
}

export default function CalendarScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const accessToken = useAuthStore((s) => s.accessToken);
  const setSession = useAuthStore((s) => s.setSession);
  const [selectedDate, setSelectedDate] = useState(new Date().toISOString().slice(0, 10));
  const [monthAnchor, setMonthAnchor] = useState(new Date());
  const [loginPending, setLoginPending] = useState(false);
  const [loginError, setLoginError] = useState<string | null>(null);

  const year = monthAnchor.getFullYear();
  const month = monthAnchor.getMonth() + 1;

  const calendarQuery = useQuery({
    queryKey: ['records', 'calendar', year, month, !!accessToken],
    queryFn: () => boboApi.getCalendar(year, month).then((r) => r.data),
    enabled: !!accessToken,
  });

  const markedDates = useMemo(() => {
    const result: Record<string, any> = {};
    const map = calendarQuery.data ?? {};
    for (const [date, dots] of Object.entries(map)) {
      result[date] = {
        dots: (dots ?? []).map((d) => ({ key: d.brand, color: d.color ?? '#84CC16' })),
      };
    }
    result[selectedDate] = {
      ...(result[selectedDate] ?? {}),
      selected: true,
      selectedColor: '#172033',
      selectedTextColor: '#FFFFFF',
    };
    return result;
  }, [calendarQuery.data, selectedDate]);

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

  if (!accessToken) {
    return (
      <View style={[styles.loginRoot, { paddingTop: insets.top + 24 }]}>
        <Text style={styles.title}>Calendar</Text>
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
    <View style={styles.root}>
      <View style={styles.backgroundOrbOne} />
      <View style={styles.backgroundOrbTwo} />

      <ScrollView
        contentContainerStyle={[styles.scrollContent, { paddingTop: insets.top + 12, paddingBottom: insets.bottom + 112 }]}
        showsVerticalScrollIndicator={false}
      >
        <LinearGradient
          colors={['#FFD6E1', '#FFC0CF', '#FFF2BB']}
          start={{ x: 0, y: 0 }}
          end={{ x: 1, y: 1 }}
          style={styles.heroCard}
        >
          <View style={styles.heroTopRow}>
            <View>
              <Text style={styles.eyebrow}>Bobo Timeline</Text>
              <Text style={styles.title}>Calendar</Text>
              <Text style={styles.subtitle}>把你的奶茶记录排成一张轻盈、好扫的时间地图。</Text>
            </View>
            <View style={styles.heroBadge}>
              <Ionicons name="calendar" size={18} color="#172033" />
            </View>
          </View>
        </LinearGradient>

        <View style={styles.calendarStage}>
          <View style={styles.calendarCard}>
            <View style={styles.calendarHeader}>
              <View>
                <Text style={styles.calendarTitle}>{monthTitle(monthAnchor)}</Text>
                <Text style={styles.calendarSub}>点击查看当天，长按可快速新增记录</Text>
              </View>
              <Pressable
                style={styles.todayChip}
                onPress={() => {
                  const today = new Date();
                  const todayStr = today.toISOString().slice(0, 10);
                  setMonthAnchor(today);
                  setSelectedDate(todayStr);
                }}
              >
                <Text style={styles.todayChipText}>Today</Text>
              </Pressable>
            </View>

            <Calendar
              current={selectedDate}
              markedDates={markedDates}
              markingType="multi-dot"
              enableSwipeMonths
              hideExtraDays={false}
              firstDay={1}
              onDayPress={(d) => {
                setSelectedDate(d.dateString);
                router.push({
                  pathname: '/day/[date]',
                  params: { date: d.dateString },
                } as any);
              }}
              onDayLongPress={(d) => {
                setSelectedDate(d.dateString);
                router.push({
                  pathname: '/manual-add',
                  params: { consumedAt: `${d.dateString}T12:00:00Z` },
                } as any);
              }}
              onMonthChange={(d) => setMonthAnchor(new Date(d.year, d.month - 1, 1))}
              theme={{
                backgroundColor: 'transparent',
                calendarBackground: 'transparent',
                textSectionTitleColor: '#9AA3AF',
                textSectionTitleDisabledColor: '#D1D5DB',
                selectedDayBackgroundColor: '#172033',
                selectedDayTextColor: '#FFFFFF',
                todayTextColor: '#172033',
                dayTextColor: '#172033',
                textDisabledColor: '#D4D7DD',
                dotColor: '#ADFF2F',
                selectedDotColor: '#FFFFFF',
                arrowColor: '#172033',
                monthTextColor: '#172033',
                indicatorColor: '#172033',
                textDayFontWeight: '600',
                textMonthFontWeight: '800',
                textDayHeaderFontWeight: '700',
                textDayFontSize: 16,
                textMonthFontSize: 17,
                textDayHeaderFontSize: 12,
              }}
              style={styles.calendar}
            />

            <View style={styles.legendRow}>
              <View style={styles.legendItem}>
                <View style={[styles.legendDot, styles.legendDotActive]} />
                <Text style={styles.legendText}>有记录</Text>
              </View>
              <View style={styles.legendItem}>
                <View style={[styles.legendDot, styles.legendDotSelected]} />
                <Text style={styles.legendText}>当前选中</Text>
              </View>
            </View>
          </View>
        </View>
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    backgroundColor: '#FFF8FA',
  },
  loginRoot: {
    flex: 1,
    backgroundColor: '#FFF8FA',
    paddingHorizontal: 16,
  },
  scrollContent: {
    paddingHorizontal: 16,
  },
  backgroundOrbOne: {
    position: 'absolute',
    top: 84,
    right: -32,
    width: 154,
    height: 154,
    borderRadius: 999,
    backgroundColor: 'rgba(255, 183, 197, 0.3)',
  },
  backgroundOrbTwo: {
    position: 'absolute',
    top: 320,
    left: -44,
    width: 126,
    height: 126,
    borderRadius: 999,
    backgroundColor: 'rgba(214, 255, 114, 0.24)',
  },
  heroCard: {
    borderRadius: 28,
    padding: 18,
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.78)',
    shadowColor: '#F3B7C7',
    shadowOffset: { width: 0, height: 14 },
    shadowOpacity: 0.18,
    shadowRadius: 28,
    elevation: 8,
    marginBottom: 18,
  },
  heroTopRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
    gap: 12,
    marginBottom: 16,
  },
  eyebrow: {
    fontSize: 11,
    fontWeight: '800',
    color: 'rgba(23, 32, 51, 0.62)',
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
    lineHeight: 19,
    color: 'rgba(23, 32, 51, 0.74)',
    maxWidth: 250,
  },
  heroBadge: {
    width: 40,
    height: 40,
    borderRadius: 20,
    backgroundColor: 'rgba(255,255,255,0.62)',
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.78)',
    alignItems: 'center',
    justifyContent: 'center',
  },
  calendarStage: {
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: 16,
  },
  calendarCard: {
    width: '100%',
    maxWidth: 380,
    borderRadius: 30,
    padding: 18,
    backgroundColor: 'rgba(255,255,255,0.84)',
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.95)',
    shadowColor: '#D8DEE9',
    shadowOffset: { width: 0, height: 16 },
    shadowOpacity: 0.14,
    shadowRadius: 28,
    elevation: 8,
  },
  calendarHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    gap: 12,
    marginBottom: 12,
  },
  calendarTitle: {
    fontSize: 22,
    fontWeight: '800',
    color: '#172033',
    marginBottom: 4,
  },
  calendarSub: {
    fontSize: 12,
    color: '#6B7280',
    lineHeight: 18,
    maxWidth: 220,
  },
  todayChip: {
    borderRadius: 999,
    paddingHorizontal: 12,
    paddingVertical: 8,
    backgroundColor: '#EEF9C7',
    borderWidth: 1,
    borderColor: 'rgba(173,255,47,0.42)',
  },
  todayChipText: {
    fontSize: 12,
    fontWeight: '800',
    color: '#172033',
  },
  calendar: {
    borderRadius: 24,
    overflow: 'hidden',
    backgroundColor: 'transparent',
  },
  legendRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 18,
    marginTop: 14,
  },
  legendItem: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
  },
  legendDot: {
    width: 10,
    height: 10,
    borderRadius: 999,
  },
  legendDotActive: {
    backgroundColor: '#ADFF2F',
  },
  legendDotSelected: {
    backgroundColor: '#172033',
  },
  legendText: {
    fontSize: 12,
    color: '#6B7280',
    fontWeight: '700',
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
