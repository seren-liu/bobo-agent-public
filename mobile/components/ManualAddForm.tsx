import { type ReactNode, useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Keyboard,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';

import { AppButton } from '@/components/AppButton';
import { DatePickerModal } from '@/components/DatePickerModal';
import { buildLocalDateTimeForDay, getCurrentLocalIsoString, getLocalDayStamp } from '@/lib/dateTime';
import { getFriendlyRecordSaveError } from '@/lib/errorMessages';

const SUGAR_OPTS = ['无糖', '少糖', '半糖', '正常'] as const;
const ICE_OPTS = ['去冰', '少冰', '正常冰', '多冰'] as const;

interface ManualAddFormProps {
  consumedAt?: string;
  onSuccess?: () => void;
  submitLabel?: string;
  saving?: boolean;
  onSubmit: (values: {
    brand: string;
    name: string;
    sugar: (typeof SUGAR_OPTS)[number];
    ice: (typeof ICE_OPTS)[number];
    mood?: string;
    price: number;
    consumedAt: string;
    reset: () => void;
  }) => Promise<void>;
}

export function ManualAddForm({
  consumedAt: consumedAtProp,
  onSuccess,
  submitLabel = '保存记录',
  saving = false,
  onSubmit,
}: ManualAddFormProps) {
  const [brand, setBrand] = useState('');
  const [name, setName] = useState('');
  const [sugar, setSugar] = useState<(typeof SUGAR_OPTS)[number]>('少糖');
  const [ice, setIce] = useState<(typeof ICE_OPTS)[number]>('少冰');
  const [mood, setMood] = useState('');
  const [price, setPrice] = useState('');
  const [selectedDay, setSelectedDay] = useState(getLocalDayStamp(consumedAtProp ?? getCurrentLocalIsoString()));
  const [datePickerVisible, setDatePickerVisible] = useState(false);

  useEffect(() => {
    setSelectedDay(getLocalDayStamp(consumedAtProp ?? getCurrentLocalIsoString()));
  }, [consumedAtProp]);

  const consumedDateLabel = useMemo(() => formatDateChip(selectedDay), [selectedDay]);

  const reset = () => {
    setBrand('');
    setName('');
    setSugar('少糖');
    setIce('少冰');
    setMood('');
    setPrice('');
    setSelectedDay(getLocalDayStamp(consumedAtProp ?? getCurrentLocalIsoString()));
  };

  const handleSubmit = async () => {
    if (!brand.trim() || !name.trim()) {
      Alert.alert('请填写品牌和饮品名称');
      return;
    }

    Keyboard.dismiss();

    try {
      await onSubmit({
        brand: brand.trim(),
        name: name.trim(),
        sugar,
        ice,
        mood: mood.trim() || undefined,
        price: parseFloat(price) || 0,
        consumedAt: buildLocalDateTimeForDay(selectedDay, 12, 0, 0),
        reset,
      });

      if (onSuccess) {
        Alert.alert('保存成功', '这杯饮品已经写进你的记录里', [
          { text: '好的', onPress: onSuccess },
        ]);
      } else {
        Alert.alert('保存成功', '这杯饮品已经写进你的记录里');
      }
    } catch (error) {
      Alert.alert('保存失败', getFriendlyRecordSaveError(error, '请检查网络连接后重试'));
    }
  };

  return (
    <View style={styles.root}>
      <View style={styles.formCard}>
        <FormField label="品牌">
          <TextInput
            style={styles.input}
            placeholder="e.g. 喜茶、奈雪"
            placeholderTextColor="#C7C7CC"
            value={brand}
            onChangeText={setBrand}
            returnKeyType="next"
          />
        </FormField>

        <FormField label="饮品名称">
          <TextInput
            style={styles.input}
            placeholder="e.g. 多肉葡萄"
            placeholderTextColor="#C7C7CC"
            value={name}
            onChangeText={setName}
            returnKeyType="next"
          />
        </FormField>

        <OptionPicker label="甜度" options={SUGAR_OPTS} value={sugar} onChange={setSugar} />
        <OptionPicker label="冰量" options={ICE_OPTS} value={ice} onChange={setIce} />

        <View style={styles.doubleRow}>
          <View style={styles.doubleCol}>
            <FormField label="价格 (¥)">
              <TextInput
                style={styles.input}
                placeholder="0"
                placeholderTextColor="#C7C7CC"
                keyboardType="decimal-pad"
                value={price}
                onChangeText={setPrice}
              />
            </FormField>
          </View>

          <View style={styles.doubleCol}>
            <FormField label="记录日期">
              <Pressable
                onPress={() => setDatePickerVisible(true)}
                style={({ pressed }) => [styles.readonlyInput, pressed && styles.dateFieldPressed]}
              >
                <Text style={styles.readonlyValue}>{consumedDateLabel}</Text>
                <Text style={styles.dateFieldAction}>选择</Text>
              </Pressable>
            </FormField>
          </View>
        </View>
      </View>

      <View style={styles.formCard}>
        <FormField label="心情 / 试饮感受">
          <TextInput
            style={[styles.input, styles.textarea]}
            placeholder="e.g. 开心、惊喜、清爽、踩雷"
            placeholderTextColor="#C7C7CC"
            value={mood}
            onChangeText={setMood}
            maxLength={120}
            multiline
            textAlignVertical="top"
          />
        </FormField>
      </View>

      <View style={styles.submitSection}>
        <AppButton
          label={submitLabel}
          onPress={handleSubmit}
          loading={saving}
          style={styles.submitBtn}
        />
      </View>

      <DatePickerModal
        visible={datePickerVisible}
        value={selectedDay}
        title="选择记录日期"
        subtitle="默认是今天，点一下就可以改成任意一天。"
        onClose={() => setDatePickerVisible(false)}
        onConfirm={setSelectedDay}
      />
    </View>
  );
}

function FormField({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}) {
  return (
    <View style={styles.fieldGroup}>
      <Text style={styles.fieldLabel}>{label}</Text>
      {children}
    </View>
  );
}

function OptionPicker<T extends string>({
  label,
  options,
  value,
  onChange,
}: {
  label: string;
  options: readonly T[];
  value: T;
  onChange: (value: T) => void;
}) {
  return (
    <FormField label={label}>
      <ScrollView horizontal showsHorizontalScrollIndicator={false}>
        <View style={styles.optionRow}>
          {options.map((opt) => {
            const active = value === opt;

            return (
              <Pressable
                key={opt}
                onPress={() => onChange(opt)}
                style={[styles.optionChip, active && styles.optionChipActive]}
              >
                <Text style={[styles.optionText, active && styles.optionTextActive]}>{opt}</Text>
              </Pressable>
            );
          })}
        </View>
      </ScrollView>
    </FormField>
  );
}

function formatDateChip(dayStamp: string) {
  const parsed = new Date(`${dayStamp}T12:00:00`);
  if (Number.isNaN(parsed.getTime())) {
    return '今天';
  }

  return parsed.toLocaleDateString('zh-CN', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    weekday: 'short',
  });
}

const styles = StyleSheet.create({
  root: {
    gap: 14,
  },
  formCard: {
    borderRadius: 26,
    padding: 18,
    backgroundColor: 'rgba(255,255,255,0.86)',
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.92)',
    shadowColor: '#FFD4DB',
    shadowOffset: { width: 0, height: 8 },
    shadowOpacity: 0.12,
    shadowRadius: 24,
    elevation: 4,
  },
  fieldGroup: {
    marginBottom: 16,
  },
  fieldLabel: {
    marginBottom: 8,
    fontSize: 13,
    fontWeight: '700',
    color: '#3C3C43',
  },
  input: {
    minHeight: 52,
    borderRadius: 16,
    backgroundColor: '#F4F5FA',
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.92)',
    paddingHorizontal: 16,
    paddingVertical: 14,
    fontSize: 15,
    color: '#1C1C1E',
  },
  textarea: {
    minHeight: 110,
  },
  optionRow: {
    flexDirection: 'row',
    gap: 10,
    paddingRight: 4,
  },
  optionChip: {
    minWidth: 78,
    paddingHorizontal: 16,
    paddingVertical: 10,
    borderRadius: 22,
    backgroundColor: '#F4F5FA',
    borderWidth: 1.5,
    borderColor: 'transparent',
    alignItems: 'center',
  },
  optionChipActive: {
    backgroundColor: 'rgba(173,255,47,0.16)',
    borderColor: '#ADFF2F',
    shadowColor: '#CDEB7F',
    shadowOffset: { width: 0, height: 4 },
    shadowOpacity: 0.16,
    shadowRadius: 8,
    elevation: 1,
  },
  optionText: {
    fontSize: 14,
    color: '#4B5563',
    fontWeight: '600',
  },
  optionTextActive: {
    color: '#1C1C1E',
  },
  doubleRow: {
    flexDirection: 'row',
    gap: 12,
  },
  doubleCol: {
    flex: 1,
  },
  readonlyInput: {
    minHeight: 52,
    borderRadius: 16,
    backgroundColor: '#FFF9EB',
    borderWidth: 1,
    borderColor: 'rgba(255,222,183,0.6)',
    paddingHorizontal: 16,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  dateFieldPressed: {
    opacity: 0.88,
    transform: [{ scale: 0.99 }],
  },
  readonlyValue: {
    fontSize: 15,
    fontWeight: '600',
    color: '#8B5E3C',
  },
  dateFieldAction: {
    fontSize: 12,
    fontWeight: '800',
    color: '#A07152',
  },
  submitSection: {
    marginTop: 2,
    paddingTop: 2,
  },
  submitBtn: {
    width: '100%',
  },
});
