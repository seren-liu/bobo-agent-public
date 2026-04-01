import { View, Text, Pressable, StyleSheet } from 'react-native';
import type { DrinkRecord } from '@/lib/api';

// Pastel glass palette – rotates by record index or derives from brand
const CARD_PALETTES = [
  { from: '#FFF8D6', to: '#FFF0A0', border: '#FFE97A' }, // yellow
  { from: '#E8F5F0', to: '#C8EBE2', border: '#A8DBCE' }, // mint
  { from: '#E8F4FF', to: '#C8E0FF', border: '#A8CAFF' }, // blue
  { from: '#FFE8EC', to: '#FFD0D8', border: '#FFB8C4' }, // pink
];

interface DrinkCardProps {
  record: DrinkRecord;
  index?: number;
  onPress?: () => void;
}

export function DrinkCard({ record, index = 0, onPress }: DrinkCardProps) {
  const palette = CARD_PALETTES[index % CARD_PALETTES.length]!;

  return (
    <Pressable
      onPress={onPress}
      style={({ pressed }) => [
        styles.card,
        {
          backgroundColor: palette.from,
          borderColor: palette.border,
          opacity: pressed ? 0.85 : 1,
          transform: pressed ? [{ scale: 0.97 }] : [{ scale: 1 }],
        },
      ]}
    >
      {/* Brand */}
      <Text style={styles.brand} numberOfLines={1}>
        {record.brand.toUpperCase()}
      </Text>

      {/* Name */}
      <Text style={styles.name} numberOfLines={2}>
        {record.name}
      </Text>

      {/* Tags */}
      <View style={styles.tags}>
        {record.sugar ? (
          <View style={styles.tag}>
            <Text style={styles.tagText}>{record.sugar}</Text>
          </View>
        ) : null}
        {record.ice ? (
          <View style={styles.tag}>
            <Text style={styles.tagText}>{record.ice}</Text>
          </View>
        ) : null}
      </View>

      {/* Price */}
      <Text style={styles.price}>¥{Number(record.price ?? 0).toFixed(0)}</Text>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  card: {
    width: 140,
    height: 160,
    borderRadius: 20,
    padding: 14,
    marginRight: 12,
    borderWidth: 1,
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.08,
    shadowRadius: 12,
    elevation: 4,
    justifyContent: 'space-between',
  },
  brand: {
    fontSize: 10,
    color: '#8E8E93',
    fontWeight: '600',
    letterSpacing: 0.8,
  },
  name: {
    fontSize: 15,
    color: '#1C1C1E',
    fontWeight: '600',
    flex: 1,
    marginTop: 4,
    lineHeight: 20,
  },
  tags: {
    flexDirection: 'row',
    gap: 4,
    flexWrap: 'wrap',
    marginBottom: 4,
  },
  tag: {
    backgroundColor: 'rgba(255,255,255,0.75)',
    borderRadius: 20,
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.5)',
  },
  tagText: {
    fontSize: 10,
    color: '#1C1C1E',
    fontWeight: '500',
  },
  price: {
    fontSize: 14,
    color: '#1C1C1E',
    fontWeight: '700',
  },
});
