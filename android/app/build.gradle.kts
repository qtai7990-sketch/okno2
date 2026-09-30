// Явные импорты: внутри скрипта Gradle имя «java» занято расширением плагина, поэтому java.time.* / java.net.*
// без импорта не разрешаются (ошибка сборки v0.2.3).
import java.net.URI

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

val runNumber = (System.getenv("GITHUB_RUN_NUMBER") ?: "1").toInt()
// Версия программы (совпадает с сервером) + номер сборки GitHub: «0.2.8+15». versionCode — номер сборки.
val appVersion = "0.2.8"
val keystorePath: String? = System.getenv("SIMPAD_KEYSTORE_PATH")

// Рабочая сборка: адрес сервера и закреплённые сертификаты зашиваются при сборке (переменные GitHub).
// На планшете их изменить нельзя. Учебная сборка (demo) берёт адрес из настройки и без закрепления.
val serverUrl: String = (System.getenv("SIMPAD_SERVER_URL") ?: "").trim().trimEnd('/')
val certPins: List<String> = (System.getenv("SIMPAD_CERT_PINS") ?: "").split(",").map { it.trim() }.filter { it.isNotEmpty() }
// Строгая проверка адреса: только https://имя-хоста, без порта, пути, параметров и учётных данных
val serverHost: String = if (serverUrl.isEmpty()) "" else {
    val u = URI(serverUrl)
    require(u.scheme == "https" && u.rawUserInfo == null && u.port == -1 && (u.rawPath ?: "").isEmpty() &&
        u.rawQuery == null && u.rawFragment == null) { "SIMPAD_SERVER_URL: только https://имя-хоста" }
    val host = u.host ?: ""
    require(Regex("^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$").matches(host)) {
        "SIMPAD_SERVER_URL: недопустимое имя хоста"
    }
    host
}
require(certPins.all { Regex("^[A-Za-z0-9+/]{43}=$").matches(it) }) { "SIMPAD_CERT_PINS: SHA-256 в base64 через запятую" }
val prodNscDir = file("generated-nsc/prod")  // вне build/: не удаляется задачей clean
val pinned = serverHost.isNotEmpty() && certPins.size >= 2  // минимум основной и резервный ключ

// Файл пишется при настройке сборки (не отдельной задачей): так на него не нужны зависимости задач Gradle.
run {
    val f = File(prodNscDir, "xml/network_security_config.xml")
    f.parentFile.mkdirs()
    val pinXml = if (pinned) """
    <domain-config cleartextTrafficPermitted="false">
        <domain includeSubdomains="false">$serverHost</domain>
        <pin-set expiration="2035-06-01">
${certPins.joinToString("\n") { "            <pin digest=\"SHA-256\">$it</pin>" }}
        </pin-set>
    </domain-config>""" else ""
    f.writeText("""<?xml version="1.0" encoding="utf-8"?>
<!-- Создано при сборке. Только HTTPS; для сервера системы — закрепление сертификата (pin-set). -->
<network-security-config>
    <base-config cleartextTrafficPermitted="false" />$pinXml
</network-security-config>
""")
}

// Рабочая подписанная сборка без ключа подписи, адреса сервера или закрепления сертификата не собирается
gradle.taskGraph.whenReady {
    if (allTasks.any { it.name == "assembleProdRelease" || it.name == "bundleProdRelease" }) {
        if (keystorePath == null) throw GradleException("Нет ключа подписи (SIMPAD_KEYSTORE_PATH)")
        if (!pinned) throw GradleException("Нет SIMPAD_SERVER_URL или минимум двух SIMPAD_CERT_PINS")
    }
}

android {
    namespace = "ru.simpad.okno"
    compileSdk = 34

    defaultConfig {
        applicationId = "ru.simpad.okno"
        minSdk = 26
        targetSdk = 34
        versionCode = runNumber
        versionName = "$appVersion+$runNumber"
    }

    flavorDimensions += "scanner"
    productFlavors {
        create("demo") {
            dimension = "scanner"
            applicationIdSuffix = ".demo"
            versionNameSuffix = "-demo"
            buildConfigField("boolean", "DEMO_SCANNER", "true")
            buildConfigField("String", "SERVER_URL", "\"\"")
            buildConfigField("boolean", "CERT_PINNED", "false")
        }
        create("prod") {
            dimension = "scanner"
            buildConfigField("boolean", "DEMO_SCANNER", "false")
            buildConfigField("String", "SERVER_URL", "\"$serverUrl\"")
            buildConfigField("boolean", "CERT_PINNED", pinned.toString())
        }
    }

    buildFeatures {
        buildConfig = true
    }

    signingConfigs {
        create("release") {
            if (keystorePath != null) {
                storeFile = file(keystorePath)
                storePassword = System.getenv("SIMPAD_KEYSTORE_PASSWORD")
                keyAlias = System.getenv("SIMPAD_KEY_ALIAS")
                keyPassword = System.getenv("SIMPAD_KEY_PASSWORD")
            }
        }
    }

    buildTypes {
        getByName("release") {
            isMinifyEnabled = false
            if (keystorePath != null) {
                signingConfig = signingConfigs.getByName("release")
            }
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }

    // Интерфейс стойки берётся из общей папки web/ — один источник для сервера и планшета
    sourceSets {
        getByName("main") {
            assets.srcDirs("src/main/assets", "../../web")
        }
        getByName("prod") {
            res.srcDir(prodNscDir)
        }
    }
}

// Выпускаются только два варианта: учебный demoDebug и рабочий prodRelease.
// prodDebug (рабочий адрес без подписи) и demoRelease отключены — их нельзя собрать даже вручную.
androidComponents {
    beforeVariants { variant ->
        val flavor = variant.productFlavors.firstOrNull { it.first == "scanner" }?.second
        if ((flavor == "prod" && variant.buildType == "debug") || (flavor == "demo" && variant.buildType == "release")) {
            variant.enable = false
        }
    }
}

// Блокировка версий зависимостей: файлы блокировки создаёт tools/android_lock.sh
dependencyLocking {
    lockAllConfigurations()
}

dependencies {
    implementation("androidx.webkit:webkit:1.11.0")
    implementation("androidx.security:security-crypto:1.0.0")
    implementation("androidx.core:core:1.13.1")
}
