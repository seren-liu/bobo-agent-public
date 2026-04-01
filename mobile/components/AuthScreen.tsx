import { useState } from 'react';
import {
  View,
  Text,
  TextInput,
  Pressable,
  StyleSheet,
  ScrollView,
  Alert,
  KeyboardAvoidingView,
  Platform,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { LinearGradient } from 'expo-linear-gradient';
import { Ionicons } from '@expo/vector-icons';

import { boboApi } from '@/lib/api';
import { useAuthStore } from '@/stores/authStore';
import { AppButton } from '@/components/AppButton';

export function AuthScreen() {
  const insets = useSafeAreaInsets();
  const setSession = useAuthStore((s) => s.setSession);

  const [isLogin, setIsLogin] = useState(true);
  const [showPassword, setShowPassword] = useState(false);
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [name, setName] = useState('');
  const [nickname, setNickname] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [loading, setLoading] = useState(false);

  const handleSubmit = async () => {
    if (!email.trim() || !password.trim()) {
      Alert.alert('Please fill in all fields');
      return;
    }

    if (!isLogin) {
      if (!name.trim()) {
        Alert.alert('Please enter your name');
        return;
      }
      if (password !== confirmPassword) {
        Alert.alert('Passwords do not match');
        return;
      }
    }

    setLoading(true);
    try {
      if (isLogin) {
        const { data } = await boboApi.login(email, password);
        setSession({
          accessToken: data.access_token,
          refreshToken: data.refresh_token,
          userId: data.user_id,
          nickname: data.nickname,
        });
      } else {
        const { data } = await boboApi.register(name, nickname, email, password);
        setSession({
          accessToken: data.access_token,
          refreshToken: data.refresh_token,
          userId: data.user_id,
          nickname: data.nickname,
        });
      }
    } catch (e: any) {
      const msg =
        e?.response?.data?.detail ??
        (e instanceof Error ? e.message : 'Please try again');
      Alert.alert(isLogin ? 'Login Failed' : 'Registration Failed', msg);
    } finally {
      setLoading(false);
    }
  };

  return (
    <View style={[styles.container, { paddingTop: insets.top }]}>
      <KeyboardAvoidingView
        behavior={Platform.OS === 'ios' ? 'padding' : 'height'}
        style={{ flex: 1 }}
      >
        <ScrollView
          contentContainerStyle={styles.scroll}
          showsVerticalScrollIndicator={false}
          keyboardShouldPersistTaps="handled"
        >
          {/* Hero */}
          <View style={styles.heroSection}>
            <LinearGradient
              colors={['#FFB7C5', '#FFA5B4']}
              style={styles.heroAvatar}
            >
              <Text style={styles.heroAvatarText}>
                {isLogin ? 'S' : '+'}
              </Text>
            </LinearGradient>
            <Text style={styles.heroTitle}>
              {isLogin ? 'Welcome Back' : 'Create Account'}
            </Text>
            <Text style={styles.heroSubtitle}>
              {isLogin
                ? 'Sign in to track your drinks'
                : 'Join Bobo to start tracking'}
            </Text>
          </View>

          {/* Form */}
          <View style={styles.form}>
            {/* Name field (register only) */}
            {!isLogin && (
              <View style={styles.inputCard}>
                <View style={styles.inputRow}>
                  <Ionicons name="person-outline" size={20} color="#8E8E93" />
                  <TextInput
                    style={styles.input}
                    placeholder="Your name"
                    placeholderTextColor="#8E8E93"
                    value={name}
                    onChangeText={setName}
                    autoCapitalize="words"
                  />
                </View>
              </View>
            )}

            {/* Nickname field (register only) */}
            {!isLogin && (
              <View style={styles.inputCard}>
                <View style={styles.inputRow}>
                  <Ionicons name="happy-outline" size={20} color="#8E8E93" />
                  <TextInput
                    style={styles.input}
                    placeholder="Nickname (displayed on Home)"
                    placeholderTextColor="#8E8E93"
                    value={nickname}
                    onChangeText={setNickname}
                    autoCapitalize="none"
                  />
                </View>
              </View>
            )}

            {/* Email */}
            <View style={styles.inputCard}>
              <View style={styles.inputRow}>
                <Ionicons name="mail-outline" size={20} color="#8E8E93" />
                <TextInput
                  style={styles.input}
                  placeholder="Email address"
                  placeholderTextColor="#8E8E93"
                  value={email}
                  onChangeText={setEmail}
                  keyboardType="email-address"
                  autoCapitalize="none"
                  autoCorrect={false}
                />
              </View>
            </View>

            {/* Password */}
            <View style={styles.inputCard}>
              <View style={styles.inputRow}>
                <Ionicons
                  name="lock-closed-outline"
                  size={20}
                  color="#8E8E93"
                />
                <TextInput
                  style={styles.input}
                  placeholder="Password"
                  placeholderTextColor="#8E8E93"
                  value={password}
                  onChangeText={setPassword}
                  secureTextEntry={!showPassword}
                  autoCapitalize="none"
                />
                <Pressable
                  onPress={() => setShowPassword(!showPassword)}
                  hitSlop={8}
                >
                  <Ionicons
                    name={showPassword ? 'eye-off-outline' : 'eye-outline'}
                    size={20}
                    color="#8E8E93"
                  />
                </Pressable>
              </View>
            </View>

            {/* Confirm Password (register only) */}
            {!isLogin && (
              <View style={styles.inputCard}>
                <View style={styles.inputRow}>
                  <Ionicons
                    name="lock-closed-outline"
                    size={20}
                    color="#8E8E93"
                  />
                  <TextInput
                    style={styles.input}
                    placeholder="Confirm password"
                    placeholderTextColor="#8E8E93"
                    value={confirmPassword}
                    onChangeText={setConfirmPassword}
                    secureTextEntry={!showPassword}
                    autoCapitalize="none"
                  />
                </View>
              </View>
            )}

            {/* Forgot password */}
            {isLogin && (
              <Pressable style={styles.forgotBtn}>
                <Text style={styles.forgotText}>Forgot password?</Text>
              </Pressable>
            )}

            {/* Submit */}
            <AppButton
              label={isLogin ? 'Sign In' : 'Create Account'}
              onPress={handleSubmit}
              loading={loading}
              style={styles.submitBtn}
            />
          </View>

          {/* Switch mode */}
          <View style={styles.switchRow}>
            <Text style={styles.switchText}>
              {isLogin
                ? "Don't have an account? "
                : 'Already have an account? '}
            </Text>
            <Pressable onPress={() => setIsLogin(!isLogin)}>
              <Text style={styles.switchLink}>
                {isLogin ? 'Sign Up' : 'Sign In'}
              </Text>
            </Pressable>
          </View>
        </ScrollView>
      </KeyboardAvoidingView>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#FAFAFA',
  },
  scroll: {
    flexGrow: 1,
    justifyContent: 'center',
    paddingHorizontal: 20,
    paddingBottom: 85,
  },

  // Hero
  heroSection: {
    alignItems: 'center',
    marginBottom: 32,
  },
  heroAvatar: {
    width: 80,
    height: 80,
    borderRadius: 40,
    alignItems: 'center',
    justifyContent: 'center',
    marginBottom: 16,
    shadowColor: '#FFB7C5',
    shadowOffset: { width: 0, height: 8 },
    shadowOpacity: 0.4,
    shadowRadius: 16,
    elevation: 8,
  },
  heroAvatarText: {
    fontSize: 30,
    fontWeight: '700',
    color: '#fff',
  },
  heroTitle: {
    fontSize: 22,
    fontWeight: '800',
    color: '#1C1C1E',
    marginBottom: 6,
  },
  heroSubtitle: {
    fontSize: 14,
    color: '#8E8E93',
  },

  // Form
  form: {
    gap: 12,
  },
  inputCard: {
    backgroundColor: 'rgba(255,255,255,0.85)',
    borderRadius: 16,
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.9)',
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.06,
    shadowRadius: 8,
    elevation: 2,
  },
  inputRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 12,
    paddingHorizontal: 16,
    paddingVertical: 14,
  },
  input: {
    flex: 1,
    fontSize: 14,
    color: '#1C1C1E',
  },

  forgotBtn: {
    alignSelf: 'flex-end',
    marginTop: -4,
  },
  forgotText: {
    fontSize: 13,
    color: '#8E8E93',
  },

  submitBtn: {
    marginTop: 4,
  },

  // Switch mode
  switchRow: {
    flexDirection: 'row',
    justifyContent: 'center',
    marginTop: 28,
  },
  switchText: {
    fontSize: 14,
    color: '#8E8E93',
  },
  switchLink: {
    fontSize: 14,
    fontWeight: '700',
    color: '#1C1C1E',
  },
});
