import React, { useCallback, useMemo, useState } from 'react';
import {
  View,
  Text,
  ScrollView,
  StyleSheet,
  ActivityIndicator,
  RefreshControl,
  Platform,
} from 'react-native';
import { useQuery } from '@tanstack/react-query';

import { HeroCard } from '@/components/charts/HeroCard';
import { BrandPieChart } from '@/components/charts/BrandPieChart';
import { WeeklyTrendChart } from '@/components/charts/WeeklyTrendChart';
import { PreferenceBar } from '@/components/charts/PreferenceBar';
import { MonthlyHeatmap } from '@/components/charts/MonthlyHeatmap';
import { boboApi, type StatsResponse } from '@/lib/api';
import { useAuthStore } from '@/stores/authStore';
import { AppButton } from '@/components/AppButton';

const DEV_LOGIN_ENABLED = process.env.EXPO_PUBLIC_ENABLE_DEV_LOGIN === 'true';

function currentYearMonth(): string {
  const d = new Date();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  return `${d.getFullYear()}-${m}`;
}

export default function StatsScreen() {
  const accessToken = useAuthStore((s) => s.accessToken);
  const setSession = useAuthStore((s) => s.setSession);
  const [periodDate, setPeriodDate] = useState(currentYearMonth());

  const statsQuery = useQuery<StatsResponse>({
    queryKey: ['records', 'stats', periodDate, !!accessToken],
    queryFn: () => boboApi.getStats('month', periodDate).then((r) => r.data),
    enabled: !!accessToken,
  });

  const loginAsDev = useCallback(async () => {
    const { data } = await boboApi.login('dev', 'dev123456');
    setSession({
      accessToken: data.access_token,
      refreshToken: data.refresh_token,
      userId: data.user_id,
    });
  }, [setSession]);

  const stats = statsQuery.data;
  const avgWeeklyCount =
    stats && stats.weekly_trend.length > 0 ? stats.total_count / stats.weekly_trend.length : 0;
  const avgWeeklyAmount =
    stats && stats.weekly_trend.length > 0 ? stats.total_amount / stats.weekly_trend.length : 0;

  const sugarData = useMemo(
    () => (stats?.sugar_pref ?? []).map((s) => ({ label: s.sugar ?? '未知', count: s.count })),
    [stats?.sugar_pref]
  );
  const iceData = useMemo(
    () => (stats?.ice_pref ?? []).map((i) => ({ label: i.ice ?? '未知', count: i.count })),
    [stats?.ice_pref]
  );

  const errorMessage = useMemo(() => {
    const status = (statsQuery.error as any)?.response?.status;
    if (status === 401) {
      return '登录已过期，请重新登录';
    }
    if (statsQuery.error) {
      return '网络或服务异常，请下拉重试';
    }
    return null;
  }, [statsQuery.error]);

  if (!accessToken) {
    return (
      <View style={styles.root}>
        <View style={styles.loginBox}>
          <Text style={styles.heading}>Your Stats</Text>
          <Text style={styles.emptyText}>未登录，暂时无法加载真实统计数据</Text>
          {DEV_LOGIN_ENABLED ? (
            <AppButton
              label="Dev Login (dev/dev123456)"
              onPress={loginAsDev}
              style={styles.loginBtn}
            />
          ) : (
            <Text style={styles.emptyText}>Dev login is disabled in this build.</Text>
          )}
        </View>
      </View>
    );
  }

  return (
    <View style={styles.root}>
      <ScrollView
        contentContainerStyle={styles.scroll}
        showsVerticalScrollIndicator={false}
        refreshControl={
          <RefreshControl refreshing={statsQuery.isRefetching} onRefresh={statsQuery.refetch} tintColor="#ADFF2F" />
        }
      >
        <Text style={styles.heading}>Your Stats</Text>

        {errorMessage && (
          <View style={styles.errorBanner}>
            <Text style={styles.errorText}>{errorMessage}</Text>
          </View>
        )}

        {statsQuery.isLoading ? (
          <View style={styles.loadingContainer}>
            <ActivityIndicator size="large" color="#ADFF2F" />
          </View>
        ) : stats ? (
          <>
            <HeroCard
              totalAmount={stats.total_amount}
              totalCount={stats.total_count}
              avgWeeklyCount={avgWeeklyCount}
              avgWeeklyAmount={avgWeeklyAmount}
            />
            <BrandPieChart data={stats.brand_dist} />
            <WeeklyTrendChart data={stats.weekly_trend} />
            <PreferenceBar sugarData={sugarData} iceData={iceData} />
            <MonthlyHeatmap yearMonth={periodDate} dailyDensity={stats.daily_density ?? {}} />
          </>
        ) : (
          <Text style={styles.emptyText}>暂无统计数据</Text>
        )}
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    backgroundColor: '#F5F6FA',
  },
  scroll: {
    paddingTop: Platform.OS === 'ios' ? 60 : 40,
    paddingHorizontal: 20,
    paddingBottom: 112,
  },
  heading: {
    fontSize: 22,
    fontWeight: '700',
    color: '#1C1C1E',
    marginBottom: 20,
  },
  errorBanner: {
    backgroundColor: 'rgba(254,243,199,0.8)',
    borderRadius: 12,
    paddingHorizontal: 14,
    paddingVertical: 10,
    marginBottom: 16,
    borderWidth: 1,
    borderColor: 'rgba(254,243,199,0.9)',
  },
  errorText: {
    fontSize: 12,
    color: '#92400E',
    fontWeight: '500',
  },
  loadingContainer: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    paddingTop: 80,
  },
  loginBox: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 20,
    gap: 12,
  },
  emptyText: {
    fontSize: 14,
    color: '#6B7280',
  },
  loginBtn: {
    marginTop: 8,
  },
});
