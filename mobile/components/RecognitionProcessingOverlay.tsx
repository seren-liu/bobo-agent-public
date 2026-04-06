import { useEffect, useMemo, useRef, useState } from 'react';
import {
  Animated,
  Easing,
  Image,
  Pressable,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import { Ionicons } from '@expo/vector-icons';

type RecognitionStage = 'compressing' | 'uploading' | 'recognizing';

interface RecognitionProcessingOverlayProps {
  visible: boolean;
  sourceType: 'photo' | 'screenshot';
  stage: RecognitionStage;
  previewUri?: string | null;
}

const STAGE_COPY: Record<
  RecognitionStage,
  {
    eyebrow: string;
    title: string;
    description: string;
  }
> = {
  compressing: {
    eyebrow: 'STEP 1',
    title: '正在整理图片细节',
    description: '先压缩和清理图片，让后续上传更快也更稳定。',
  },
  uploading: {
    eyebrow: 'STEP 2',
    title: '正在安全上传',
    description: '图片已准备好，正在送去识别服务。',
  },
  recognizing: {
    eyebrow: 'STEP 3',
    title: 'AI 正在识别内容',
    description: '正在提取品牌、饮品名、甜度和价格信息。',
  },
};

const STAGE_ORDER: RecognitionStage[] = ['compressing', 'uploading', 'recognizing'];

export function RecognitionProcessingOverlay({
  visible,
  sourceType,
  stage,
  previewUri,
}: RecognitionProcessingOverlayProps) {
  const fade = useRef(new Animated.Value(0)).current;
  const cardLift = useRef(new Animated.Value(18)).current;
  const pulse = useRef(new Animated.Value(0)).current;
  const scan = useRef(new Animated.Value(0)).current;
  const [dots, setDots] = useState('');

  useEffect(() => {
    if (visible) {
      Animated.parallel([
        Animated.timing(fade, {
          toValue: 1,
          duration: 220,
          easing: Easing.out(Easing.cubic),
          useNativeDriver: true,
        }),
        Animated.spring(cardLift, {
          toValue: 0,
          damping: 18,
          stiffness: 180,
          mass: 0.9,
          useNativeDriver: true,
        }),
      ]).start();
    } else {
      Animated.parallel([
        Animated.timing(fade, {
          toValue: 0,
          duration: 180,
          useNativeDriver: true,
        }),
        Animated.timing(cardLift, {
          toValue: 18,
          duration: 180,
          useNativeDriver: true,
        }),
      ]).start();
    }
  }, [cardLift, fade, visible]);

  useEffect(() => {
    if (!visible) {
      pulse.stopAnimation();
      pulse.setValue(0);
      scan.stopAnimation();
      scan.setValue(0);
      setDots('');
      return;
    }

    const pulseLoop = Animated.loop(
      Animated.sequence([
        Animated.timing(pulse, {
          toValue: 1,
          duration: 1600,
          easing: Easing.inOut(Easing.ease),
          useNativeDriver: true,
        }),
        Animated.timing(pulse, {
          toValue: 0,
          duration: 1600,
          easing: Easing.inOut(Easing.ease),
          useNativeDriver: true,
        }),
      ])
    );

    const scanLoop = Animated.loop(
      Animated.sequence([
        Animated.timing(scan, {
          toValue: 1,
          duration: 1800,
          easing: Easing.inOut(Easing.quad),
          useNativeDriver: true,
        }),
        Animated.timing(scan, {
          toValue: 0,
          duration: 1800,
          easing: Easing.inOut(Easing.quad),
          useNativeDriver: true,
        }),
      ])
    );

    pulseLoop.start();
    scanLoop.start();
    const dotTimer = setInterval(() => {
      setDots((current) => (current.length >= 3 ? '' : `${current}.`));
    }, 360);

    return () => {
      pulseLoop.stop();
      scanLoop.stop();
      clearInterval(dotTimer);
      pulse.stopAnimation();
      scan.stopAnimation();
      setDots('');
    };
  }, [pulse, scan, visible]);

  const copy = STAGE_COPY[stage];
  const pulseScale = pulse.interpolate({
    inputRange: [0, 1],
    outputRange: [1, 1.08],
  });
  const pulseOpacity = pulse.interpolate({
    inputRange: [0, 1],
    outputRange: [0.14, 0.3],
  });
  const scanTranslate = scan.interpolate({
    inputRange: [0, 1],
    outputRange: [-64, 64],
  });

  const headline = useMemo(
    () => `${sourceType === 'photo' ? '照片' : '截图'}识别中`,
    [sourceType]
  );

  if (!visible) {
    return null;
  }

  return (
    <Animated.View style={[styles.portal, { opacity: fade }]}>
      <Pressable style={styles.backdrop} />
      <Animated.View
        style={[
          styles.cardWrap,
          {
            transform: [{ translateY: cardLift }],
          },
        ]}
      >
        <LinearGradient
          colors={['rgba(255,255,255,0.98)', 'rgba(255,246,243,0.96)', 'rgba(249,251,241,0.96)']}
          start={{ x: 0, y: 0 }}
          end={{ x: 1, y: 1 }}
          style={styles.card}
        >
          <View style={styles.topRow}>
            <View>
              <Text style={styles.eyebrow}>{copy.eyebrow}</Text>
              <Text style={styles.headline}>{headline}</Text>
            </View>
            <View style={styles.badge}>
              <Ionicons
                name={sourceType === 'photo' ? 'camera-outline' : 'cut-outline'}
                size={14}
                color="#355641"
              />
              <Text style={styles.badgeText}>
                {sourceType === 'photo' ? '拍照识别' : '截图识别'}
              </Text>
            </View>
          </View>

          <View style={styles.previewShell}>
            <Animated.View
              style={[
                styles.pulseRing,
                {
                  opacity: pulseOpacity,
                  transform: [{ scale: pulseScale }],
                },
              ]}
            />
            <LinearGradient
              colors={['#FFF4F6', '#FFF8E9']}
              start={{ x: 0, y: 0 }}
              end={{ x: 1, y: 1 }}
              style={styles.previewFrame}
            >
              {previewUri ? (
                <Image source={{ uri: previewUri }} style={styles.previewImage} resizeMode="cover" />
              ) : (
                <View style={styles.previewFallback}>
                  <Ionicons name="image-outline" size={30} color="#9CA3AF" />
                </View>
              )}

              <View style={styles.previewMask} />
              <Animated.View
                style={[
                  styles.scanLine,
                  {
                    transform: [{ translateY: scanTranslate }],
                  },
                ]}
              />
              <View style={styles.cornerTL} />
              <View style={styles.cornerTR} />
              <View style={styles.cornerBL} />
              <View style={styles.cornerBR} />
            </LinearGradient>
          </View>

          <View style={styles.copyBlock}>
            <Text style={styles.title}>{copy.title}</Text>
            <Text style={styles.description}>{copy.description}</Text>
          </View>

          <View style={styles.stageRow}>
            {STAGE_ORDER.map((item, index) => {
              const active = item === stage;
              const completed = STAGE_ORDER.indexOf(stage) > index;
              return (
                <View
                  key={item}
                  style={[
                    styles.stagePill,
                    active && styles.stagePillActive,
                    completed && styles.stagePillDone,
                  ]}
                >
                  <Text
                    style={[
                      styles.stageText,
                      active && styles.stageTextActive,
                      completed && styles.stageTextDone,
                    ]}
                  >
                    {item === 'compressing' ? '整理' : item === 'uploading' ? '上传' : '识别'}
                  </Text>
                </View>
              );
            })}
          </View>

          <View style={styles.footer}>
            <Text style={styles.footerText}>{`请稍等${dots}`}</Text>
            <Text style={styles.footerHint}>通常只需要几秒钟，完成后会自动进入确认页</Text>
          </View>
        </LinearGradient>
      </Animated.View>
    </Animated.View>
  );
}

const styles = StyleSheet.create({
  portal: {
    ...StyleSheet.absoluteFillObject,
    zIndex: 30,
    justifyContent: 'center',
    paddingHorizontal: 18,
  },
  backdrop: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: 'rgba(24, 27, 36, 0.22)',
  },
  cardWrap: {
    borderRadius: 30,
    shadowColor: '#F6A6B8',
    shadowOffset: { width: 0, height: 18 },
    shadowOpacity: 0.22,
    shadowRadius: 36,
    elevation: 12,
  },
  card: {
    borderRadius: 30,
    paddingHorizontal: 20,
    paddingVertical: 22,
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.92)',
    overflow: 'hidden',
  },
  topRow: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    justifyContent: 'space-between',
    gap: 12,
  },
  eyebrow: {
    fontSize: 11,
    fontWeight: '800',
    letterSpacing: 1.1,
    color: '#D07A8E',
  },
  headline: {
    marginTop: 6,
    fontSize: 24,
    fontWeight: '800',
    color: '#1C1C1E',
  },
  badge: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    borderRadius: 999,
    backgroundColor: 'rgba(216, 246, 193, 0.84)',
    paddingHorizontal: 10,
    paddingVertical: 7,
    borderWidth: 1,
    borderColor: 'rgba(123, 175, 22, 0.18)',
  },
  badgeText: {
    fontSize: 12,
    fontWeight: '700',
    color: '#355641',
  },
  previewShell: {
    marginTop: 18,
    alignItems: 'center',
    justifyContent: 'center',
  },
  pulseRing: {
    position: 'absolute',
    width: 196,
    height: 196,
    borderRadius: 999,
    backgroundColor: '#FFD7DE',
  },
  previewFrame: {
    width: 172,
    height: 172,
    borderRadius: 28,
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.88)',
    overflow: 'hidden',
    alignItems: 'center',
    justifyContent: 'center',
  },
  previewImage: {
    width: '100%',
    height: '100%',
  },
  previewFallback: {
    width: '100%',
    height: '100%',
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: 'rgba(255,255,255,0.75)',
  },
  previewMask: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: 'rgba(24, 28, 35, 0.08)',
  },
  scanLine: {
    position: 'absolute',
    left: 18,
    right: 18,
    top: 54,
    height: 3,
    borderRadius: 999,
    backgroundColor: '#D6FF72',
    shadowColor: '#D6FF72',
    shadowOffset: { width: 0, height: 0 },
    shadowOpacity: 0.9,
    shadowRadius: 10,
  },
  cornerTL: {
    position: 'absolute',
    top: 14,
    left: 14,
    width: 18,
    height: 18,
    borderTopWidth: 2.5,
    borderLeftWidth: 2.5,
    borderColor: '#FFFFFF',
    borderTopLeftRadius: 10,
  },
  cornerTR: {
    position: 'absolute',
    top: 14,
    right: 14,
    width: 18,
    height: 18,
    borderTopWidth: 2.5,
    borderRightWidth: 2.5,
    borderColor: '#FFFFFF',
    borderTopRightRadius: 10,
  },
  cornerBL: {
    position: 'absolute',
    bottom: 14,
    left: 14,
    width: 18,
    height: 18,
    borderBottomWidth: 2.5,
    borderLeftWidth: 2.5,
    borderColor: '#FFFFFF',
    borderBottomLeftRadius: 10,
  },
  cornerBR: {
    position: 'absolute',
    bottom: 14,
    right: 14,
    width: 18,
    height: 18,
    borderBottomWidth: 2.5,
    borderRightWidth: 2.5,
    borderColor: '#FFFFFF',
    borderBottomRightRadius: 10,
  },
  copyBlock: {
    marginTop: 18,
    alignItems: 'center',
  },
  title: {
    fontSize: 20,
    fontWeight: '800',
    color: '#1C1C1E',
    textAlign: 'center',
  },
  description: {
    marginTop: 8,
    fontSize: 13,
    lineHeight: 20,
    color: '#6B7280',
    textAlign: 'center',
    paddingHorizontal: 8,
  },
  stageRow: {
    marginTop: 18,
    flexDirection: 'row',
    gap: 8,
  },
  stagePill: {
    flex: 1,
    borderRadius: 16,
    paddingVertical: 10,
    alignItems: 'center',
    backgroundColor: 'rgba(255,255,255,0.66)',
    borderWidth: 1,
    borderColor: 'rgba(0,0,0,0.04)',
  },
  stagePillActive: {
    backgroundColor: '#1C1C1E',
    borderColor: '#1C1C1E',
  },
  stagePillDone: {
    backgroundColor: 'rgba(214,255,114,0.4)',
    borderColor: 'rgba(123,175,22,0.18)',
  },
  stageText: {
    fontSize: 12,
    fontWeight: '700',
    color: '#8E8E93',
  },
  stageTextActive: {
    color: '#FFFFFF',
  },
  stageTextDone: {
    color: '#355641',
  },
  footer: {
    marginTop: 18,
    alignItems: 'center',
  },
  footerText: {
    fontSize: 14,
    fontWeight: '700',
    color: '#374151',
    textAlign: 'center',
  },
  footerHint: {
    marginTop: 6,
    fontSize: 12,
    color: '#9CA3AF',
    textAlign: 'center',
  },
});
