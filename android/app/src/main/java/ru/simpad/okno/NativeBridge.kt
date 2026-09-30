package ru.simpad.okno

import android.webkit.JavascriptInterface
import org.json.JSONObject

/** Методы, доступные интерфейсу стойки как window.SimpadNative. Ключ планшета через мост не читается. */
class NativeBridge(private val activity: MainActivity) {

    private val config = SecureConfig(activity)
    private val api = ApiClient(config)
    private val scanner: DocumentScanner =
        if (BuildConfig.DEMO_SCANNER) DemoScanner() else SmartEnginesScanner(activity)

    @JavascriptInterface
    fun getServer(): String = config.server() ?: ""

    @JavascriptInterface
    fun isConfigured(): Boolean = config.isConfigured()

    /** Рабочая сборка: адрес сервера зашит при сборке и не меняется на планшете. */
    @JavascriptInterface
    fun isServerFixed(): Boolean = config.isServerFixed()

    /** Ключ планшета задан системой управления планшетами (MDM). */
    @JavascriptInterface
    fun isManaged(): Boolean = config.isManaged()

    @JavascriptInterface
    fun saveConfig(server: String, deviceKey: String): Boolean = try {
        config.save(server.trim(), deviceKey.trim())
    } catch (e: Exception) {
        false
    }

    @JavascriptInterface
    fun http(reqId: String, method: String, path: String, headersJson: String, body: String) {
        // Рабочая сборка вне режима киоска к данным не допускается (fail-closed)
        if (!BuildConfig.DEMO_SCANNER && !activity.kioskReady()) {
            activity.deliverHttp(reqId, 423, "{\"detail\":\"Планшет не в режиме киоска: работа запрещена. Обратитесь к администратору (MDM)\"}")
            return
        }
        api.request(method, path, headersJson, body) { status, text -> activity.deliverHttp(reqId, status, text) }
    }

    /** Режим киоска включён системой управления планшетами (для учебной сборки всегда true). */
    @JavascriptInterface
    fun isKioskReady(): Boolean = BuildConfig.DEMO_SCANNER || activity.kioskReady()

    @JavascriptInterface
    fun isDemo(): Boolean = BuildConfig.DEMO_SCANNER

    @JavascriptInterface
    fun appVersion(): String = BuildConfig.VERSION_NAME

    @JavascriptInterface
    fun startScan(kind: String, reqId: String) {
        scanner.scan(kind) { result -> activity.deliverScan(reqId, result) }
    }

    @JavascriptInterface
    fun print() {
        activity.printPage()
    }

    // ---------- поясной принтер талонов ----------

    @JavascriptInterface
    fun hasPrinter(): Boolean = activity.printer.configured() && activity.printer.hasPermission()

    @JavascriptInterface
    fun printerName(): String = activity.printer.name()

    @JavascriptInterface
    fun printerDots(): Int = activity.printer.dots()

    /** Список сопряжённых устройств. Без разрешения Bluetooth — запрос разрешения и ошибка с подсказкой. */
    @JavascriptInterface
    fun listPrinters(): String {
        if (!activity.printer.canPick()) {
            return JSONObject().put("error", "Принтер задаёт администратор через систему управления планшетами").toString()
        }
        if (!activity.printer.hasPermission()) {
            activity.requestBluetooth()
            return JSONObject().put("error", "Разрешите доступ к Bluetooth в появившемся окне и нажмите ещё раз").toString()
        }
        return JSONObject().put("devices", activity.printer.bonded()).toString()
    }

    @JavascriptInterface
    fun setPrinter(mac: String, name: String, dots: Int): Boolean = activity.printer.select(mac, name, dots)

    /** Можно ли выбирать принтер на экране настройки (только учебная сборка без принтера от MDM). */
    @JavascriptInterface
    fun canPickPrinter(): Boolean = activity.printer.canPick()

    @JavascriptInterface
    fun printTicket(reqId: String, pngB64: String) {
        activity.printer.print(pngB64) { ok, err ->
            val res = if (ok) JSONObject().put("ok", true).put("data", JSONObject()) else JSONObject().put("ok", false).put("error", err)
            activity.deliverPrint(reqId, res)
        }
    }
}
