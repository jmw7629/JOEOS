package com.joeos.app.ui

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

// JoeOS design tokens (native Compose equivalents of the cross-platform tokens)
val joeOSCanvas = Color(0xFF07090D)
val joeOSSurface = Color(0xFF0B0E14)
val joeOSRaised = Color(0xFF171C25)
val joeOSSilver = Color(0xFFB8C0CC)
val joeOSText = Color(0xFFFFFFFF)
val joeOSBlue = Color(0xFF1769AA)
val joeOSActiveBlue = Color(0xFF3B8FD8)

private val JoeOSColors = darkColorScheme(
    primary = joeOSActiveBlue,
    onPrimary = Color.White,
    secondary = joeOSBlue,
    background = joeOSCanvas,
    surface = joeOSSurface,
    onBackground = joeOSText,
    onSurface = joeOSText,
    error = Color(0xFFFF647C),
)

@Composable
fun JoeOSTheme(content: @Composable () -> Unit) {
    MaterialTheme(colorScheme = JoeOSColors, content = content)
}
