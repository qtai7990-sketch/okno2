package ru.simpad.okno

import org.json.JSONObject
import java.io.IOException
import java.net.URL
import java.util.concurrent.Executors
import javax.net.ssl.HttpsURLConnection

/**
 * Запросы интерфейса к серверу системы. Ключ планшета добавляется здесь и в JavaScript не попадает.
 * Разрешены только HTTPS и только адреса контура стойки /api/v1/ — административный и банковский контуры недоступны.
 */
class ApiClient(private val config: SecureConfig) {

    fun request(method: String, path: String, headersJson: String, body: String, done: (Int, String) -> Unit) {
        pool.execute {
            val result: Pair<Int, String> = try {
                perform(method, path, headersJson, body)
            } catch (e: IOException) {
                Pair(0, "")
            } catch (e: IllegalArgumentException) {
                Pair(400, JSONObject().put("detail", e.message ?: "Запрос отклонён планшетом").toString())
            } catch (e: Exception) {
                Pair(0, "")
            }
            done(result.first, result.second)
        }
    }

    private fun perform(method: String, path: String, headersJson: String, body: String): Pair<Int, String> {
        require(method in ALLOWED_METHODS) { "Метод запроса не разрешён" }
        require(path.startsWith("/api/v1/") && !path.contains("..") && !path.contains("//")) { "Адрес не разрешён" }
        val server = config.server()
        val key = config.deviceKey()
        require(!server.isNullOrEmpty() && !key.isNullOrEmpty()) { "Планшет не настроен" }
        require(server.startsWith("https://")) { "Разрешён только HTTPS" }
        // Рабочая сборка без закрепления сертификата сервера к данным не допускается
        require(BuildConfig.DEMO_SCANNER || BuildConfig.CERT_PINNED) {
            "Сборка без закрепления сертификата сервера — только для проверки, не для работы"
        }

        val conn = URL(server + path).openConnection() as HttpsURLConnection
        try {
            conn.requestMethod = method
            conn.connectTimeout = 15_000
            conn.readTimeout = 60_000
            conn.useCaches = false
            conn.instanceFollowRedirects = false
            conn.setRequestProperty("X-Device-Key", key)
            conn.setRequestProperty("X-App-Version", BuildConfig.VERSION_CODE.toString())
            conn.setRequestProperty("Accept", "application/json")
            val headers = if (headersJson.isEmpty()) JSONObject() else JSONObject(headersJson)
            for (name in ALLOWED_HEADERS) {
                val value = headers.optString(name, "")
                if (value.isNotEmpty() && !value.contains('\n') && !value.contains('\r')) {
                    conn.setRequestProperty(name, value)
                }
            }
            if (body.isNotEmpty()) {
                conn.doOutput = true
                conn.outputStream.use { it.write(body.toByteArray(Charsets.UTF_8)) }
            }
            val code = conn.responseCode
            val stream = if (code >= 400) conn.errorStream else conn.inputStream
            val text = stream?.bufferedReader(Charsets.UTF_8)?.use { it.readText() } ?: ""
            return Pair(code, text)
        } finally {
            conn.disconnect()
        }
    }

    private companion object {
        // один пул на процесс: Activity может пересоздаваться, пул — нет
        val pool = Executors.newFixedThreadPool(4)
        val ALLOWED_METHODS = setOf("GET", "POST", "PUT", "PATCH", "DELETE")
        val ALLOWED_HEADERS = listOf("X-Session", "Content-Type")
    }
}
