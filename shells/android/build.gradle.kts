// The plugins are resolved once here so both modules share one copy. Declaring the Compose compiler plugin here
// also puts the Kotlin Gradle plugin of the same version on the classpath, above the older one AGP asks for.
plugins {
    alias(libs.plugins.android.application) apply false
    alias(libs.plugins.android.library) apply false
    alias(libs.plugins.kotlin.compose) apply false
}
