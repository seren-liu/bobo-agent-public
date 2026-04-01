import type { ReactNode } from 'react';
import {
  ActivityIndicator,
  Pressable,
  StyleSheet,
  Text,
  View,
  type StyleProp,
  type TextStyle,
  type ViewStyle,
} from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';

type AppButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger';

interface AppButtonProps {
  label: string;
  onPress?: () => void;
  disabled?: boolean;
  loading?: boolean;
  variant?: AppButtonVariant;
  style?: StyleProp<ViewStyle>;
  textStyle?: StyleProp<TextStyle>;
  leftSlot?: ReactNode;
}

const VARIANT_TOKENS: Record<
  AppButtonVariant,
  {
    gradient: readonly [string, string];
    text: string;
    border: string;
    shadowColor: string;
    containerStyle?: ViewStyle;
  }
> = {
  primary: {
    gradient: ['#D6FF72', '#ADFF2F'],
    text: '#172033',
    border: 'rgba(154, 230, 0, 0.72)',
    shadowColor: '#B7EE52',
  },
  secondary: {
    gradient: ['rgba(255,255,255,0.98)', 'rgba(255,244,247,0.94)'],
    text: '#172033',
    border: 'rgba(255,183,197,0.68)',
    shadowColor: '#FFB7C5',
  },
  ghost: {
    gradient: ['rgba(255,255,255,0.7)', 'rgba(255,255,255,0.58)'],
    text: '#4B5563',
    border: 'rgba(255,255,255,0.78)',
    shadowColor: 'transparent',
    containerStyle: {
      shadowOpacity: 0,
      elevation: 0,
    },
  },
  danger: {
    gradient: ['#FFF1F3', '#FFE4E8'],
    text: '#9F1239',
    border: 'rgba(251, 113, 133, 0.36)',
    shadowColor: '#FDA4AF',
  },
};

export function AppButton({
  label,
  onPress,
  disabled,
  loading,
  variant = 'primary',
  style,
  textStyle,
  leftSlot,
}: AppButtonProps) {
  const tokens = VARIANT_TOKENS[variant];
  const isDisabled = disabled || loading;

  return (
    <Pressable
      accessibilityRole="button"
      disabled={isDisabled}
      onPress={onPress}
      style={({ pressed }) => [
        styles.pressable,
        style,
        pressed && !isDisabled ? styles.pressed : null,
        isDisabled ? styles.disabled : null,
      ]}
    >
      <LinearGradient
        colors={tokens.gradient as [string, string]}
        start={{ x: 0, y: 0 }}
        end={{ x: 1, y: 1 }}
        style={[
          styles.surface,
          {
            borderColor: tokens.border,
            shadowColor: tokens.shadowColor,
          },
          tokens.containerStyle,
        ]}
      >
        <View style={styles.content}>
          {loading ? (
            <ActivityIndicator color={tokens.text} />
          ) : (
            <>
              {leftSlot ? <View style={styles.leftSlot}>{leftSlot}</View> : null}
              <Text style={[styles.label, { color: tokens.text }, textStyle]}>{label}</Text>
            </>
          )}
        </View>
      </LinearGradient>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  pressable: {
    borderRadius: 18,
  },
  surface: {
    minHeight: 54,
    borderRadius: 18,
    borderWidth: 1,
    paddingHorizontal: 18,
    shadowOffset: { width: 0, height: 10 },
    shadowOpacity: 0.18,
    shadowRadius: 18,
    elevation: 7,
  },
  content: {
    minHeight: 52,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
  },
  leftSlot: {
    marginRight: 8,
  },
  label: {
    fontSize: 15,
    fontWeight: '800',
    letterSpacing: 0.2,
  },
  pressed: {
    transform: [{ scale: 0.985 }],
  },
  disabled: {
    opacity: 0.62,
  },
});
