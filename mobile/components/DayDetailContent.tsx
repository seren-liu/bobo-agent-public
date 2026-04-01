import { Image, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import { Ionicons } from '@expo/vector-icons';

import type { DayDetail, DrinkRecord } from '@/lib/api';

export function DayDetailContent({
  date,
  detail,
  onAddPress,
  onRecordLongPress,
}: {
  date: string;
  detail?: DayDetail;
  onAddPress?: () => void;
  onRecordLongPress?: (record: DrinkRecord) => void;
}) {
  const recordPhotos = getRecordPhotos(detail);
  const summary = formatDateSummary(date);
  const records = detail?.records ?? [];

  return (
    <>
      <LinearGradient
        colors={['#FFF8EA', '#FFF1F4', '#F3FFD8']}
        start={{ x: 0, y: 0 }}
        end={{ x: 1, y: 1 }}
        style={styles.heroCard}
      >
        <View style={styles.heroGlowPrimary} />
        <View style={styles.heroGlowSecondary} />

        <View style={styles.heroTop}>
          <View style={styles.heroCopy}>
            <Text style={styles.eyebrow}>Daily Detail</Text>
            <Text style={styles.title}>{summary.label}</Text>
            <Text style={styles.subtitle}>
              {records.length
                ? `按时间线回看 ${summary.weekday} 的饮品记录、图片和花费。`
                : `这一天还没有饮品记录，可以从这里直接补记。`}
            </Text>
          </View>

          <View style={styles.totalBadge}>
            <Text style={styles.totalBadgeLabel}>当日花费</Text>
            <Text style={styles.totalBadgeValue}>¥{Number(detail?.total ?? 0).toFixed(0)}</Text>
          </View>
        </View>

        <View style={styles.heroMetaRow}>
          <View style={styles.metaPill}>
            <View style={styles.metaPillInline}>
              <Text style={styles.metaPillValue}>{records.length}</Text>
              <Text style={styles.metaPillUnit}>杯饮品</Text>
            </View>
          </View>
          <View style={styles.metaPill}>
            <View style={styles.metaPillInline}>
              <Text style={styles.metaPillValue}>{recordPhotos.length}</Text>
              <Text style={styles.metaPillUnit}>张图片</Text>
            </View>
          </View>
          <View style={styles.metaPill}>
            <View style={styles.metaPillInline}>
              <Text style={styles.metaPillValue}>{summary.weekdayShort}</Text>
              <Text style={styles.metaPillUnit}>星期</Text>
            </View>
          </View>
        </View>
      </LinearGradient>

      <View style={styles.section}>
        <View style={styles.sectionHeader}>
          <View>
            <Text style={styles.sectionTitle}>当日图片</Text>
            <Text style={styles.sectionHint}>把拍照、截图和补记照片集中放在一起看</Text>
          </View>
        </View>
        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.photosRow}>
          {recordPhotos.length ? (
            recordPhotos.map((url, index) => (
              <View key={`${url}-${index}`} style={styles.photoCard}>
                <Image source={{ uri: url }} style={styles.photo} />
              </View>
            ))
          ) : (
            <View style={styles.emptyPhoto}>
              <Ionicons name="images-outline" size={22} color="#D4A373" />
              <Text style={styles.emptyPhotoTitle}>还没有图片</Text>
              <Text style={styles.emptyPhotoText}>今天的记录里暂时没有附带照片</Text>
            </View>
          )}
        </ScrollView>
      </View>

      <View style={styles.section}>
        <View style={styles.sectionHeader}>
          <View>
            <Text style={styles.sectionTitle}>饮品时间线</Text>
            <Text style={styles.sectionHint}>按录入时间回看每一杯喝了什么</Text>
          </View>
        </View>
        {records.length ? <Text style={styles.timelineTip}>长按某张卡片即可删除这条记录</Text> : null}

        {records.length ? (
          <View style={styles.timeline}>
            {records.map((record, index) => {
              const inlinePhotoUrls = getInlinePhotoUrls(record.photos, record.photo_url);
              const description = record.notes?.trim()
                ? record.notes.trim()
                : `${record.size ?? '常规杯'} · ${record.sugar ?? '糖度未记'} · ${record.ice ?? '冰量未记'} · ${formatSource(record.source)}`;

              return (
                <View style={styles.timelineRow} key={record.id}>
                  <View style={styles.timelineTrack}>
                    <View style={[styles.timelineDot, index === 0 && styles.timelineDotActive]} />
                    {index !== records.length - 1 ? <View style={styles.timelineLine} /> : null}
                  </View>

                  <Pressable
                    accessibilityRole="button"
                    accessibilityHint="长按打开删除操作"
                    delayLongPress={280}
                    onLongPress={() => onRecordLongPress?.(record)}
                    style={({ pressed }) => [
                      styles.recordCard,
                      pressed && styles.recordCardPressed,
                    ]}
                  >
                    <View style={styles.recordHeader}>
                      <View style={styles.recordTitleWrap}>
                        <Text style={styles.timestamp}>{formatDateTime(record.consumed_at || record.created_at)}</Text>
                        <Text style={styles.name}>
                          {record.brand} {record.name}
                        </Text>
                      </View>
                      <Text style={styles.price}>¥{Number(record.price ?? 0).toFixed(0)}</Text>
                    </View>

                    <View style={styles.chipsRow}>
                      <View style={styles.chip}>
                        <Text style={styles.chipText}>{record.size ?? '常规杯'}</Text>
                      </View>
                      <View style={styles.chip}>
                        <Text style={styles.chipText}>{record.sugar ?? '糖度未记'}</Text>
                      </View>
                      <View style={styles.chip}>
                        <Text style={styles.chipText}>{record.ice ?? '冰量未记'}</Text>
                      </View>
                    </View>

                    {record.mood ? <Text style={styles.mood}>心情：{record.mood}</Text> : null}

                    {inlinePhotoUrls.length ? (
                      <ScrollView
                        horizontal
                        showsHorizontalScrollIndicator={false}
                        contentContainerStyle={styles.inlinePhotoRow}
                      >
                        {inlinePhotoUrls.map((url, photoIndex) => (
                          <Image key={`${record.id}-${photoIndex}`} source={{ uri: url }} style={styles.inlinePhoto} />
                        ))}
                      </ScrollView>
                    ) : null}

                    <View style={styles.noteCard}>
                      <Text style={styles.noteLabel}>饮品描述</Text>
                      <Text style={styles.noteText}>{description}</Text>
                    </View>
                  </Pressable>
                </View>
              );
            })}
          </View>
        ) : (
          <View style={styles.emptyTimeline}>
            <Text style={styles.emptyTimelineEmoji}>🧋</Text>
            <Text style={styles.emptyTimelineTitle}>这一天还是空白的</Text>
            <Text style={styles.emptyTimelineText}>你可以补记一杯饮品，让日历回顾更完整。</Text>
            <Pressable
              accessibilityRole="button"
              onPress={onAddPress}
              style={({ pressed }) => [styles.emptyAction, pressed && styles.emptyActionPressed]}
            >
              <Text style={styles.emptyActionText}>立即补记</Text>
            </Pressable>
          </View>
        )}
      </View>
    </>
  );
}

function getRecordPhotos(detail?: DayDetail) {
  const photosFromRecords = (detail?.records ?? []).flatMap((record) => {
    const explicitPhotos = (record.photos ?? []).map((photo) => photo.url).filter(Boolean);
    if (explicitPhotos.length) {
      return explicitPhotos;
    }
    return record.photo_url ? [record.photo_url] : [];
  });

  return Array.from(new Set([...(detail?.photos ?? []), ...photosFromRecords]));
}

function getInlinePhotoUrls(
  photos?: Array<{ url: string; sort_order: number; created_at?: string | null }>,
  photoUrl?: string
) {
  const explicitPhotos = (photos ?? []).map((photo) => photo.url).filter(Boolean);
  if (explicitPhotos.length) {
    return explicitPhotos;
  }
  return photoUrl ? [photoUrl] : [];
}

function formatDateSummary(value: string) {
  const parsed = new Date(`${value}T12:00:00`);
  if (Number.isNaN(parsed.getTime())) {
    return {
      label: value,
      weekday: '这一天',
      weekdayShort: '--',
    };
  }

  return {
    label: parsed.toLocaleDateString('zh-CN', {
      month: 'long',
      day: 'numeric',
    }),
    weekday: parsed.toLocaleDateString('zh-CN', {
      weekday: 'long',
    }),
    weekdayShort: parsed.toLocaleDateString('zh-CN', {
      weekday: 'short',
    }),
  };
}

function formatDateTime(value: string) {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }

  const year = parsed.getFullYear();
  const month = String(parsed.getMonth() + 1).padStart(2, '0');
  const day = String(parsed.getDate()).padStart(2, '0');
  const hours = String(parsed.getHours()).padStart(2, '0');
  const minutes = String(parsed.getMinutes()).padStart(2, '0');

  return `${year}-${month}-${day} ${hours}:${minutes}`;
}

function formatSource(source: string) {
  switch (source) {
    case 'photo':
      return '拍照识别录入';
    case 'screenshot':
      return '截图识别录入';
    case 'agent':
      return 'AI 助手录入';
    default:
      return '手动补记';
  }
}

const styles = StyleSheet.create({
  heroCard: {
    borderRadius: 30,
    padding: 20,
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.88)',
    overflow: 'hidden',
    shadowColor: '#FFB7C5',
    shadowOffset: { width: 0, height: 14 },
    shadowOpacity: 0.2,
    shadowRadius: 24,
    elevation: 8,
  },
  heroGlowPrimary: {
    position: 'absolute',
    width: 180,
    height: 180,
    borderRadius: 999,
    backgroundColor: 'rgba(255,255,255,0.42)',
    top: -50,
    right: -20,
  },
  heroGlowSecondary: {
    position: 'absolute',
    width: 110,
    height: 110,
    borderRadius: 999,
    backgroundColor: 'rgba(173,255,47,0.18)',
    bottom: -10,
    left: -20,
  },
  heroTop: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    justifyContent: 'space-between',
    gap: 14,
  },
  heroCopy: {
    flex: 1,
    paddingRight: 4,
  },
  eyebrow: {
    fontSize: 12,
    fontWeight: '700',
    letterSpacing: 1.1,
    color: '#A07152',
    textTransform: 'uppercase',
    marginBottom: 8,
  },
  title: {
    fontSize: 30,
    fontWeight: '800',
    color: '#172033',
  },
  subtitle: {
    marginTop: 8,
    fontSize: 13,
    lineHeight: 20,
    color: '#667085',
  },
  totalBadge: {
    minWidth: 92,
    paddingHorizontal: 14,
    paddingVertical: 13,
    borderRadius: 22,
    backgroundColor: 'rgba(255,255,255,0.78)',
    alignItems: 'flex-end',
  },
  totalBadgeLabel: {
    fontSize: 11,
    color: '#8B5E3C',
    fontWeight: '700',
  },
  totalBadgeValue: {
    marginTop: 4,
    fontSize: 24,
    fontWeight: '800',
    color: '#172033',
  },
  heroMetaRow: {
    flexDirection: 'row',
    gap: 10,
    marginTop: 18,
  },
  metaPill: {
    flex: 1,
    borderRadius: 20,
    paddingVertical: 12,
    paddingHorizontal: 12,
    backgroundColor: 'rgba(23,32,51,0.06)',
  },
  metaPillInline: {
    flexDirection: 'row',
    alignItems: 'baseline',
    gap: 4,
  },
  metaPillValue: {
    fontSize: 24,
    fontWeight: '800',
    color: '#172033',
  },
  metaPillUnit: {
    fontSize: 12,
    color: '#6B7280',
    fontWeight: '700',
  },
  section: {
    marginTop: 22,
  },
  sectionHeader: {
    marginBottom: 12,
  },
  sectionTitle: {
    fontSize: 18,
    fontWeight: '800',
    color: '#172033',
  },
  sectionHint: {
    marginTop: 4,
    fontSize: 12,
    lineHeight: 18,
    color: '#8B95A7',
  },
  photosRow: {
    gap: 12,
    paddingRight: 10,
  },
  photoCard: {
    width: 182,
    height: 218,
    borderRadius: 26,
    padding: 6,
    backgroundColor: '#FFFFFF',
    shadowColor: '#D1B9A5',
    shadowOffset: { width: 0, height: 8 },
    shadowOpacity: 0.13,
    shadowRadius: 18,
    elevation: 6,
  },
  photo: {
    width: '100%',
    height: '100%',
    borderRadius: 20,
    backgroundColor: '#F3F4F6',
  },
  emptyPhoto: {
    width: 220,
    height: 218,
    borderRadius: 26,
    borderWidth: 1,
    borderColor: '#F0E7E2',
    backgroundColor: '#FFFCFA',
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 22,
  },
  emptyPhotoTitle: {
    marginTop: 12,
    fontSize: 16,
    fontWeight: '800',
    color: '#172033',
  },
  emptyPhotoText: {
    marginTop: 6,
    fontSize: 12,
    lineHeight: 18,
    textAlign: 'center',
    color: '#8B95A7',
  },
  timeline: {
    gap: 14,
  },
  timelineTip: {
    marginTop: 6,
    marginBottom: 12,
    fontSize: 12,
    lineHeight: 18,
    color: '#8B95A7',
  },
  timelineRow: {
    flexDirection: 'row',
    alignItems: 'stretch',
    gap: 12,
  },
  timelineTrack: {
    width: 18,
    alignItems: 'center',
  },
  timelineDot: {
    width: 12,
    height: 12,
    borderRadius: 999,
    backgroundColor: '#FFD6DE',
    marginTop: 20,
  },
  timelineDotActive: {
    backgroundColor: '#ADFF2F',
  },
  timelineLine: {
    flex: 1,
    width: 2,
    marginTop: 6,
    marginBottom: -6,
    borderRadius: 999,
    backgroundColor: '#F0E7E2',
  },
  recordCard: {
    flex: 1,
    borderRadius: 26,
    backgroundColor: '#FFFFFF',
    borderWidth: 1,
    borderColor: '#F4ECE6',
    padding: 16,
    shadowColor: '#D6C1B3',
    shadowOffset: { width: 0, height: 8 },
    shadowOpacity: 0.12,
    shadowRadius: 18,
    elevation: 5,
  },
  recordCardPressed: {
    opacity: 0.94,
    transform: [{ scale: 0.985 }],
  },
  recordHeader: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    justifyContent: 'space-between',
    gap: 12,
  },
  recordTitleWrap: {
    flex: 1,
  },
  timestamp: {
    fontSize: 13,
    fontWeight: '800',
    color: '#B45309',
    marginBottom: 8,
  },
  name: {
    fontSize: 20,
    fontWeight: '800',
    color: '#172033',
    lineHeight: 26,
  },
  price: {
    fontSize: 20,
    fontWeight: '800',
    color: '#172033',
  },
  chipsRow: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    gap: 8,
    marginTop: 12,
  },
  chip: {
    borderRadius: 999,
    paddingHorizontal: 10,
    paddingVertical: 6,
    backgroundColor: '#F7F7FB',
    borderWidth: 1,
    borderColor: '#EEF1F6',
  },
  chipText: {
    fontSize: 12,
    fontWeight: '700',
    color: '#475467',
  },
  mood: {
    fontSize: 12,
    color: '#9A3412',
    marginTop: 10,
    fontWeight: '600',
  },
  inlinePhotoRow: {
    gap: 10,
    paddingRight: 4,
    marginTop: 14,
  },
  inlinePhoto: {
    width: 186,
    height: 168,
    borderRadius: 18,
    backgroundColor: '#F3F4F6',
  },
  noteCard: {
    marginTop: 14,
    borderRadius: 18,
    backgroundColor: '#FFF7F0',
    borderWidth: 1,
    borderColor: '#FDE7D5',
    padding: 12,
  },
  noteLabel: {
    fontSize: 11,
    fontWeight: '800',
    letterSpacing: 0.6,
    color: '#B45309',
    marginBottom: 6,
  },
  noteText: {
    fontSize: 13,
    lineHeight: 20,
    color: '#374151',
  },
  emptyTimeline: {
    borderRadius: 28,
    borderWidth: 1,
    borderColor: '#F0E7E2',
    backgroundColor: '#FFFCFA',
    paddingVertical: 32,
    paddingHorizontal: 24,
    alignItems: 'center',
  },
  emptyTimelineEmoji: {
    fontSize: 34,
  },
  emptyTimelineTitle: {
    marginTop: 10,
    fontSize: 18,
    fontWeight: '800',
    color: '#172033',
  },
  emptyTimelineText: {
    marginTop: 6,
    fontSize: 13,
    lineHeight: 20,
    color: '#8B95A7',
    textAlign: 'center',
  },
  emptyAction: {
    marginTop: 16,
    borderRadius: 16,
    backgroundColor: '#172033',
    paddingHorizontal: 18,
    paddingVertical: 12,
  },
  emptyActionPressed: {
    opacity: 0.88,
  },
  emptyActionText: {
    fontSize: 14,
    fontWeight: '800',
    color: '#FFFFFF',
  },
});
