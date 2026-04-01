import React from "react"
import { View, Text, StyleSheet } from "react-native"
import { CartesianChart, Bar } from "victory-native"
import { LinearGradient, vec } from "@shopify/react-native-skia"

interface WeekItem {
  week: string
  count: number
}

interface WeeklyTrendChartProps {
  data: WeekItem[]
}

export function WeeklyTrendChart({ data }: WeeklyTrendChartProps) {
  // victory-native CartesianChart requires numeric x keys
  const chartData = data.map((item, index) => ({
    x: index,
    y: item.count,
    label: item.week,
  }))

  return (
    <View style={styles.card}>
      <Text style={styles.title}>Weekly Trend</Text>

      <View style={styles.chartWrapper}>
        <CartesianChart
          data={chartData}
          xKey="x"
          yKeys={["y"]}
          domainPadding={{ left: 8, right: 8, top: 16 }}
          axisOptions={{
            font: null,
            tickCount: { x: data.length, y: 4 },
            lineColor: "rgba(0,0,0,0.05)",
            labelColor: "#9CA3AF",
          }}
        >
          {({ points, chartBounds }) => (
            <Bar
              points={points.y}
              chartBounds={chartBounds}
              roundedCorners={{ topLeft: 6, topRight: 6 }}
              color="#ADFF2F"
            >
              <LinearGradient
                start={vec(0, 0)}
                end={vec(0, chartBounds.bottom)}
                colors={["#ADFF2F", "rgba(173,255,47,0.5)"]}
              />
            </Bar>
          )}
        </CartesianChart>
      </View>

      {/* Week labels */}
      <View style={styles.labelsRow}>
        {data.map((item) => (
          <Text key={item.week} style={styles.weekLabel}>
            {item.week}
          </Text>
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
    marginBottom: 12,
  },
  chartWrapper: {
    height: 140,
  },
  labelsRow: {
    flexDirection: "row",
    justifyContent: "space-around",
    marginTop: 4,
  },
  weekLabel: {
    fontSize: 10,
    fontWeight: "600",
    color: "#9CA3AF",
    flex: 1,
    textAlign: "center",
  },
})
