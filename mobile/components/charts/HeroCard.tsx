import React from "react"
import { View, Text, StyleSheet } from "react-native"

interface HeroCardProps {
  totalAmount: number
  totalCount: number
  avgWeeklyCount: number
  avgWeeklyAmount: number
}

export function HeroCard({
  totalAmount,
  totalCount,
  avgWeeklyCount,
  avgWeeklyAmount,
}: HeroCardProps) {
  const amount = Number(totalAmount) || 0
  const weeklyCount = Number(avgWeeklyCount) || 0
  const weeklyAmount = Number(avgWeeklyAmount) || 0

  return (
    <View style={styles.row}>
      {/* Primary card — this month */}
      <View style={[styles.card, styles.cardStrong]}>
        <Text style={styles.label}>THIS MONTH</Text>
        <Text style={styles.primaryValue}>{totalCount ?? 0} cups</Text>
        <Text style={styles.amountValue}>¥{amount.toFixed(0)}</Text>
      </View>

      {/* Secondary card — avg per week */}
      <View style={[styles.card, styles.cardNormal]}>
        <Text style={styles.label}>AVG PER WEEK</Text>
        <Text style={styles.primaryValue}>{weeklyCount.toFixed(1)} cups</Text>
        <Text style={styles.amountMuted}>~¥{weeklyAmount.toFixed(0)}</Text>
      </View>
    </View>
  )
}

const styles = StyleSheet.create({
  row: {
    flexDirection: "row",
    gap: 12,
    marginBottom: 24,
  },
  card: {
    flex: 1,
    borderRadius: 20,
    padding: 16,
    borderWidth: 1,
  },
  cardStrong: {
    backgroundColor: "rgba(255, 255, 255, 0.75)",
    borderColor: "rgba(255, 255, 255, 0.9)",
    shadowColor: "#000",
    shadowOffset: { width: 0, height: 8 },
    shadowOpacity: 0.08,
    shadowRadius: 24,
    elevation: 4,
  },
  cardNormal: {
    backgroundColor: "rgba(255, 255, 255, 0.6)",
    borderColor: "rgba(255, 255, 255, 0.8)",
    shadowColor: "#000",
    shadowOffset: { width: 0, height: 6 },
    shadowOpacity: 0.06,
    shadowRadius: 20,
    elevation: 3,
  },
  label: {
    fontSize: 10,
    color: "#9CA3AF",
    fontWeight: "600",
    letterSpacing: 0.8,
    marginBottom: 4,
  },
  primaryValue: {
    fontSize: 24,
    fontWeight: "700",
    color: "#1C1C1E",
    marginBottom: 2,
  },
  amountValue: {
    fontSize: 14,
    fontWeight: "700",
    color: "#1C1C1E",
  },
  amountMuted: {
    fontSize: 14,
    fontWeight: "500",
    color: "#9CA3AF",
  },
})
