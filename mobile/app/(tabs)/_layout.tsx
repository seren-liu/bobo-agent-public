import { Tabs } from 'expo-router';
import { View, Text, Pressable, StyleSheet } from 'react-native';
import type { BottomTabBarProps } from '@react-navigation/bottom-tabs';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Ionicons } from '@expo/vector-icons';

// ─── Tab definition ──────────────────────────────────────────────────────────

type TabConfig = {
  name: string;
  label: string;
  icon: keyof typeof Ionicons.glyphMap;
  iconActive: keyof typeof Ionicons.glyphMap;
};

const TABS: TabConfig[] = [
  { name: 'index', label: 'Home', icon: 'home-outline', iconActive: 'home' },
  { name: 'calendar', label: 'Calendar', icon: 'calendar-outline', iconActive: 'calendar' },
  { name: 'stats', label: 'Stats', icon: 'bar-chart-outline', iconActive: 'bar-chart' },
  { name: 'ai', label: 'AI Chat', icon: 'sparkles-outline', iconActive: 'sparkles' },
];

// ─── Floating pill tab bar ────────────────────────────────────────────────────

function FloatingTabBar({ state, descriptors, navigation }: BottomTabBarProps) {
  const insets = useSafeAreaInsets();

  return (
    <View style={[styles.tabBarWrapper, { paddingBottom: insets.bottom + 8 }]}>
      <View style={styles.tabBar}>
        {state.routes.map((route, index) => {
          const tab = TABS[index];
          const isActive = state.index === index;
          const { options } = descriptors[route.key];
          const label = options.tabBarLabel ?? tab?.label ?? route.name;

          const onPress = () => {
            const event = navigation.emit({
              type: 'tabPress',
              target: route.key,
              canPreventDefault: true,
            });
            if (!isActive && !event.defaultPrevented) {
              navigation.navigate(route.name);
            }
          };

          return (
            <Pressable
              key={route.key}
              onPress={onPress}
              style={[styles.tabItem, isActive && styles.tabItemActive]}
              accessibilityLabel={String(label)}
            >
              <Ionicons
                name={isActive ? tab!.iconActive : tab!.icon}
                size={22}
                color={isActive ? '#ADFF2F' : '#8E8E93'}
                style={{ marginBottom: 2 }}
              />
              <Text
                style={[
                  styles.tabLabel,
                  { color: isActive ? '#ADFF2F' : '#8E8E93' },
                ]}
              >
                {String(label)}
              </Text>
              {isActive && <View style={styles.activeDot} />}
            </Pressable>
          );
        })}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  tabBarWrapper: {
    position: 'absolute',
    bottom: 0,
    left: 0,
    right: 0,
    alignItems: 'center',
    paddingHorizontal: 20,
    pointerEvents: 'box-none',
  },
  tabBar: {
    flexDirection: 'row',
    width: '100%',
    maxWidth: 340,
    height: 68,
    backgroundColor: 'rgba(255,255,255,0.92)',
    borderRadius: 34,
    alignItems: 'center',
    justifyContent: 'space-around',
    paddingHorizontal: 8,
    // shadow
    shadowColor: '#000',
    shadowOffset: { width: 0, height: 8 },
    shadowOpacity: 0.12,
    shadowRadius: 24,
    elevation: 12,
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.8)',
  },
  tabItem: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    paddingVertical: 8,
    borderRadius: 20,
    position: 'relative',
  },
  tabItemActive: {
    backgroundColor: 'rgba(255,255,255,0.6)',
  },
  tabLabel: {
    fontSize: 10,
    fontWeight: '600',
    letterSpacing: 0.2,
  },
  activeDot: {
    position: 'absolute',
    bottom: 2,
    width: 4,
    height: 4,
    borderRadius: 2,
    backgroundColor: '#ADFF2F',
  },
});

// ─── Layout ───────────────────────────────────────────────────────────────────

export default function TabLayout() {
  return (
    <Tabs
      tabBar={(props) => <FloatingTabBar {...props} />}
      screenOptions={{ headerShown: false }}
    >
      <Tabs.Screen name="index" options={{ title: 'Home' }} />
      <Tabs.Screen name="calendar" options={{ title: 'Calendar' }} />
      <Tabs.Screen name="stats" options={{ title: 'Stats' }} />
      <Tabs.Screen name="ai" options={{ title: 'AI Chat' }} />
    </Tabs>
  );
}
