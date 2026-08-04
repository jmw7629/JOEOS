import { Stack } from "expo-router";
import { StatusBar } from "expo-status-bar";

const brandBackground = "#0B1220";

export default function RootLayout() {
  return (
    <>
      <StatusBar style="light" />
      <Stack
        screenOptions={{
          headerStyle: { backgroundColor: brandBackground },
          headerTintColor: "#E8EDF4",
          headerTitleStyle: { fontWeight: "600" },
          contentStyle: { backgroundColor: brandBackground },
        }}
      />
    </>
  );
}
