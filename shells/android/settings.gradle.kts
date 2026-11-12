// The Android shell: `core` is the Rust core as an Android library, `app` is the Compose app over it.
pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    // Every module takes its dependencies from these two repositories and no others.
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
    }
}

rootProject.name = "MailMind"
include(":core", ":app")
