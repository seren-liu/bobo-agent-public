import React from "react"
import { View, Text, StyleSheet } from "react-native"

interface PrefItem {
  label: string
  count: number
}

interface PreferenceBarProps {
  sugarData: PrefItem[]
  iceData: PrefItem[]
}

function SingleBar({ item, total, color }: { item: PrefItem; total: number; color: string }) {
  const pct = total > 0 ? (item.count / total) * 100 : 0
  return (
    <View style={barStyles.row}>
      <Text style={barStyles.label} numberOfLines={1}>
        {item.label}
      </Text>
      <View style={barStyles.track}>
        <View
          style={[
            barStyles.fill,
            { width: `${pct}%` as any, backgroundColor: color },
          ]}
        />
      </View>
      <Text style={barStyles.pct}>{pct.toFixed(0)}%</Text>
    </View>
  )
}

const barStyles = StyleSheet.create({
  row: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    marginBottom: 8,
  },
  label: {
    width: 52,
    fontSize: 12,
    fontWeight: "500",
    color: "#1C1C1E",
  },
  track: {
    flex: 1,
    height: 8,
    borderRadius: 4,
    backgroundColor: "rgba(0,0,0,0.06)",
    overflow: "hidden",
  },
  fill: {
    height: "100%",
    borderRadius: 4,
  },
  pct: {
    width: 34,
    fontSize: 11,
    fontWeight: "600",
    color: "#9CA3AF",
    textAlign: "right",
  },
})

export function PreferenceBar({ sugarData, iceData }: PreferenceBarProps) {
  const sugarTotal = sugarData.reduce((s, i) => s + i.count, 0)
  const iceTotal = iceData.reduce((s, i) => s + i.count, 0)

  return (
    <View style={styles.card}>
      <Text style={styles.title}>Your Preferences</Text>

      {/* Sugar preference */}
      <View style={styles.section}>
        <Text style={styles.sectionLabel}>Sugar Level</Text>
        {sugarData.map((item) => (
          <SingleBar
            key={item.label}
            item={item}
            total={sugarTotal}
            color="#ADFF2F"
          />
        ))}
      </View>

      {/* Ice preference */}
      <View style={styles.section}>
        <Text style={styles.sectionLabel}>Ice Level</Text>
        {iceData.map((item) => (
          <SingleBar
            key={item.label}
            item={item}
            total={iceTotal}
            color="#FFB7C5"
          />
        ))}
      </View>
    </View>
  )
}

const styles = StyleSheet.create({
  card: {
    borderRadius: 20,
    padding: 20,
    marginBottom: 24,
    backgroundColor: "rgba(255, 255, 255, 0.6)",
    borderWidth: 1,
    borderColor: "rgba(255, 255, 255, 0.8)",
    shadowColor: "#000",
    shadowOffset: { width: 0, height: 6 },
    shadowOpacity: 0.06,
    shadowRadius: 24,
    elevation: 3,
  },
  title: {
    fontSize: 16,
    fontWeight: "600",
    color: "#1C1C1E",
    marginBottom: 16,
  },
  section: {
    marginBottom: 16,
  },
  sectionLabel: {
    fontSize: 11,
    fontWeight: "600",
    color: "#9CA3AF",
    letterSpacing: 0.5,
    marginBottom: 10,
    textTransform: "uppercase",
  },
})
