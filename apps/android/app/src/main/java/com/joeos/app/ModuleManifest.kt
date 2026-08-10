package com.joeos.app

import kotlinx.serialization.Serializable

/**
 * Cross-platform JoeOS module manifest (mirrors server/modules/manifest.py and
 * the Swift ModuleManifest). Data-only declarative contract: a client renders a
 * manifest through a trusted Compose component registry and never downloads
 * executable code. Unknown fields are ignored; unknown widget types fail safely.
 */
@Serializable
data class ModuleManifest(
    val id: String,
    val type: String = "module",
    val version: String = "1.0.0",
    val displayName: String = "",
    val description: String = "",
    val icon: String = "",
    val category: String = "",
    val subcategory: String = "",
    val route: String = "",
    val supportedFormFactors: List<String> = listOf("phone", "tablet", "laptop", "desktop"),
    val requiredPermissions: List<String> = emptyList(),
    val requiredCapabilities: List<String> = emptyList(),
    val commands: List<String> = emptyList(),
    val actions: List<String> = emptyList(),
    val dataSources: List<String> = emptyList(),
    val joeContext: JoeContextScope = JoeContextScope(),
    val widgets: List<ModuleWidget> = emptyList(),
    val inspection: Boolean = false,
    val featureFlags: List<String> = emptyList(),
    val policyRequirements: List<String> = emptyList(),
    val minClientVersion: String = "",
    val visibility: String = "visible",
    val ordering: Int = 0,
    val pinned: Boolean = false,
    val userCustomizable: Boolean = false,
    val schemaVersion: Int = 1,
)

@Serializable
data class JoeContextScope(
    val kind: String = "none",
    val objectType: String? = null,
    val objectID: String? = null,
)

@Serializable
data class ModuleWidget(
    val id: String,
    val type: String,
    val title: String = "",
    val config: Map<String, String> = emptyMap(),
)
