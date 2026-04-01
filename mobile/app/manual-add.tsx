import { useState } from 'react';
import { KeyboardAvoidingView, Platform, Pressable, ScrollView, StyleSheet, Text, View } from 'react-native';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { Ionicons } from '@expo/vector-icons';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useQueryClient } from '@tanstack/react-query';
import * as ImagePicker from 'expo-image-picker';

import { ManualAddPhotoPicker, type ManualPhotoDraft } from '@/components/ManualAddPhotoPicker';
import { ManualAddForm } from '@/components/ManualAddForm';
import { boboApi } from '@/lib/api';
import { uploadImageAsset } from '@/lib/uploads';

export default function ManualAddScreen() {
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const queryClient = useQueryClient();
  const params = useLocalSearchParams<{ consumedAt?: string | string[] }>();
  const consumedAt = Array.isArray(params.consumedAt) ? params.consumedAt[0] : params.consumedAt;
  const [photos, setPhotos] = useState<ManualPhotoDraft[]>([]);
  const [saving, setSaving] = useState(false);

  const appendAssets = (assets: ImagePicker.ImagePickerAsset[]) => {
    setPhotos((prev) => {
      const existing = new Set(prev.map((item) => item.uri));
      const next = [...prev];

      for (const asset of assets) {
        if (next.length >= 3) {
          break;
        }
        if (existing.has(asset.uri)) {
          continue;
        }
        next.push({
          id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
          uri: asset.uri,
          mimeType: asset.mimeType || 'image/jpeg',
          filename: asset.fileName || `manual-${Date.now()}.jpg`,
          status: 'local',
        });
      }

      return next;
    });
  };

  const removePhoto = (id: string) => {
    setPhotos((prev) => prev.filter((item) => item.id !== id));
  };

  const uploadPendingPhotos = async () => {
    const nextPhotos = [...photos];
    const uploadedUrls: string[] = [];

    for (let index = 0; index < nextPhotos.length; index += 1) {
      const photo = nextPhotos[index];
      if (photo.fileUrl) {
        uploadedUrls.push(photo.fileUrl);
        continue;
      }

      setPhotos((prev) => prev.map((item) => (item.id === photo.id ? { ...item, status: 'uploading' } : item)));

      try {
        const fileUrl = await uploadImageAsset({
          uri: photo.uri,
          mimeType: photo.mimeType,
          fileName: photo.filename,
        });
        uploadedUrls.push(fileUrl);
        nextPhotos[index] = { ...photo, status: 'uploaded', fileUrl };
        setPhotos((prev) =>
          prev.map((item) => (item.id === photo.id ? { ...item, status: 'uploaded', fileUrl } : item))
        );
      } catch {
        setPhotos((prev) => prev.map((item) => (item.id === photo.id ? { ...item, status: 'failed' } : item)));
        throw new Error(`第 ${index + 1} 张图片上传失败`);
      }
    }

    return uploadedUrls;
  };

  const handleSave = async (values: {
    brand: string;
    name: string;
    sugar: '无糖' | '少糖' | '半糖' | '正常';
    ice: '去冰' | '少冰' | '正常冰' | '多冰';
    mood?: string;
    price: number;
    consumedAt: string;
    reset: () => void;
  }) => {
    setSaving(true);

    try {
      const uploadedUrls = await uploadPendingPhotos();
      try {
        await boboApi.confirmRecords([
          {
            brand: values.brand,
            name: values.name,
            sugar: values.sugar,
            ice: values.ice,
            mood: values.mood,
            price: values.price,
            source: 'manual',
            consumed_at: values.consumedAt,
            photos: uploadedUrls.map((url, index) => ({ url, sort_order: index })),
          },
        ]);
      } catch {
        if (uploadedUrls.length) {
          throw new Error('图片已上传，但记录保存失败，请重试');
        }
        throw new Error('记录保存失败，请稍后重试');
      }

      queryClient.invalidateQueries({ queryKey: ['records', 'day'] });
      queryClient.invalidateQueries({ queryKey: ['records', 'calendar'] });
      queryClient.invalidateQueries({ queryKey: ['records', 'recent'] });
      queryClient.invalidateQueries({ queryKey: ['records', 'stats'] });
      queryClient.invalidateQueries({ queryKey: ['records', 'day-detail'] });

      values.reset();
      setPhotos([]);
    } catch (error) {
      if (error instanceof Error) {
        throw error;
      }
      throw new Error('请检查网络连接后重试');
    } finally {
      setSaving(false);
    }
  };

  return (
    <View style={styles.page}>
      <KeyboardAvoidingView
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
        style={styles.flex}
      >
        <View
          style={[
            styles.screen,
            {
              paddingTop: insets.top + 10,
            },
          ]}
        >
          <View style={styles.header}>
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="返回"
              onPress={() => router.back()}
              style={({ pressed }) => [styles.backButton, pressed && styles.backButtonPressed]}
            >
              <Ionicons name="chevron-back" size={22} color="#1C1C1E" />
            </Pressable>

            <View style={styles.headerCopy}>
              <Text style={styles.title}>手动新增饮品</Text>
            </View>
          </View>

          <ScrollView
            contentContainerStyle={[
              styles.scrollContent,
              { paddingBottom: Math.max(insets.bottom, 24) + 18 },
            ]}
            showsVerticalScrollIndicator={false}
            keyboardShouldPersistTaps="handled"
            keyboardDismissMode="interactive"
          >
            <ManualAddPhotoPicker
              photos={photos}
              disabled={saving}
              onAddAssets={appendAssets}
              onRemove={removePhoto}
            />
            <ManualAddForm
              consumedAt={consumedAt}
              saving={saving}
              onSubmit={handleSave}
              onSuccess={() => router.back()}
            />
          </ScrollView>
        </View>
      </KeyboardAvoidingView>
    </View>
  );
}

const styles = StyleSheet.create({
  page: {
    flex: 1,
    backgroundColor: '#FAFAFA',
  },
  flex: {
    flex: 1,
  },
  screen: {
    flex: 1,
    paddingHorizontal: 18,
  },
  scrollContent: {
    paddingBottom: 24,
  },
  header: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    gap: 12,
    marginBottom: 18,
  },
  backButton: {
    width: 42,
    height: 42,
    borderRadius: 21,
    backgroundColor: 'rgba(255,255,255,0.88)',
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 1,
    borderColor: 'rgba(0,0,0,0.04)',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 3 },
    shadowOpacity: 0.06,
    shadowRadius: 10,
    elevation: 2,
  },
  backButtonPressed: {
    opacity: 0.82,
    transform: [{ scale: 0.97 }],
  },
  headerCopy: {
    flex: 1,
    paddingTop: 2,
  },
  title: {
    fontSize: 28,
    fontWeight: '800',
    color: '#111827',
  },
});
