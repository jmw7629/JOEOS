# JoeOS Android — release R8 rules.
# Keep the module manifest models (serialized JSON) and their fields.
-keep class com.joeos.app.** { *; }
-keepattributes *Annotation*
-dontwarn kotlinx.serialization.**
