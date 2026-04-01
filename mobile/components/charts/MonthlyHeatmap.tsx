import { useMemo } from 'react';
import { StyleSheet, Text, View } from 'react-native';

export function MonthlyHeatmap({
  yearMonth,
  dailyDensity,
}: {
  yearMonth: string;
  dailyDensity: Record<string, number>;
}) {
  const { weeks, maxValue } = useMemo(() => {
    const [y, m] = yearMonth.split('-').map(Number);
    const first = new Date(y, m - 1, 1);
    const next = new Date(y, m, 1);
    const daysInMonth = Math.round((next.getTime() - first.getTime()) / (1000 * 3600 * 24));
    const firstWeekday = first.getDay(); // 0=Sun

    const cells: Array<{ day: number; value: number } | null> = [];
    for (let i = 0; i < firstWeekday; i += 1) cells.push(null);
    for (let day = 1; day <= daysInMonth; day += 1) {
      const d = `${yearMonth}-${String(day).padStart(2, '0')}`;
      cells.push({ day, value: dailyDensity[d] ?? 0 });
    }
    while (cells.length % 7 !== 0) cells.push(null);

    const weeks = [];
    for (let i = 0; i < cells.length; i += 7) weeks.push(cells.slice(i, i + 7));
    const maxValue = Math.max(0, ...Object.values(dailyDensity));
    return { weeks, maxValue };
  }, [dailyDensity, yearMonth]);

  const level = (value: number): 0 | 1 | 2 | 3 | 4 => {
    if (value <= 0 || maxValue === 0) return 0;
    const ratio = value / maxValue;
    if (ratio <= 0.25) return 1;
    if (ratio <= 0.5) return 2;
    if (ratio <= 0.75) return 3;
    return 4;
  };

  return (
    <View style={styles.card}>
      <Text style={styles.title}>月度热力图</Text>
      <Text style={styles.sub}>饮用频次密度（颜色越深表示杯数越多）</Text>

      {(Object.keys(dailyDensity).length === 0 || maxValue === 0) && (
        <Text style={styles.empty}>本月暂无数据</Text>
      )}

      <View style={styles.grid}>
        {weeks.map((week, wIdx) => (
          <View key={`w-${wIdx}`} style={styles.week}>
            {week.map((cell, cIdx) => (
              <View
                key={`c-${wIdx}-${cIdx}`}
                style={[
                  styles.cell,
                  { backgroundColor: LEVEL_COLORS[cell ? level(cell.value) : 0] },
                ]}
              />
            ))}
          </View>
        ))}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    marginTop: 16,
    borderRadius: 20,
    padding: 16,
    backgroundColor: '#FFFFFF',
    borderWidth: 1,
    borderColor: '#ECFDF5',
  },
  title: { fontSize: 17, fontWeight: '700', color: '#111827' },
  sub: { fontSize: 12, color: '#6B7280', marginTop: 2 },
  empty: { marginTop: 10, fontSize: 12, color: '#9CA3AF' },
  grid: { marginTop: 12, gap: 6 },
  week: { flexDirection: 'row', gap: 6 },
  cell: { width: 16, height: 16, borderRadius: 4, borderWidth: 1, borderColor: '#E5E7EB' },
});

const LEVEL_COLORS = ['#F3F4F6', '#ECFCCB', '#BEF264', '#84CC16', '#4D7C0F'] as const;
