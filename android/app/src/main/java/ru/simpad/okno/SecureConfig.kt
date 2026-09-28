package ru.simpad.okno

import android.content.Context
import android.content.RestrictionsManager
import android.content.SharedPreferences
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKeys

/**
 * Адрес сервера и ключ планшета. Хранятся зашифрованно (ключ шифрования — в Android Keystore).
 * Рабочая сборка: адрес сервера зашит при сборке (BuildConfig.SERVER_URL) и на планшете не меняется.
 * Ключ планшета может задать система управления планшетами (MDM, управляемая конфигурация device_key).
 * Ключ планшета наружу не отдаётся: интерфейс (JavaScript) его не видит, он подставляется только в ApiClient.
 */
class SecureConfig(private val context: Context) {

    private val prefs: SharedPreferences = EncryptedSharedPreferences.create(
        "simpad_secure_v2",
        MasterKeys.getOrCreate(MasterKeys.AES256_GCM_SPEC),
        context,
        EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
        EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM
    )

    fun isServerFixed(): Boolean = BuildConfig.SERVER_URL.isNotEmpty()

    fun server(): String? = if (isServerFixed()) BuildConfig.SERVER_URL else prefs.getString(KEY_SERVER, null)

    fun deviceKey(): String? = managedKey() ?: prefs.getString(KEY_DEVICE, null)

    fun isManaged(): Boolean = managedKey() != null

    fun isConfigured(): Boolean = !server().isNullOrEmpty() && !deviceKey().isNullOrEmpty()

    /**
     * Сохранение настройки. Смена адреса сервера без ввода нового ключа запрещена:
     * сохранённый ключ планшета никогда не уйдёт на другой сервер.
     */
    fun save(server: String, deviceKey: String): Boolean {
        val newServer = if (isServerFixed()) BuildConfig.SERVER_URL else server.trim().trimEnd('/')
        if (!newServer.startsWith("https://")) return false
        val oldServer = prefs.getString(KEY_SERVER, null)
        val serverChanged = !isServerFixed() && oldServer != null && oldServer != newServer
        if (serverChanged && deviceKey.isEmpty()) return false
        // ключ, заданный системой управления планшетами, привязан к её настройке — смена адреса здесь запрещена
        if (serverChanged && isManaged()) return false
        if (deviceKey.isEmpty() && prefs.getString(KEY_DEVICE, null).isNullOrEmpty() && !isManaged()) return false
        val edit = prefs.edit().putString(KEY_SERVER, newServer)
        if (serverChanged) edit.remove(KEY_DEVICE)
        if (deviceKey.isNotEmpty()) edit.putString(KEY_DEVICE, deviceKey)
        return edit.commit()
    }

    private fun managedKey(): String? {
        return try {
            val rm = context.getSystemService(Context.RESTRICTIONS_SERVICE) as RestrictionsManager
            val key = rm.applicationRestrictions.getString(MANAGED_DEVICE_KEY)
            if (key.isNullOrEmpty()) null else key
        } catch (e: Exception) {
            null
        }
    }

    private companion object {
        const val KEY_SERVER = "server"
        const val KEY_DEVICE = "device_key"
        const val MANAGED_DEVICE_KEY = "device_key"
    }
}
