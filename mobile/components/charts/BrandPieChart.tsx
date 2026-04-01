import React from "react"
import { View, Text, StyleSheet } from "react-native"
import { PolarChart, Pie } from "victory-native"

interface BrandItem {
  brand: string
  count: number
  pct: number
}

interface BrandPieChartProps {
  data: BrandItem[]
}

// Map brand names to design-system colors
const BRAND_COLORS: Record<string, string> = {
  HEYTEA: "#ADFF2F",
  喜茶: "#ADFF2F",
  COCO: "#FFB7C5",
  NAYUKI: "#FFA5B4",
  奈雪: "#FFA5B4",
  MIXUE: "#FEF3C7",
  蜜雪冰城: "#FEF3C7",
  CHAGEE: "#DCFCE7",
  霸王茶姬: "#DCFCE7",
  Others: "#E5E7EB",
}

function getBrandColor(brand: string, index: number): string {
  if (BRAND_COLORS[brand]) return BRAND_COLORS[brand]
  const fallbacks = ["#E0E7FF", "#FCE7F3", "#FED7AA", "#D1FAE5", "#E5E7EB"]
  return fallbacks[index % fallbacks.length]
}

export function BrandPieChart({ data }: BrandPieChartProps) {
  const pieData = data.map((item, i) => ({
    label: item.brand,
    value: item.pct,
    color: getBrandColor(item.brand, i),
  }))

  return (
    <View style={styles.card}>
      <Text style={styles.title}>Brand Breakdown</Text>

      <View style={styles.content}>
        {/* Donut chart */}
        <View style={styles.chartContainer}>
          <PolarChart
            data={pieData}
            labelKey="label"
            valueKey="value"
            colorKey="color"
          >
            <Pie.Chart innerRadius="55%">
              {({ slice }) => (
                <Pie.SliceAngularInset
                  angularInset={{
                    angularStrokeWidth: 2,
                    angularStrokeColor: "#F5F6FA",
                  }}
                />
              )}
            </Pie.Chart>
          </PolarChart>

          {/* Center emoji */}
          <View style={styles.centerOverlay} pointerEvents="none">
            <Text style={styles.centerEmoji}>🧋</Text>
          </View>
        </View>

        {/* Legend */}
        <View style={styles.legend}>
          {data.map((item, i) => (
            <View key={item.brand} style={styles.legendRow}>
              <View
                style={[
                  styles.legendDot,
                  { backgroundColor: getBrandColor(item.brand, i) },
                ]}
              />
              <Text style={styles.legendLabel} numberOfLines={1}>
                {item.brand}
              </Text>
              <Text style={styles.legendPct}>{item.pct.toFixed(0)}%</Text>
            </View>
          ))}
        </View>
      </View>
    </View>
  )
}

const styles = StyleSheet.create({
  card: {
    borderRadius: 20,
    padding: 20,
    marginBottom: 24,
    backgroundColor: "rgba(255, 255, 255, 0.75)",
    borderWidth: 1,
    borderColor: "rgba(255, 255, 255, 0.9)",
    shadowColor: "#000",
    shadowOffset: { width: 0, height: 10 },
    shadowOpacity: 0.08,
    shadowRadius: 32,
    elevation: 5,
  },
  title: {
    fontSize: 16,
    fontWeight: "600",
    color: "#1C1C1E",
    marginBottom: 16,
  },
  content: {
    flexDirection: "row",
    alignItems: "center",
    gap: 24,
  },
  chartContainer: {
    width: 128,
    height: 128,
    position: "relative",
  },
  centerOverlay: {
    position: "absolute",
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    alignItems: "center",
    justifyContent: "center",
  },
  centerEmoji: {
    fontSize: 28,
  },
  legend: {
    flex: 1,
    gap: 10,
  },
  legendRow: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
  },
  legendDot: {
    width: 12,
    height: 12,
    borderRadius: 6,
    shadowColor: "#000",
    shadowOffset: { width: 0, height: 1 },
    shadowOpacity: 0.1,
    shadowRadius: 2,
  },
  legendLabel: {
    flex: 1,
    fontSize: 14,
    fontWeight: "500",
    color: "#1C1C1E",
  },
  legendPct: {
    fontSize: 14,
    fontWeight: "600",
    color: "#9CA3AF",
  },
})
