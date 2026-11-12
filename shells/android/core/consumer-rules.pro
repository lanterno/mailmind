# JNA finds its classes and the core's callbacks by reflection, so a shrunk app must keep them.
-keep class com.sun.jna.** { *; }
-keep class * implements com.sun.jna.** { *; }
-keep class app.mailmind.core.** { *; }
-dontwarn java.awt.**
