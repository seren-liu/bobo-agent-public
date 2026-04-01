import { Pressable, StyleSheet, Text, View } from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';

import type { DrinkRecord } from '@/lib/api';

const CARD_PALETTES = [
  {
    colors: ['#FFF5D8', '#FFF0BE'],
    border: '#F7DE88',
    brand: '#B88912',
    glow: 'rgba(247, 222, 136, 0.45)',
  },
  {
    colors: ['#E6F8EE', '#D3F1E1'],
    border: '#BCE5CE',
    brand: '#2E8B62',
    glow: 'rgba(188, 229, 206, 0.48)',
  },
  {
    colors: ['#E9EDFF', '#DCE2FF'],
    border: '#CAD4FF',
    brand: '#5A67D8',
    glow: 'rgba(202, 212, 255, 0.5)',
  },
  {
    colors: ['#FFE9EE', '#FFDCE6'],
    border: '#FFC7D7',
    brand: '#C0567A',
    glow: 'rgba(255, 199, 215, 0.5)',
  },
  {
    colors: ['#EAF8FF', '#DBF1FF'],
    border: '#C5E6FA',
    brand: '#26799A',
    glow: 'rgba(197, 230, 250, 0.5)',
  },
];

interface RecentDrinkCardProps {
  record: DrinkRecord;
  index?: number;
  onPress?: () => void;
}

function formatRecordDate(consumedAt: string) {
  const date = new Date(consumedAt);
  return date.toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
  });
}

function getTags(record: DrinkRecord) {
  return [record.sugar, record.ice].filter(Boolean) as string[];
}

export function RecentDrinkCard({
  record,
  index = 0,
  onPress,
}: RecentDrinkCardProps) {
  const palette = CARD_PALETTES[index % CARD_PALETTES.length]!;
  const tags = getTags(record);

  return (
    <Pressable
      onPress={onPress}
      style={({ pressed }) => [
        styles.pressable,
        pressed && styles.pressablePressed,
      ]}
    >
      <LinearGradient
        colors={palette.colors as [string, string]}
        start={{ x: 0, y: 0 }}
        end={{ x: 1, y: 1 }}
        style={[styles.card, { borderColor: palette.border }]}
      >
        <View style={[styles.glowOrb, { backgroundColor: palette.glow }]} />
        <View style={styles.topRow}>
          <Text style={[styles.brand, { color: palette.brand }]} numberOfLines={1}>
            {record.brand.toUpperCase()}
          </Text>
          <Text style={styles.date}>{formatRecordDate(record.consumed_at)}</Text>
        </View>

        <View style={styles.content}>
          <Text style={styles.name} numberOfLines={3}>
            {record.name}
          </Text>
        </View>

        <View style={styles.bottom}>
          <View style={styles.tagRow}>
            {(tags.length ? tags : ['Fresh log']).map((tag) => (
              <View key={`${record.id}-${tag}`} style={styles.tag}>
                <Text style={styles.tagText}>{tag}</Text>
              </View>
            ))}
          </View>

          <Text style={styles.price}>¥{Number(record.price ?? 0).toFixed(0)}</Text>
        </View>
      </LinearGradient>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  pressable: {},
  pressablePressed: {
    opacity: 0.92,
    transform: [{ scale: 0.97 }],
  },
  card: {
    width: 142,
    minHeight: 158,
    borderRadius: 22,
    paddingHorizontal: 12,
    paddingVertical: 12,
    borderWidth: 1,
    overflow: 'hidden',
    shadowColor: '#D8DDE8',
    shadowOffset: { width: 0, height: 14 },
    shadowOpacity: 0.18,
    shadowRadius: 22,
    elevation: 6,
    justifyContent: 'space-between',
  },
  glowOrb: {
    position: 'absolute',
    width: 74,
    height: 74,
    borderRadius: 999,
    top: -16,
    right: -8,
  },
  topRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 10,
  },
  brand: {
    flex: 1,
    fontSize: 9,
    fontWeight: '700',
    letterSpacing: 0.8,
  },
  date: {
    fontSize: 9,
    fontWeight: '600',
    color: '#8E8E93',
  },
  content: {
    marginTop: 4,
    flex: 1,
    justifyContent: 'center',
  },
  name: {
    fontSize: 14,
    lineHeight: 18,
    fontWeight: '800',
    color: '#1C1C1E',
    maxWidth: '88%',
  },
  bottom: {
    gap: 8,
  },
  tagRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
  },
  tag: {
    backgroundColor: 'rgba(255,255,255,0.74)',
    borderRadius: 999,
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.92)',
  },
  tagText: {
    fontSize: 9,
    fontWeight: '600',
    color: '#2C2C2E',
  },
  price: {
    fontSize: 13,
    fontWeight: '800',
    color: '#1C1C1E',
  },
});
