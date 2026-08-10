package com.joeos.app

import kotlinx.serialization.json.Json
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Cross-platform module contract tests: the Kotlin ModuleManifest must decode
 * the same snake_case JSON the JoeOS server emits (mirroring the Swift contract
 * verification). Guards against schema drift between server / iOS / Android.
 */
class ModuleManifestTest {

    private val json = Json {
        ignoreUnknownKeys = true
    }

    private val serverPayload = """
        {"id":"agents","type":"module","version":"1.0.0","display_name":"Agents",
         "description":"Agent directory","icon":"fa-user-astronaut","category":"core",
         "subcategory":"","route":"/os/agents",
         "supported_form_factors":["phone","tablet","desktop"],
         "required_permissions":[],"required_capabilities":["agent.read"],
         "commands":[],"actions":[],"data_sources":[],
         "joe_context":{"kind":"module","object_type":null,"object_id":null},
         "widgets":[{"id":"w1","type":"list","title":"Agents","config":{}}],
         "inspection":true,"feature_flags":[],"policy_requirements":[],
         "min_client_version":"","visibility":"visible","ordering":10,
         "pinned":false,"user_customizable":false,"schema_version":1}
    """.trimIndent()

    @Test
    fun decodesServerManifest() {
        val manifest = json.decodeFromString<ModuleManifest>(serverPayload)
        assertEquals("agents", manifest.id)
        assertEquals("Agents", manifest.displayName)
        assertEquals("/os/agents", manifest.route)
        assertEquals(listOf("agent.read"), manifest.requiredCapabilities)
        assertEquals("module", manifest.joeContext.kind)
        assertEquals("list", manifest.widgets.first().type)
        assertEquals(10, manifest.ordering)
        assertTrue(manifest.inspection)
    }

    @Test
    fun serializationRoundTrip() {
        val manifest = ModuleManifest(id = "command", displayName = "Command Center", route = "/os/command")
        val encoded = json.encodeToString(ModuleManifest.serializer(), manifest)
        val decoded = json.decodeFromString<ModuleManifest>(encoded)
        assertEquals("command", decoded.id)
        assertEquals("Command Center", decoded.displayName)
        assertEquals("/os/command", decoded.route)
    }
}
