import { useEffect, useMemo, useState } from 'react';
import { Modal, Pressable, StyleSheet, Text, View } from 'react-native';
import { Calendar } from 'react-native-calendars';

import { AppButton } from '@/components/AppButton';
import { getLocalDayStamp } from '@/lib/dateTime';

interface DatePickerModalProps {
  visible: boolean;
  value: string;
  title?: string;
  subtitle?: string;
  onClose: () => void;
  onConfirm: (dayStamp: string) => void;
}

export function DatePickerModal({
  visible,
  value,
  title = '选择日期',
  subtitle = '保存前可以把这条记录归到任意一天。',
  onClose,
  onConfirm,
}: DatePickerModalProps) {
  const [draftDate, setDraftDate] = useState(value || getLocalDayStamp());

  useEffect(() => {
    if (visible) {
      setDraftDate(value || getLocalDayStamp());
    }
  }, [value, visible]);

  const markedDates = useMemo(
    () => ({
      [draftDate]: {
        selected: true,
        selectedColor: '#172033',
        selectedTextColor: '#FFFFFF',
      },
    }),
    [draftDate]
  );

  return (
    <Modal visible={visible} animationType="fade" transparent onRequestClose={onClose}>
      <View style={styles.portal}>
        <Pressable style={styles.backdrop} onPress={onClose} />
        <View style={styles.card}>
          <Text style={styles.title}>{title}</Text>
          <Text style={styles.subtitle}>{subtitle}</Text>

          <View style={styles.chipRow}>
            <Pressable
              onPress={() => setDraftDate(getLocalDayStamp())}
              style={({ pressed }) => [styles.todayChip, pressed && styles.todayChipPressed]}
            >
              <Text style={styles.todayChipText}>回到今天</Text>
            </Pressable>
            <Text style={styles.selectedLabel}>{formatDateLabel(draftDate)}</Text>
          </View>

          <Calendar
            current={draftDate}
            markedDates={markedDates}
            firstDay={1}
            enableSwipeMonths
            onDayPress={(day) => setDraftDate(day.dateString)}
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

          <View style={styles.actionRow}>
            <AppButton label="取消" variant="ghost" onPress={onClose} style={styles.actionBtn} />
            <AppButton
              label="确认日期"
              onPress={() => {
                onConfirm(draftDate);
                onClose();
              }}
              style={styles.actionBtn}
            />
          </View>
        </View>
      </View>
    </Modal>
  );
}

function formatDateLabel(dayStamp: string) {
  const parsed = new Date(`${dayStamp}T12:00:00`);
  if (Number.isNaN(parsed.getTime())) {
    return '今天';
  }

  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
    weekday: 'short',
  }).format(parsed);
}

const styles = StyleSheet.create({
  portal: {
    flex: 1,
    justifyContent: 'center',
    paddingHorizontal: 18,
  },
  backdrop: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: 'rgba(24, 27, 36, 0.28)',
  },
  card: {
    borderRadius: 30,
    padding: 18,
    backgroundColor: 'rgba(255,255,255,0.97)',
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.94)',
    shadowColor: '#F3B7C7',
    shadowOffset: { width: 0, height: 18 },
    shadowOpacity: 0.18,
    shadowRadius: 28,
    elevation: 10,
  },
  title: {
    fontSize: 22,
    fontWeight: '800',
    color: '#172033',
  },
  subtitle: {
    marginTop: 6,
    fontSize: 13,
    lineHeight: 19,
    color: '#6B7280',
  },
  chipRow: {
    marginTop: 14,
    marginBottom: 10,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 12,
  },
  todayChip: {
    borderRadius: 999,
    paddingHorizontal: 12,
    paddingVertical: 8,
    backgroundColor: '#EEF9C7',
    borderWidth: 1,
    borderColor: 'rgba(173,255,47,0.42)',
  },
  todayChipPressed: {
    opacity: 0.84,
  },
  todayChipText: {
    fontSize: 12,
    fontWeight: '800',
    color: '#172033',
  },
  selectedLabel: {
    flex: 1,
    textAlign: 'right',
    fontSize: 12,
    fontWeight: '700',
    color: '#6B7280',
  },
  calendar: {
    borderRadius: 24,
    overflow: 'hidden',
    backgroundColor: 'transparent',
  },
  actionRow: {
    marginTop: 14,
    flexDirection: 'row',
    gap: 10,
  },
  actionBtn: {
    flex: 1,
  },
});
