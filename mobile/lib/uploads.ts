import * as ImagePicker from 'expo-image-picker';

import { boboApi } from '@/lib/api';
import { compressBeforeUpload, type UploadCompressionProfile } from '@/lib/imageCompression';

export type UploadablePhotoAsset = {
  uri: string;
  mimeType?: string | null;
  fileName?: string | null;
  width?: number | null;
  height?: number | null;
  fileSize?: number | null;
};

export function buildManualPhotoFilename(asset: UploadablePhotoAsset) {
  const mimeType = asset.mimeType || 'image/jpeg';
  const extFromName = asset.fileName?.split('.').pop()?.toLowerCase();
  const extFromMime = mimeType.includes('png')
    ? 'png'
    : mimeType.includes('webp')
      ? 'webp'
      : mimeType.includes('heic')
        ? 'heic'
        : 'jpg';
  const ext = extFromName && /^[a-z0-9]+$/.test(extFromName) ? extFromName : extFromMime;

  return {
    mimeType,
    filename: `bobo-${Date.now()}-${Math.random().toString(36).slice(2, 8)}.${ext}`,
  };
}

export async function uploadImageAsset(
  asset: UploadablePhotoAsset,
  options: {
    profile: UploadCompressionProfile;
    sourceType: 'photo' | 'screenshot' | 'manual';
    onStageChange?: (stage: 'compressing' | 'uploading') => void;
  }
) {
  options.onStageChange?.('compressing');
  const prepared = await compressBeforeUpload({
    uri: asset.uri,
    mimeType: asset.mimeType,
    fileName: asset.fileName,
    width: asset.width,
    height: asset.height,
    fileSize: asset.fileSize,
    profile: options.profile,
  });

  try {
    const { filename, mimeType } = buildManualPhotoFilename({
      uri: prepared.uri,
      mimeType: prepared.mimeType,
      fileName: prepared.fileName,
    });
    const upload = await boboApi.getUploadUrl({
      filename,
      contentType: mimeType,
      fileSize: prepared.size,
      width: prepared.width,
      height: prepared.height,
      sourceType: options.sourceType,
    });
    options.onStageChange?.('uploading');
    const imageResponse = await fetch(prepared.uri);
    const blob = await imageResponse.blob();
    const putResp = await fetch(upload.data.upload_url, {
      method: 'PUT',
      headers: { 'Content-Type': mimeType },
      body: blob,
    });

    if (!putResp.ok) {
      throw new Error(`upload failed (${putResp.status})`);
    }

    return upload.data.file_url;
  } finally {
    await prepared.cleanup?.();
  }
}

export function mapPickerAssetToUploadable(asset: ImagePicker.ImagePickerAsset): UploadablePhotoAsset {
  return {
    uri: asset.uri,
    mimeType: asset.mimeType,
    fileName: asset.fileName,
    width: asset.width,
    height: asset.height,
    fileSize: asset.fileSize,
  };
}
