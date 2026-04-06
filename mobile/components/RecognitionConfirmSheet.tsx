import { forwardRef, useImperativeHandle, useMemo, useRef, useState } from 'react';
import { Alert, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';
import BottomSheet, { BottomSheetBackdrop, BottomSheetView } from '@gorhom/bottom-sheet';
import { useMutation, useQueryClient } from '@tanstack/react-query';

import { boboApi, type ConfirmItem, type VisionItem } from '@/lib/api';
import { AppButton } from '@/components/AppButton';
import { DatePickerModal } from '@/components/DatePickerModal';
import { buildLocalDateTimeForDay, getLocalDayStamp } from '@/lib/dateTime';
import { getFriendlyRecordSaveError } from '@/lib/errorMessages';

interface EditableVisionItem {
  id: string;
  selected: boolean;
  brand: string;
  name: string;
  size: string;
  sugar: string;
  ice: string;
  mood: string;
  price: string;
}

export interface RecognitionConfirmSheetRef {
  open: (args: {
    sourceType: 'photo' | 'screenshot';
    fileUrl: string;
    orderTime?: string | null;
    items: VisionItem[];
  }) => void;
  close: () => void;
}

export const RecognitionConfirmSheet = forwardRef<RecognitionConfirmSheetRef>((_, ref) => {
  const sheetRef = useRef<BottomSheet>(null);
  const queryClient = useQueryClient();
  const [sourceType, setSourceType] = useState<'photo' | 'screenshot'>('photo');
  const [fileUrl, setFileUrl] = useState<string>('');
  const [items, setItems] = useState<EditableVisionItem[]>([]);
  const [selectedDay, setSelectedDay] = useState(getLocalDayStamp());
  const [datePickerVisible, setDatePickerVisible] = useState(false);

  const snapPoints = useMemo(() => ['80%'], []);

  useImperativeHandle(ref, () => ({
    open: ({ sourceType, fileUrl, orderTime, items }) => {
      setSourceType(sourceType);
      setFileUrl(fileUrl);
      void orderTime;
      setSelectedDay(getLocalDayStamp());
      setItems(
        items.map((it, idx) => ({
          id: `${Date.now()}-${idx}`,
          selected: true,
          brand: it.brand ?? '',
          name: it.name ?? '',
          size: it.size ?? '',
          sugar: it.sugar ?? '',
          ice: it.ice ?? '',
          mood: '',
          price: it.price == null ? '' : String(it.price),
        }))
      );
      sheetRef.current?.expand();
    },
    close: () => sheetRef.current?.close(),
  }));

  const saveMutation = useMutation({
    mutationFn: async () => {
      const selected = items
        .filter((item) => item.selected && item.brand.trim() && item.name.trim())
        .map<ConfirmItem>((item) => ({
          brand: item.brand.trim(),
          name: item.name.trim(),
          size: item.size.trim() || undefined,
          sugar: item.sugar.trim() || undefined,
          ice: item.ice.trim() || undefined,
          mood: item.mood.trim() || undefined,
          price: Number(item.price) || 0,
          source: sourceType,
          photo_url: fileUrl,
          photos: [{ url: fileUrl, sort_order: 0 }],
          consumed_at: buildLocalDateTimeForDay(selectedDay, 12, 0, 0),
        }));

      if (!selected.length) {
        throw new Error('请选择至少一条可保存记录');
      }

      await boboApi.confirmRecords(selected);
    },
    onSuccess: () => {
      const today = getLocalDayStamp();
      queryClient.invalidateQueries({ queryKey: ['records', 'day'] });
      queryClient.invalidateQueries({ queryKey: ['records', 'calendar'] });
      queryClient.invalidateQueries({ queryKey: ['records', 'recent'] });
      queryClient.invalidateQueries({ queryKey: ['records', 'stats'] });
      queryClient.invalidateQueries({ queryKey: ['records', 'day-detail'] });
      queryClient.invalidateQueries({ queryKey: ['records', 'day', today] });
      queryClient.invalidateQueries({ queryKey: ['records', 'day-detail', today] });
      Alert.alert('识别已保存', '记录已写入你的饮品日历');
      sheetRef.current?.close();
    },
    onError: (error) => {
      Alert.alert('保存失败', getFriendlyRecordSaveError(error));
    },
  });

  return (
    <BottomSheet
      ref={sheetRef}
      index={-1}
      snapPoints={snapPoints}
      enablePanDownToClose
      backdropComponent={(props) => <BottomSheetBackdrop {...props} appearsOnIndex={0} disappearsOnIndex={-1} />}
      backgroundStyle={styles.sheetBg}
      handleIndicatorStyle={styles.handle}
    >
      <BottomSheetView style={styles.content}>
        <Text style={styles.title}>识别确认</Text>
        <Text style={styles.subtitle}>可编辑、取消勾选或删除单条后再批量保存</Text>

        <Pressable
          onPress={() => setDatePickerVisible(true)}
          style={({ pressed }) => [styles.dateCard, pressed && styles.dateCardPressed]}
        >
          <View>
            <Text style={styles.dateCardLabel}>记录日期</Text>
            <Text style={styles.dateCardValue}>{formatSelectedDay(selectedDay)}</Text>
          </View>
          <Text style={styles.dateCardAction}>选择</Text>
        </Pressable>

        <ScrollView showsVerticalScrollIndicator={false} contentContainerStyle={styles.list}>
          {items.map((item) => (
            <View key={item.id} style={styles.card}>
              <View style={styles.cardTop}>
                <Pressable
                  onPress={() =>
                    setItems((prev) =>
                      prev.map((p) => (p.id === item.id ? { ...p, selected: !p.selected } : p))
                    )
                  }
                  style={[styles.checkbox, item.selected && styles.checkboxActive]}
                />
                <Pressable
                  onPress={() => setItems((prev) => prev.filter((p) => p.id !== item.id))}
                  style={styles.deleteBtn}
                >
                  <Text style={styles.deleteBtnText}>删除</Text>
                </Pressable>
              </View>
              <View style={styles.grid}>
                <Field
                  label="品牌"
                  value={item.brand}
                  onChangeText={(v) =>
                    setItems((prev) => prev.map((p) => (p.id === item.id ? { ...p, brand: v } : p)))
                  }
                />
                <Field
                  label="名称"
                  value={item.name}
                  onChangeText={(v) =>
                    setItems((prev) => prev.map((p) => (p.id === item.id ? { ...p, name: v } : p)))
                  }
                />
                <Field
                  label="规格"
                  value={item.size}
                  onChangeText={(v) =>
                    setItems((prev) => prev.map((p) => (p.id === item.id ? { ...p, size: v } : p)))
                  }
                />
                <Field
                  label="价格"
                  value={item.price}
                  keyboardType="decimal-pad"
                  onChangeText={(v) =>
                    setItems((prev) => prev.map((p) => (p.id === item.id ? { ...p, price: v } : p)))
                  }
                />
                <Field
                  label="甜度"
                  value={item.sugar}
                  onChangeText={(v) =>
                    setItems((prev) => prev.map((p) => (p.id === item.id ? { ...p, sugar: v } : p)))
                  }
                />
                <Field
                  label="冰量"
                  value={item.ice}
                  onChangeText={(v) =>
                    setItems((prev) => prev.map((p) => (p.id === item.id ? { ...p, ice: v } : p)))
                  }
                />
                <Field
                  label="心情"
                  value={item.mood}
                  onChangeText={(v) =>
                    setItems((prev) => prev.map((p) => (p.id === item.id ? { ...p, mood: v } : p)))
                  }
                />
              </View>
            </View>
          ))}
        </ScrollView>

        <AppButton
          label="确认并写入记录"
          onPress={() => saveMutation.mutate()}
          loading={saveMutation.isPending}
          style={styles.submit}
        />

        <DatePickerModal
          visible={datePickerVisible}
          value={selectedDay}
          title="选择识别记录日期"
          subtitle="默认是今天，识别出的饮品会一起保存到你选中的这一天。"
          onClose={() => setDatePickerVisible(false)}
          onConfirm={setSelectedDay}
        />
      </BottomSheetView>
    </BottomSheet>
  );
});

RecognitionConfirmSheet.displayName = 'RecognitionConfirmSheet';

function formatSelectedDay(dayStamp: string) {
  const parsed = new Date(`${dayStamp}T12:00:00`);
  if (Number.isNaN(parsed.getTime())) {
    return '今天';
  }

  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    weekday: 'short',
  }).format(parsed);
}

function Field({
  label,
  value,
  onChangeText,
  keyboardType,
}: {
  label: string;
  value: string;
  onChangeText: (v: string) => void;
  keyboardType?: 'default' | 'decimal-pad';
}) {
  return (
    <View style={styles.field}>
      <Text style={styles.label}>{label}</Text>
      <TextInput
        style={styles.input}
        value={value}
        keyboardType={keyboardType}
        onChangeText={onChangeText}
        placeholderTextColor="#9CA3AF"
      />
    </View>
  );
}

const styles = StyleSheet.create({
  sheetBg: { backgroundColor: '#FAFAFA', borderRadius: 24 },
  handle: { backgroundColor: '#D1D5DB' },
  content: { flex: 1, paddingHorizontal: 16, paddingBottom: 18 },
  title: { fontSize: 20, fontWeight: '700', color: '#111827' },
  subtitle: { marginTop: 4, marginBottom: 10, fontSize: 12, color: '#6B7280' },
  dateCard: {
    marginBottom: 10,
    borderRadius: 16,
    borderWidth: 1,
    borderColor: 'rgba(255,222,183,0.6)',
    backgroundColor: '#FFF9EB',
    paddingHorizontal: 14,
    paddingVertical: 12,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  dateCardPressed: {
    opacity: 0.88,
    transform: [{ scale: 0.99 }],
  },
  dateCardLabel: {
    fontSize: 11,
    color: '#A07152',
    fontWeight: '700',
    marginBottom: 4,
  },
  dateCardValue: {
    fontSize: 15,
    color: '#8B5E3C',
    fontWeight: '700',
  },
  dateCardAction: {
    fontSize: 12,
    fontWeight: '800',
    color: '#A07152',
  },
  list: { gap: 10, paddingBottom: 10 },
  card: {
    borderRadius: 14,
    borderWidth: 1,
    borderColor: '#E5E7EB',
    backgroundColor: '#FFFFFF',
    padding: 10,
  },
  cardTop: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  checkbox: {
    width: 20,
    height: 20,
    borderRadius: 6,
    borderWidth: 1.5,
    borderColor: '#9CA3AF',
  },
  checkboxActive: {
    backgroundColor: '#ADFF2F',
    borderColor: '#84CC16',
  },
  deleteBtn: { paddingVertical: 4, paddingHorizontal: 8, borderRadius: 8, backgroundColor: '#FEF2F2' },
  deleteBtnText: { color: '#991B1B', fontSize: 12, fontWeight: '600' },
  grid: { marginTop: 8, flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  field: { width: '48%' },
  label: { fontSize: 11, color: '#6B7280', marginBottom: 4 },
  input: {
    borderWidth: 1,
    borderColor: '#E5E7EB',
    borderRadius: 10,
    paddingHorizontal: 8,
    paddingVertical: 8,
    fontSize: 13,
    color: '#111827',
    backgroundColor: '#F9FAFB',
  },
  submit: {
    marginTop: 8,
  },
});
