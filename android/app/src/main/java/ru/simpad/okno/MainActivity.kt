package ru.simpad.okno

import android.Manifest
import android.annotation.SuppressLint
import android.app.Activity
import android.app.ActivityManager
import android.app.admin.DevicePolicyManager
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.print.PrintAttributes
import android.print.PrintManager
import android.view.KeyEvent
import android.view.WindowManager
import android.webkit.PermissionRequest
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.webkit.WebViewAssetLoader
import org.json.JSONObject
import java.io.File

/**
 * Оболочка «Единого окна»: интерфейс стойки (папка web/) внутри WebView
 * и мост к возможностям планшета — сканер документов, запросы к серверу с ключом планшета,
 * камера для фото подписанного бланка согласия, печать.
 * Фото бланка снимается внутри приложения (камера в WebView): кадр не сохраняется ни в галерею,
 * ни в файлы — он существует только в памяти до отправки на сервер в зашифрованное дело.
 */
class MainActivity : Activity() {

    private lateinit var web: WebView
    lateinit var printer: TicketPrinter
    private var pendingCamera: PermissionRequest? = null
    private var printing = false

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // Персональные данные на экране: запрет скриншотов и показа в списке недавних приложений
        window.setFlags(WindowManager.LayoutParams.FLAG_SECURE, WindowManager.LayoutParams.FLAG_SECURE)
        // остатки фото бланков от версий до 0.2.8 (внешняя камера писала файл во временную папку) — удалить
        File(cacheDir, "consent").let { d -> d.listFiles()?.forEach { it.delete() }; d.delete() }

        val loader = WebViewAssetLoader.Builder()
            .addPathHandler("/assets/", WebViewAssetLoader.AssetsPathHandler(this))
            .build()

        web = WebView(this)
        web.settings.javaScriptEnabled = true
        web.settings.domStorageEnabled = false
        web.settings.allowFileAccess = false
        web.settings.allowContentAccess = false
        web.webViewClient = object : WebViewClient() {
            override fun shouldInterceptRequest(view: WebView, request: WebResourceRequest): WebResourceResponse? {
                return loader.shouldInterceptRequest(request.url)
            }

            // Любые переходы со страницы запрещены: приложение показывает только интерфейс стойки
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                return true
            }
        }
        web.settings.mediaPlaybackRequiresUserGesture = false  // видео с камеры без отдельного касания
        web.webChromeClient = object : WebChromeClient() {
            // Камера — только для собственной страницы приложения и только видео, без микрофона
            override fun onPermissionRequest(request: PermissionRequest) {
                runOnUiThread {
                    val own = request.origin?.host == ASSET_HOST
                    if (!own || !request.resources.contains(PermissionRequest.RESOURCE_VIDEO_CAPTURE)) {
                        request.deny()
                        return@runOnUiThread
                    }
                    if (checkSelfPermission(Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED) {
                        request.grant(arrayOf(PermissionRequest.RESOURCE_VIDEO_CAPTURE))
                    } else {
                        pendingCamera?.deny()
                        pendingCamera = request
                        requestPermissions(arrayOf(Manifest.permission.CAMERA), REQ_CAMERA_PERMISSION)
                    }
                }
            }

            override fun onPermissionRequestCanceled(request: PermissionRequest) {
                if (pendingCamera == request) pendingCamera = null
            }
        }
        printer = TicketPrinter(this)
        web.addJavascriptInterface(NativeBridge(this), "SimpadNative")
        setContentView(web)
        web.loadUrl("https://$ASSET_HOST/assets/index.html")
    }

    override fun onResume() {
        super.onResume()
        printing = false
        // Если система управления планшетами разрешила киоск — входим в него сами
        val dpm = getSystemService(DEVICE_POLICY_SERVICE) as DevicePolicyManager
        val am = getSystemService(ACTIVITY_SERVICE) as ActivityManager
        if (dpm.isLockTaskPermitted(packageName) && am.lockTaskModeState == ActivityManager.LOCK_TASK_MODE_NONE) {
            try {
                startLockTask()
            } catch (e: Exception) {
                // киоск не включился — рабочая сборка запросы не отправит (kioskReady() = false)
            }
        }
    }

    /** Киоск разрешён системой управления планшетами и включён. */
    fun kioskReady(): Boolean {
        val dpm = getSystemService(DEVICE_POLICY_SERVICE) as DevicePolicyManager
        val am = getSystemService(ACTIVITY_SERVICE) as ActivityManager
        return dpm.isLockTaskPermitted(packageName) && am.lockTaskModeState != ActivityManager.LOCK_TASK_MODE_NONE
    }

    override fun onStop() {
        super.onStop()
        // Приложение ушло с экрана (не ради печати) — экран блокируется, вернуться можно по ПИН.
        // Камера работает внутри приложения, поэтому экран с неё не уходит.
        if (!printing) {
            web.evaluateJavascript("window.__simpadLock && window.__simpadLock()", null)
        }
    }

    private fun callJs(fn: String, reqId: String, payload: String) {
        runOnUiThread {
            web.evaluateJavascript("window.$fn(${JSONObject.quote(reqId)}, $payload)", null)
        }
    }

    /** Результат печати талона — обратно в интерфейс. */
    fun deliverPrint(reqId: String, result: JSONObject) {
        callJs("__simpadPrintResult", reqId, JSONObject.quote(result.toString()))
    }

    /** Разрешение на подключение к принтеру (Android 12+). */
    fun requestBluetooth() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            runOnUiThread { requestPermissions(arrayOf(Manifest.permission.BLUETOOTH_CONNECT), REQ_BT_PERMISSION) }
        }
    }

    override fun onDestroy() {
        printer.shutdown()
        super.onDestroy()
    }

    /**
     * Samsung Active Key настраивается MDM/Knox как F2. Обрабатываем его только
     * через Web UI: проверка согласия 152-ФЗ остаётся в JavaScript и сканер
     * никогда не запускается нативным кодом в обход этого ограничения.
     */
    override fun dispatchKeyEvent(event: KeyEvent): Boolean {
        if (event.keyCode == KeyEvent.KEYCODE_F2 && event.action == KeyEvent.ACTION_UP) {
            web.evaluateJavascript("window.__floor1Action && window.__floor1Action('scan')", null)
            return true
        }
        return super.dispatchKeyEvent(event)
    }

    /** Результат сканирования — обратно в интерфейс. */
    fun deliverScan(reqId: String, result: JSONObject) {
        callJs("__simpadScanResult", reqId, JSONObject.quote(result.toString()))
    }

    /** Ответ сервера — обратно в интерфейс (status 0 — нет связи). */
    fun deliverHttp(reqId: String, status: Int, text: String) {
        callJs("__simpadHttp", reqId, "$status, ${JSONObject.quote(text)}")
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode != REQ_CAMERA_PERMISSION) return
        val req = pendingCamera ?: return
        pendingCamera = null
        if (grantResults.isNotEmpty() && grantResults[0] == PackageManager.PERMISSION_GRANTED) {
            req.grant(arrayOf(PermissionRequest.RESOURCE_VIDEO_CAPTURE))
        } else {
            req.deny()  // интерфейс покажет «Нет разрешения на камеру»
        }
    }

    /** Печать памятки через системную печать Android. */
    fun printPage() {
        runOnUiThread {
            printing = true
            val manager = getSystemService(PRINT_SERVICE) as PrintManager
            val adapter = web.createPrintDocumentAdapter("Pamyatka")
            manager.print("Памятка клиенту", adapter, PrintAttributes.Builder().build())
        }
    }

    @Deprecated("Кнопка «Назад» отключена: планшет работает в режиме киоска")
    override fun onBackPressed() {
        // Ничего не делаем
    }

    private companion object {
        const val ASSET_HOST = "appassets.androidplatform.net"
        const val REQ_CAMERA_PERMISSION = 42
        const val REQ_BT_PERMISSION = 43
    }
}
