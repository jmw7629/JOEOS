package com.joeos.app

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import com.joeos.app.ui.JoeOSTheme
import com.joeos.app.ui.joeOSCanvas

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            JoeOSTheme {
                JoeOSShell()
            }
        }
    }
}

/** Built-in Command Center modules seeded from the product defaults. */
val builtinModules = listOf(
    ModuleManifest(
        id = "command", displayName = "Command Center", icon = "fa-border-all",
        route = "/os/command", ordering = 0, pinned = true,
        widgets = listOf(
            ModuleWidget("agents", "agent_panel", "Agents"),
            ModuleWidget("automations", "task_panel", "Automations"),
        ),
    ),
    ModuleManifest(
        id = "agents", displayName = "Agents", icon = "fa-user-astronaut",
        route = "/os/agents", ordering = 10, inspection = true,
        requiredCapabilities = listOf("agent.read"),
        widgets = listOf(ModuleWidget("list", "list", "Agent directory")),
    ),
    ModuleManifest(
        id = "terminal", displayName = "Terminal", icon = "fa-terminal",
        route = "/os/terminal", ordering = 50,
        requiredPermissions = listOf("terminal.open"),
    ),
)

@Composable
fun JoeOSShell() {
    Surface(modifier = Modifier.fillMaxSize().background(joeOSCanvas), color = joeOSCanvas) {
        LazyColumn(modifier = Modifier.fillMaxSize().padding(16.dp)) {
            item { Text("JoeOS", style = MaterialTheme.typography.headlineMedium, color = Color.White) }
            items(builtinModules.size) { index ->
                ModuleCard(builtinModules[index])
            }
        }
    }
}

@Composable
fun ModuleCard(manifest: ModuleManifest) {
    Card(
        modifier = Modifier.fillMaxWidth().padding(vertical = 6.dp),
        colors = CardDefaults.cardColors(containerColor = Color(0xFF171C25)),
    ) {
        Column(Modifier.padding(16.dp)) {
            Text(
                manifest.displayName.ifEmpty { manifest.id },
                style = MaterialTheme.typography.titleMedium,
                color = Color.White,
            )
            if (manifest.requiredCapabilities.isNotEmpty()) {
                Text(
                    manifest.requiredCapabilities.joinToString(", "),
                    style = MaterialTheme.typography.bodySmall,
                    color = Color(0xFF3B8FD8),
                )
            }
            manifest.widgets.forEach { widget ->
                Text(
                    "• ${widget.type}: ${widget.title}",
                    style = MaterialTheme.typography.bodySmall,
                    color = Color(0xFFB8C0CC),
                )
            }
        }
    }
}
