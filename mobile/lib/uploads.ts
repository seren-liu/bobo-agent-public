import * as ImagePicker from 'expo-image-picker';

import { boboApi } from '@/lib/api';

export type UploadablePhotoAsset = {
  uri: string;
  mimeType?: string | null;
  fileName?: string | null;
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

export async function uploadImageAsset(asset: UploadablePhotoAsset) {
  const { filename, mimeType } = buildManualPhotoFilename(asset);
  const upload = await boboApi.getUploadUrl(filename, mimeType);
  const imageResponse = await fetch(asset.uri);
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
}

export function mapPickerAssetToUploadable(asset: ImagePicker.ImagePickerAsset): UploadablePhotoAsset {
  return {
    uri: asset.uri,
    mimeType: asset.mimeType,
    fileName: asset.fileName,
  };
}
