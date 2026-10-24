plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.example.workouttts"
    // 注:此前误判 android-34 平台损坏(实际完好,DisplayManager 在 android.hardware.display 包)。
    // 真正的问题是 CaptureService.kt 把 import 写成了 android.view.DisplayManager(错误),
    // 已修正为 android.hardware.display.DisplayManager。故 compileSdk 维持 34 即可(与 AGP 8.5.2 匹配)。
    compileSdk = 34

    defaultConfig {
        applicationId = "com.example.workouttts"
        minSdk = 24
        targetSdk = 34
        versionCode = 1
        versionName = "1.0"
    }

    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
    buildFeatures {
        viewBinding = true
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("com.google.android.material:material:1.12.0")
    implementation("androidx.constraintlayout:constraintlayout:2.1.4")
    // 设备端 OCR（中文），bundled 模型打包进 APK，离线可用、无需 Google Play 下载
    implementation("com.google.mlkit:text-recognition-chinese:16.0.1")
}
