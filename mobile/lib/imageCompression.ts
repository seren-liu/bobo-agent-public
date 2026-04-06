import { File } from 'expo-file-system';
import { ImageManipulator, SaveFormat } from 'expo-image-manipulator';

export type UploadCompressionProfile =
  | 'recognition-photo'
  | 'recognition-screenshot'
  | 'manual-gallery';

export type PreparedUploadAsset = {
  uri: string;
  mimeType: string;
  fileName: string;
  size: number;
  width: number;
  height: number;
  cleanup?: () => Promise<void>;
};

type CompressionInput = {
  uri: string;
  mimeType?: string | null;
  fileName?: string | null;
  width?: number | null;
  height?: number | null;
  fileSize?: number | null;
  profile: UploadCompressionProfile;
};

type CompressionPlan = {
  maxLongEdge: number;
  preferredFormat: 'jpeg' | 'png';
  qualitySteps: number[];
  targetMaxBytes: number;
  rawFallbackLimitBytes: number;
};

type RenderedCandidate = {
  uri: string;
  size: number;
  width: number;
  height: number;
  format: 'jpeg' | 'png';
};

const MB = 1024 * 1024;

const PROFILE_PLANS: Record<UploadCompressionProfile, CompressionPlan> = {
  'recognition-photo': {
    maxLongEdge: 1920,
    preferredFormat: 'jpeg',
    qualitySteps: [0.82, 0.74, 0.66, 0.58],
    targetMaxBytes: 900 * 1024,
    rawFallbackLimitBytes: 12 * MB,
  },
  'recognition-screenshot': {
    maxLongEdge: 2200,
    preferredFormat: 'png',
    qualitySteps: [0.9, 0.84, 0.78],
    targetMaxBytes: 1200 * 1024,
    rawFallbackLimitBytes: 10 * MB,
  },
  'manual-gallery': {
    maxLongEdge: 1440,
    preferredFormat: 'jpeg',
    qualitySteps: [0.76, 0.68, 0.6],
    targetMaxBytes: 600 * 1024,
    rawFallbackLimitBytes: 8 * MB,
  },
};

export async function compressBeforeUpload(input: CompressionInput): Promise<PreparedUploadAsset> {
  const original = resolveOriginalInfo(input);
  const plan = PROFILE_PLANS[input.profile];
  const cleanupUris = new Set<string>();

  try {
    if (!needsCompression(original, plan, input.profile)) {
      return {
        uri: input.uri,
        mimeType: original.mimeType,
        fileName: ensureExtension(input.fileName, extensionForMime(original.mimeType)),
        size: original.size,
        width: original.width,
        height: original.height,
      };
    }

    const scaled = scaleDimension(original.width, original.height, plan.maxLongEdge);
    const candidates = await buildCandidates({
      input,
      original,
      plan,
      scaled,
      cleanupUris,
    });

    const best = chooseBestCandidate(candidates, plan.targetMaxBytes);
    return {
      uri: best.uri,
      mimeType: mimeForFormat(best.format),
      fileName: ensureExtension(input.fileName, best.format === 'png' ? 'png' : 'jpg'),
      size: best.size,
      width: best.width,
      height: best.height,
      cleanup: createCleanup(cleanupUris, input.uri),
    };
  } catch {
    await createCleanup(cleanupUris, input.uri)();
    if (original.size > plan.rawFallbackLimitBytes) {
      throw new Error('图片过大，建议重新拍摄或裁剪后再试');
    }
    return {
      uri: input.uri,
      mimeType: original.mimeType,
      fileName: ensureExtension(input.fileName, extensionForMime(original.mimeType)),
      size: original.size,
      width: original.width,
      height: original.height,
    };
  }
}

function resolveOriginalInfo(input: CompressionInput) {
  const info = new File(input.uri).info();
  return {
    size: input.fileSize ?? info.size ?? 0,
    width: normalizeDimension(input.width),
    height: normalizeDimension(input.height),
    mimeType: normalizeMimeType(input.mimeType),
  };
}

function normalizeDimension(value?: number | null) {
  return value && Number.isFinite(value) && value > 0 ? Math.round(value) : 0;
}

function normalizeMimeType(mimeType?: string | null) {
  const normalized = mimeType?.toLowerCase();
  if (!normalized || normalized === 'image/jpg') {
    return 'image/jpeg';
  }
  return normalized;
}

function needsCompression(
  original: { size: number; width: number; height: number; mimeType: string },
  plan: CompressionPlan,
  profile: UploadCompressionProfile
) {
  const longEdge = Math.max(original.width, original.height);
  if (!longEdge) {
    return true;
  }
  if (original.size > plan.targetMaxBytes) {
    return true;
  }
  if (longEdge > plan.maxLongEdge) {
    return true;
  }
  if (profile === 'recognition-screenshot' && original.mimeType === 'image/png') {
    return false;
  }
  if (profile === 'recognition-photo' || profile === 'manual-gallery') {
    return original.mimeType !== 'image/jpeg';
  }
  return false;
}

async function buildCandidates({
  input,
  original,
  plan,
  scaled,
  cleanupUris,
}: {
  input: CompressionInput;
  original: { size: number; width: number; height: number; mimeType: string };
  plan: CompressionPlan;
  scaled: { width: number; height: number };
  cleanupUris: Set<string>;
}) {
  const candidates: RenderedCandidate[] = [];
  const preferPng = input.profile === 'recognition-screenshot' && original.mimeType === 'image/png';

  if (preferPng) {
    const pngCandidate = await renderVariant({
      sourceUri: input.uri,
      targetWidth: scaled.width,
      targetHeight: scaled.height,
      format: 'png',
      compress: 1,
    });
    cleanupUris.add(pngCandidate.uri);
    candidates.push(pngCandidate);
    if (pngCandidate.size <= plan.targetMaxBytes) {
      return candidates;
    }
  }

  for (const quality of plan.qualitySteps) {
    const jpegCandidate = await renderVariant({
      sourceUri: input.uri,
      targetWidth: scaled.width,
      targetHeight: scaled.height,
      format: plan.preferredFormat === 'png' ? 'jpeg' : plan.preferredFormat,
      compress: quality,
    });
    cleanupUris.add(jpegCandidate.uri);
    candidates.push(jpegCandidate);
    if (jpegCandidate.size <= plan.targetMaxBytes) {
      break;
    }
  }

  return candidates;
}

async function renderVariant({
  sourceUri,
  targetWidth,
  targetHeight,
  format,
  compress,
}: {
  sourceUri: string;
  targetWidth: number;
  targetHeight: number;
  format: 'jpeg' | 'png';
  compress: number;
}): Promise<RenderedCandidate> {
  const context = ImageManipulator.manipulate(sourceUri);
  if (targetWidth > 0 && targetHeight > 0) {
    context.resize({ width: targetWidth, height: targetHeight });
  }
  const rendered = await context.renderAsync();
  const saved = await rendered.saveAsync({
    compress,
    format: format === 'png' ? SaveFormat.PNG : SaveFormat.JPEG,
  });
  const info = new File(saved.uri).info();

  return {
    uri: saved.uri,
    size: info.size ?? 0,
    width: saved.width,
    height: saved.height,
    format,
  };
}

function scaleDimension(width: number, height: number, maxLongEdge: number) {
  if (!width || !height) {
    return { width, height };
  }
  const longEdge = Math.max(width, height);
  if (longEdge <= maxLongEdge) {
    return { width, height };
  }
  const scale = maxLongEdge / longEdge;
  return {
    width: Math.max(1, Math.round(width * scale)),
    height: Math.max(1, Math.round(height * scale)),
  };
}

function chooseBestCandidate(candidates: RenderedCandidate[], targetMaxBytes: number) {
  const acceptable = candidates.filter((candidate) => candidate.size > 0 && candidate.size <= targetMaxBytes);
  if (acceptable.length) {
    return [...acceptable].sort((left, right) => right.size - left.size)[0]!;
  }
  return [...candidates].sort((left, right) => left.size - right.size)[0]!;
}

function mimeForFormat(format: 'jpeg' | 'png') {
  return format === 'png' ? 'image/png' : 'image/jpeg';
}

function extensionForMime(mimeType: string) {
  switch (mimeType) {
    case 'image/png':
      return 'png';
    case 'image/webp':
      return 'webp';
    case 'image/heic':
      return 'heic';
    case 'image/heif':
      return 'heif';
    default:
      return 'jpg';
  }
}

function ensureExtension(fileName: string | null | undefined, ext: string) {
  const base =
    fileName?.replace(/\.[^.]+$/, '') || `bobo-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
  return `${base}.${ext}`;
}

function createCleanup(cleanupUris: Set<string>, originalUri: string) {
  return async () => {
    for (const uri of cleanupUris) {
      if (!uri || uri === originalUri) {
        continue;
      }
      try {
        new File(uri).delete();
      } catch {
        // Ignore cache cleanup failures.
      }
    }
  };
}
