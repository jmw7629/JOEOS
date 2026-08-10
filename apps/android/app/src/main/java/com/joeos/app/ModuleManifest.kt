package com.joeos.app

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * Cross-platform JoeOS module manifest (mirrors server/modules/manifest.py and
 * the Swift ModuleManifest). Data-only declarative contract: a client renders a
 * manifest through a trusted Compose component registry and never downloads
 * executable code. Unknown fields are ignored; unknown widget types fail safely.
 *
 * @SerialName mappings preserve the snake_case JSON keys the server emits so
 * the same contract decodes identically on server, iOS (Swift), and Android.
 */
@Serializable
data class ModuleManifest(
    val id: String,
    val type: String = "module",
    val version: String = "1.0.0",
    @SerialName("display_name")
    val displayName: String = "",
    val description: String = "",
    val icon: String = "",
    val category: String = "",
    val subcategory: String = "",
    val route: String = "",
    @SerialName("supported_form_factors")
    val supportedFormFactors: List<String> = listOf("phone", "tablet", "laptop", "desktop"),
    @SerialName("required_permissions")
    val requiredPermissions: List<String> = emptyList(),
    @SerialName("required_capabilities")
    val requiredCapabilities: List<String> = emptyList(),
    val commands: List<String> = emptyList(),
    val actions: List<String> = emptyList(),
    @SerialName("data_sources")
    val dataSources: List<String> = emptyList(),
    @SerialName("joe_context")
    val joeContext: JoeContextScope = JoeContextScope(),
    val widgets: List<ModuleWidget> = emptyList(),
    val inspection: Boolean = false,
    @SerialName("feature_flags")
    val featureFlags: List<String> = emptyList(),
    @SerialName("policy_requirements")
    val policyRequirements: List<String> = emptyList(),
    @SerialName("min_client_version")
    val minClientVersion: String = "",
    val visibility: String = "visible",
    val ordering: Int = 0,
    val pinned: Boolean = false,
    @SerialName("user_customizable")
    val userCustomizable: Boolean = false,
    @SerialName("schema_version")
    val schemaVersion: Int = 1,
)

@Serializable
data class JoeContextScope(
    val kind: String = "none",
    @SerialName("object_type")
    val objectType: String? = null,
    @SerialName("object_id")
    val objectID: String? = null,
)

@Serializable
data class ModuleWidget(
    val id: String,
    val type: String,
    val title: String = "",
    val config: Map<String, String> = emptyMap(),
)
