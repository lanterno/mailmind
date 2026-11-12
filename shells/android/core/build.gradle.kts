// The Rust core as an Android library: UniFFI's generated Kotlin in src/main/kotlin, and the core's shared library
// per ABI in src/main/jniLibs, which `just android libs` writes. AGP compiles the Kotlin itself (built-in Kotlin).
plugins {
    alias(libs.plugins.android.library)
}

android {
    // uniffi.toml names this package; both are placeholders until the company's domain is settled.
    namespace = "app.mailmind.core"
    compileSdk = libs.versions.compileSdk.get().toInt()
    ndkVersion = libs.versions.ndk.get()

    defaultConfig {
        minSdk = libs.versions.minSdk.get().toInt()
        consumerProguardFiles("consumer-rules.pro")
    }
}

dependencies {
    // The aar, not the jar: only the aar carries JNA's native library for Android. `api`, because the generated
    // classes the app uses name JNA's types in their signatures.
    api("net.java.dev.jna:jna:${libs.versions.jna.get()}@aar")
}
