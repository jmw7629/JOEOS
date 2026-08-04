import { StyleSheet, Text, View } from "react-native";

export default function Home() {
  return (
    <View style={styles.screen}>
      <View style={styles.card}>
        <Text style={styles.title}>JoeOS</Text>
        <Text style={styles.subtitle}>Mobile web client foundation</Text>
        <Text style={styles.body}>
          This is the initial scaffold for the JoeOS mobile web client. Pairing,
          identity, and platform services arrive in later phases and are never
          shown as connected before they exist.
        </Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    padding: 24,
    backgroundColor: "#0B1220",
  },
  card: {
    width: "100%",
    maxWidth: 480,
    padding: 24,
    borderRadius: 12,
    borderWidth: 1,
    borderColor: "#1E2A3A",
    backgroundColor: "#0E1626",
  },
  title: {
    fontSize: 32,
    fontWeight: "700",
    color: "#E8EDF4",
  },
  subtitle: {
    fontSize: 17,
    color: "#18BFFF",
    marginTop: 4,
  },
  body: {
    fontSize: 15,
    lineHeight: 22,
    color: "#9FB0C3",
    marginTop: 16,
  },
});
