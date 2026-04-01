import React from 'react';
import { Alert, Image, Pressable, StyleSheet, Text, View } from 'react-native';
import * as ImagePicker from 'expo-image-picker';
import { Ionicons } from '@expo/vector-icons';

const MAX_PHOTOS = 3;

export type ManualPhotoDraft = {
  id: string;
  uri: string;
  mimeType: string;
  filename: string;
  status: 'local' | 'uploading' | 'uploaded' | 'failed';
  fileUrl?: string;
};

interface ManualAddPhotoPickerProps {
  photos: ManualPhotoDraft[];
  disabled?: boolean;
  onAddAssets: (assets: ImagePicker.ImagePickerAsset[]) => void | Promise<void>;
  onRemove: (id: string) => void;
}

export function ManualAddPhotoPicker({
  photos,
  disabled = false,
  onAddAssets,
  onRemove,
}: ManualAddPhotoPickerProps) {
  const remaining = MAX_PHOTOS - photos.length;

  const openChooser = () => {
    if (disabled) {
      return;
    }
    if (remaining <= 0) {
      Alert.alert('最多上传 3 张', '如果想换图，可以先删除已选照片。');
      return;
    }

    Alert.alert('添加照片', '选择图片来源', [
      { text: '拍照', onPress: pickFromCamera },
      { text: '相册', onPress: pickFromLibrary },
      { text: '取消', style: 'cancel' },
    ]);
  };

  const pickFromCamera = async () => {
    const permission = await ImagePicker.requestCameraPermissionsAsync();
    if (!permission.granted) {
      Alert.alert('没有相机权限', '请在系统设置中允许相机访问后再试');
      return;
    }

    const result = await ImagePicker.launchCameraAsync({
      mediaTypes: ['images'],
      quality: 0.85,
    });

    if (!result.canceled && result.assets.length > 0) {
      await onAddAssets(result.assets.slice(0, remaining));
    }
  };

  const pickFromLibrary = async () => {
    const permission = await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (!permission.granted) {
      Alert.alert('没有相册权限', '请在系统设置中允许相册访问后再试');
      return;
    }

    const result = await ImagePicker.launchImageLibraryAsync({
      mediaTypes: ['images'],
      quality: 0.85,
      allowsMultipleSelection: true,
      selectionLimit: remaining,
    });

    if (!result.canceled && result.assets.length > 0) {
      await onAddAssets(result.assets.slice(0, remaining));
    }
  };

  return (
    <View style={styles.card}>
      <View style={styles.header}>
        <View>
          <Text style={styles.title}>上传照片</Text>
          <Text style={styles.subtitle}>可选，最多 3 张，点保存时才上传</Text>
        </View>
        <View style={styles.countBadge}>
          <Text style={styles.countText}>
            {photos.length}/{MAX_PHOTOS}
          </Text>
        </View>
      </View>

      <View style={styles.grid}>
        {Array.from({ length: MAX_PHOTOS }, (_, index) => {
          const photo = photos[index];

          if (photo) {
            return (
              <View key={photo.id} style={styles.previewCard}>
                <Image source={{ uri: photo.uri }} style={styles.previewImage} />
                <View style={styles.previewFooter}>
                  <View style={styles.previewBadge}>
                    <Text style={styles.previewBadgeText}>{index + 1}</Text>
                  </View>
                  <Text style={[styles.statusText, photo.status === 'failed' && styles.statusTextFailed]}>
                    {formatPhotoStatus(photo.status)}
                  </Text>
                </View>
                <Pressable
                  accessibilityRole="button"
                  accessibilityLabel={`删除第 ${index + 1} 张照片`}
                  onPress={() => onRemove(photo.id)}
                  disabled={disabled}
                  style={({ pressed }) => [
                    styles.removeButton,
                    pressed && styles.removeButtonPressed,
                    disabled && styles.removeButtonDisabled,
                  ]}
                >
                  <Ionicons name="close" size={14} color="#172033" />
                </Pressable>
              </View>
            );
          }

          return (
            <Pressable
              key={`empty-${index}`}
              onPress={openChooser}
              style={({ pressed }) => [
                styles.emptySlot,
                pressed && styles.emptySlotPressed,
                (remaining <= 0 || disabled) && styles.emptySlotDisabled,
              ]}
              disabled={remaining <= 0 || disabled}
            >
              <View style={styles.emptyIconWrap}>
                <Ionicons name="add" size={20} color="#84CC16" />
              </View>
              <Text style={styles.emptyTitle}>添加照片</Text>
              <Text style={styles.emptyHint}>第 {index + 1} 张</Text>
            </Pressable>
          );
        })}
      </View>

      <View style={styles.footnoteRow}>
        <Ionicons name="sparkles-outline" size={14} color="#8B5E3C" />
        <Text style={styles.footnote}>支持拍照或从相册选择，不会触发识别。</Text>
      </View>
    </View>
  );
}

function formatPhotoStatus(status: ManualPhotoDraft['status']) {
  switch (status) {
    case 'uploading':
      return '上传中';
    case 'uploaded':
      return '已上传';
    case 'failed':
      return '上传失败';
    default:
      return '待保存';
  }
}

const styles = StyleSheet.create({
  card: {
    borderRadius: 24,
    padding: 18,
    marginBottom: 16,
    backgroundColor: 'rgba(255,255,255,0.86)',
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.92)',
    shadowColor: '#FFD4DB',
    shadowOffset: { width: 0, height: 8 },
    shadowOpacity: 0.1,
    shadowRadius: 20,
    elevation: 4,
  },
  header: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'flex-start',
    gap: 12,
    marginBottom: 14,
  },
  title: {
    fontSize: 17,
    fontWeight: '800',
    color: '#172033',
  },
  subtitle: {
    marginTop: 4,
    fontSize: 12,
    lineHeight: 18,
    color: '#6B7280',
  },
  countBadge: {
    minWidth: 54,
    borderRadius: 999,
    paddingHorizontal: 10,
    paddingVertical: 7,
    backgroundColor: '#FFF8EA',
    borderWidth: 1,
    borderColor: 'rgba(255,222,183,0.58)',
    alignItems: 'center',
  },
  countText: {
    fontSize: 12,
    fontWeight: '700',
    color: '#8B5E3C',
  },
  grid: {
    flexDirection: 'row',
    gap: 10,
    marginTop: 2,
  },
  emptySlot: {
    flex: 1,
    aspectRatio: 0.84,
    borderRadius: 22,
    borderWidth: 1.5,
    borderStyle: 'dashed',
    borderColor: 'rgba(173,255,47,0.42)',
    backgroundColor: '#FBFFF0',
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 8,
  },
  emptySlotPressed: {
    opacity: 0.84,
    transform: [{ scale: 0.98 }],
  },
  emptySlotDisabled: {
    opacity: 0.45,
  },
  emptyIconWrap: {
    width: 40,
    height: 40,
    borderRadius: 20,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: 'rgba(173,255,47,0.16)',
  },
  emptyTitle: {
    marginTop: 10,
    fontSize: 13,
    fontWeight: '700',
    color: '#172033',
  },
  emptyHint: {
    marginTop: 4,
    fontSize: 12,
    color: '#8E8E93',
  },
  previewCard: {
    flex: 1,
    aspectRatio: 0.84,
    borderRadius: 22,
    padding: 6,
    backgroundColor: '#FFFFFF',
    shadowColor: '#D1B9A5',
    shadowOffset: { width: 0, height: 8 },
    shadowOpacity: 0.12,
    shadowRadius: 18,
    elevation: 5,
  },
  previewImage: {
    width: '100%',
    height: '100%',
    borderRadius: 18,
    backgroundColor: '#F3F4F6',
  },
  previewFooter: {
    position: 'absolute',
    left: 14,
    bottom: 12,
    gap: 6,
  },
  previewBadge: {
    alignSelf: 'flex-start',
    borderRadius: 999,
    minWidth: 26,
    paddingHorizontal: 9,
    paddingVertical: 5,
    backgroundColor: 'rgba(255,255,255,0.9)',
    alignItems: 'center',
  },
  previewBadgeText: {
    fontSize: 11,
    fontWeight: '700',
    color: '#172033',
  },
  statusText: {
    alignSelf: 'flex-start',
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: 999,
    fontSize: 11,
    fontWeight: '700',
    color: '#3F6212',
    backgroundColor: 'rgba(236,252,203,0.92)',
  },
  statusTextFailed: {
    color: '#B42318',
    backgroundColor: 'rgba(254,228,226,0.95)',
  },
  removeButton: {
    position: 'absolute',
    top: 10,
    right: 10,
    width: 24,
    height: 24,
    borderRadius: 12,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: 'rgba(255,255,255,0.92)',
  },
  removeButtonPressed: {
    opacity: 0.8,
  },
  removeButtonDisabled: {
    opacity: 0.55,
  },
  footnote: {
    fontSize: 12,
    color: '#8E8E93',
    flex: 1,
    lineHeight: 18,
  },
  footnoteRow: {
    marginTop: 14,
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
  },
});
