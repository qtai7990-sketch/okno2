package ru.simpad.okno

import android.Manifest
import android.annotation.SuppressLint
import android.app.Activity
import android.app.ActivityManager
import android.app.admin.DevicePolicyManager
import android.content.ActivityNotFoundException
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Matrix
import android.media.ExifInterface
import android.os.Build
import android.os.Bundle
import android.print.PrintAttributes
import android.print.PrintManager
import android.provider.MediaStore
import android.util.Base64
import android.view.WindowManager
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.core.content.FileProvider
import androidx.webkit.WebViewAssetLoader
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.io.File

/**
 * Оболочка «Единого окна»: интерфейс стойки (папка web/) внутри WebView
 * и мост к возможностям планшета — сканер документов, запросы к серверу с ключом планшета,
 * камера для фото подписанного бланка согласия, печать.
 */
class MainActivity : Activity() {

    private lateinit var web: WebView
    lateinit var printer: TicketPrinter
    private var photoReq: String? = null
    private var photoFile: File? = null
    private var printing = false

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // Персональные данные на экране: запрет скриншотов и показа в списке недавних приложений
        window.setFlags(WindowManager.LayoutParams.FLAG_SECURE, WindowManager.LayoutParams.FLAG_SECURE)
        // остатки фото бланков после аварийного завершения — удалить
        File(cacheDir, "consent").listFiles()?.forEach { it.delete() }

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
        printer = TicketPrinter(this)
        web.addJavascriptInterface(NativeBridge(this), "SimpadNative")
        setContentView(web)
        web.loadUrl("https://appassets.androidplatform.net/assets/index.html")
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
        // Приложение ушло с экрана (не ради камеры или печати) — экран блокируется, вернуться можно по ПИН
        if (photoReq == null && !printing) {
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

    /** Результат сканирования — обратно в интерфейс. */
    fun deliverScan(reqId: String, result: JSONObject) {
        callJs("__simpadScanResult", reqId, JSONObject.quote(result.toString()))
    }

    /** Ответ сервера — обратно в интерфейс (status 0 — нет связи). */
    fun deliverHttp(reqId: String, status: Int, text: String) {
        callJs("__simpadHttp", reqId, "$status, ${JSONObject.quote(text)}")
    }

    // ---------- фото подписанного бумажного бланка ----------

    fun startPhoto(reqId: String) {
        runOnUiThread {
            if (photoReq != null) {
                callJs("__simpadPhotoResult", reqId, JSONObject.quote(scanFail("Камера уже открыта").toString()))
                return@runOnUiThread
            }
            photoReq = reqId
            if (checkSelfPermission(Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) {
                requestPermissions(arrayOf(Manifest.permission.CAMERA), REQ_CAMERA_PERMISSION)
            } else {
                launchCamera()
            }
        }
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode != REQ_CAMERA_PERMISSION) return
        if (grantResults.isNotEmpty() && grantResults[0] == PackageManager.PERMISSION_GRANTED) {
            launchCamera()
        } else {
            finishPhoto(scanFail("Нет разрешения на камеру"))
        }
    }

    private fun launchCamera() {
        try {
            val dir = File(cacheDir, "consent")
            dir.mkdirs()
            val file = File.createTempFile("form", ".jpg", dir)
            photoFile = file
            val uri = FileProvider.getUriForFile(this, "$packageName.files", file)
            val intent = Intent(MediaStore.ACTION_IMAGE_CAPTURE)
            intent.putExtra(MediaStore.EXTRA_OUTPUT, uri)
            intent.addFlags(Intent.FLAG_GRANT_WRITE_URI_PERMISSION or Intent.FLAG_GRANT_READ_URI_PERMISSION)
            startActivityForResult(intent, REQ_PHOTO)
        } catch (e: ActivityNotFoundException) {
            finishPhoto(scanFail("На планшете нет приложения камеры"))
        } catch (e: Exception) {
            finishPhoto(scanFail("Камера недоступна: ${e.message}"))
        }
    }

    @Deprecated("Используется Activity без AndroidX Activity Result API")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode != REQ_PHOTO) return
        val file = photoFile
        if (resultCode != RESULT_OK || file == null || !file.exists() || file.length() == 0L) {
            finishPhoto(scanFail("Фото не сделано"))
            return
        }
        Thread { finishPhoto(encodePhoto(file)) }.start()
    }

    /** Уменьшает фото до 2400 точек по длинной стороне, JPEG 80%. Исходный файл удаляется. */
    private fun encodePhoto(file: File): JSONObject {
        try {
            val bounds = BitmapFactory.Options()
            bounds.inJustDecodeBounds = true
            BitmapFactory.decodeFile(file.path, bounds)
            var sample = 1
            while (maxOf(bounds.outWidth, bounds.outHeight) / sample > MAX_SIDE) sample *= 2
            val opts = BitmapFactory.Options()
            opts.inSampleSize = sample
            val decoded = BitmapFactory.decodeFile(file.path, opts) ?: return scanFail("Не удалось прочитать фото")
            // Поворот по данным камеры (EXIF), чтобы бланк не сохранялся «на боку»
            val degrees = when (ExifInterface(file.path).getAttributeInt(ExifInterface.TAG_ORIENTATION, ExifInterface.ORIENTATION_NORMAL)) {
                ExifInterface.ORIENTATION_ROTATE_90 -> 90f
                ExifInterface.ORIENTATION_ROTATE_180 -> 180f
                ExifInterface.ORIENTATION_ROTATE_270 -> 270f
                else -> 0f
            }
            val bmp = if (degrees == 0f) decoded else {
                val m = Matrix()
                m.postRotate(degrees)
                val rotated = Bitmap.createBitmap(decoded, 0, 0, decoded.width, decoded.height, m, true)
                if (rotated != decoded) decoded.recycle()
                rotated
            }
            val out = ByteArrayOutputStream()
            bmp.compress(Bitmap.CompressFormat.JPEG, 80, out)
            bmp.recycle()
            val data = JSONObject()
                .put("mime", "image/jpeg")
                .put("b64", Base64.encodeToString(out.toByteArray(), Base64.NO_WRAP))
            return scanOk(data)
        } catch (e: OutOfMemoryError) {
            return scanFail("Фото слишком большое")
        } catch (e: Exception) {
            return scanFail("Ошибка обработки фото: ${e.message}")
        }
    }

    private fun finishPhoto(result: JSONObject) {
        runOnUiThread {
            photoFile?.delete()
            photoFile = null
            val id = photoReq
            photoReq = null
            if (id != null) callJs("__simpadPhotoResult", id, JSONObject.quote(result.toString()))
        }
    }

    /** Печать памятки через системную печать Android. */
    fun printPage() {
        runOnUiThread {
            printing = true
            val manager = getSystemService(PRINT_SERVICE) as PrintManager
            val adapter = web.createPrintDocumentAdapter("SIMPAD-pamyatka")
            manager.print("Памятка клиенту", adapter, PrintAttributes.Builder().build())
        }
    }

    @Deprecated("Кнопка «Назад» отключена: планшет работает в режиме киоска")
    override fun onBackPressed() {
        // Ничего не делаем
    }

    private companion object {
        const val REQ_PHOTO = 41
        const val REQ_CAMERA_PERMISSION = 42
        const val REQ_BT_PERMISSION = 43
        const val MAX_SIDE = 2400
    }
}
