package ru.simpad.okno

import android.Manifest
import android.annotation.SuppressLint
import android.bluetooth.BluetoothManager
import android.bluetooth.BluetoothSocket
import android.content.Context
import android.content.RestrictionsManager
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Color
import android.os.Build
import android.util.Base64
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.io.InputStream
import java.io.OutputStream
import java.util.UUID
import java.util.concurrent.Executors

/**
 * Поясной принтер талонов: Bluetooth Classic (SPP), команды ESC/POS.
 * Талон приходит из интерфейса готовой картинкой (PNG): так печатаются любые буквы —
 * таджикские, киргизские, узбекские — без зависимости от кодовых страниц принтера.
 * Картинка переводится в чёрно-белый растр и отправляется командой GS v 0 полосами.
 * Принтер сопрягается с планшетом заранее (в настройках Android, до включения киоска).
 * Рабочая сборка печатает только на принтер, заданный системой управления планшетами (MDM:
 * управляемая конфигурация printer_mac / printer_dots) — сотрудник смены выбрать другое устройство не может.
 * Учебная сборка позволяет выбрать принтер на экране настройки (для стенда и показа).
 */
class TicketPrinter(private val context: Context) {

    private val prefs = context.getSharedPreferences("okno_printer", Context.MODE_PRIVATE)
    private val worker = Executors.newSingleThreadExecutor()
    private var socket: BluetoothSocket? = null
    private var out: OutputStream? = null
    private var inp: InputStream? = null
    private var connectedMac: String? = null

    fun hasPermission(): Boolean =
        Build.VERSION.SDK_INT < Build.VERSION_CODES.S ||
            context.checkSelfPermission(Manifest.permission.BLUETOOTH_CONNECT) == PackageManager.PERMISSION_GRANTED

    private fun managed(): android.os.Bundle? = try {
        (context.getSystemService(Context.RESTRICTIONS_SERVICE) as RestrictionsManager).applicationRestrictions
    } catch (e: Exception) {
        null
    }

    private fun managedMac(): String? = managed()?.getString(M_MAC)?.trim()?.uppercase()?.takeIf { MAC_RE.matches(it) }

    /** Принтер задан системой управления планшетами. */
    fun isManaged(): Boolean = managedMac() != null

    /** Выбирать принтер в приложении можно только в учебной сборке и только если MDM его не задала. */
    fun canPick(): Boolean = BuildConfig.DEMO_SCANNER && !isManaged()

    fun mac(): String? = managedMac() ?: if (BuildConfig.DEMO_SCANNER) prefs.getString(KEY_MAC, null) else null
    fun name(): String = if (isManaged()) (managed()?.getString(M_NAME)?.takeIf { it.isNotBlank() } ?: "задан MDM")
        else prefs.getString(KEY_NAME, "") ?: ""
    fun dots(): Int {
        val d = if (isManaged()) managed()?.getInt(M_DOTS, 576) ?: 576 else prefs.getInt(KEY_DOTS, 576)
        return if (d == 384) 384 else 576
    }
    fun configured(): Boolean = !mac().isNullOrEmpty()

    /** Сопряжённые с планшетом Bluetooth-устройства: имя и адрес. */
    @SuppressLint("MissingPermission")  // разрешение проверяется выше, в hasPermission()
    fun bonded(): JSONArray {
        val arr = JSONArray()
        if (!hasPermission()) return arr
        val adapter = (context.getSystemService(Context.BLUETOOTH_SERVICE) as BluetoothManager).adapter ?: return arr
        for (d in adapter.bondedDevices ?: emptySet()) {
            arr.put(JSONObject().put("mac", d.address).put("name", d.name ?: d.address))
        }
        return arr
    }

    fun select(mac: String, name: String, dots: Int): Boolean {
        if (!canPick() || !MAC_RE.matches(mac) || dots !in listOf(384, 576)) return false
        worker.execute { closeQuietly() }  // соединение живёт только в потоке печати
        return prefs.edit().putString(KEY_MAC, mac).putString(KEY_NAME, name.take(60)).putInt(KEY_DOTS, dots).commit()
    }

    /** Печать в отдельном потоке; результат — в done(ok, ошибка). Одна повторная попытка с новым соединением. */
    fun print(pngB64: String, done: (Boolean, String) -> Unit) {
        worker.execute {
            try {
                val raster = toRaster(pngB64)
                try {
                    send(raster)
                } catch (e: IOException) {
                    closeQuietly()  // принтер мог уснуть или выйти из зоны — переподключаемся один раз
                    send(raster)
                }
                done(true, "")
            } catch (e: SecurityException) {
                done(false, "Нет разрешения на Bluetooth")
            } catch (e: IOException) {
                closeQuietly()
                done(false, "Принтер не отвечает — проверьте, что он включён и рядом")
            } catch (e: IllegalStateException) {
                done(false, e.message ?: "Принтер не настроен")
            } catch (e: Exception) {
                closeQuietly()
                done(false, "Ошибка печати: ${e.message}")
            }
        }
    }

    @SuppressLint("MissingPermission")
    private fun connection(): OutputStream {
        val mac = mac() ?: throw IllegalStateException("Принтер не задан — обратитесь к администратору")
        // соединение переиспользуется только с тем же принтером: MDM могла назначить другой
        out?.let { if (socket?.isConnected == true && connectedMac == mac) return it }
        closeQuietly()
        if (!hasPermission()) throw SecurityException("no BLUETOOTH_CONNECT")
        val adapter = (context.getSystemService(Context.BLUETOOTH_SERVICE) as BluetoothManager).adapter
            ?: throw IllegalStateException("На планшете нет Bluetooth")
        if (!adapter.isEnabled) throw IllegalStateException("Bluetooth выключен")
        val s = adapter.getRemoteDevice(mac).createRfcommSocketToServiceRecord(SPP_UUID)
        s.connect()
        socket = s
        connectedMac = mac
        inp = s.inputStream
        return s.outputStream.also { out = it }
    }

    /**
     * Датчик бумаги: DLE EOT 4. Если принтер ответил и бумаги нет — печать не начинается.
     * Принтер, который на запрос не отвечает (не все поддерживают), печатает как обычно.
     */
    private fun checkPaper(o: OutputStream) {
        val i = inp ?: return
        while (i.available() > 0) i.read()  // старые ответы — прочь
        o.write(byteArrayOf(0x10, 0x04, 0x04))
        o.flush()
        val until = System.currentTimeMillis() + STATUS_WAIT_MS
        while (System.currentTimeMillis() < until) {
            if (i.available() > 0) {
                val b = i.read()
                if (b >= 0 && (b and 0x60) != 0) throw IllegalStateException("В принтере нет бумаги — заправьте ленту")
                return
            }
            Thread.sleep(20)
        }
    }

    private fun send(raster: Raster) {
        val o = connection()
        o.write(byteArrayOf(0x1B, 0x40))  // ESC @ — сброс
        checkPaper(o)
        var y = 0
        while (y < raster.height) {
            val h = minOf(BAND, raster.height - y)
            val bpr = raster.bytesPerRow
            o.write(byteArrayOf(0x1D, 0x76, 0x30, 0x00, (bpr and 0xFF).toByte(), (bpr shr 8).toByte(),
                (h and 0xFF).toByte(), (h shr 8).toByte()))
            o.write(raster.data, y * bpr, h * bpr)
            y += h
        }
        o.write(byteArrayOf(0x1B, 0x64, 0x04))  // ESC d 4 — прогон бумаги до линии отрыва
        o.flush()
    }

    private class Raster(val bytesPerRow: Int, val height: Int, val data: ByteArray)

    private fun toRaster(pngB64: String): Raster {
        val bytes = Base64.decode(pngB64, Base64.DEFAULT)
        var bmp = BitmapFactory.decodeByteArray(bytes, 0, bytes.size) ?: throw IllegalStateException("Не удалось подготовить талон")
        val width = dots()
        if (bmp.width != width) {
            val h = (bmp.height.toLong() * width / bmp.width).toInt().coerceAtLeast(1)
            val scaled = Bitmap.createScaledBitmap(bmp, width, h, true)
            if (scaled != bmp) bmp.recycle()
            bmp = scaled
        }
        val w = bmp.width
        val h = bmp.height.coerceAtMost(MAX_HEIGHT)
        val bpr = (w + 7) / 8
        val data = ByteArray(bpr * h)
        val row = IntArray(w)
        for (yy in 0 until h) {
            bmp.getPixels(row, 0, w, 0, yy, w, 1)
            for (xx in 0 until w) {
                val p = row[xx]
                // прозрачное — белое; тёмнее середины — точка
                val lum = if (Color.alpha(p) < 128) 255 else (Color.red(p) * 299 + Color.green(p) * 587 + Color.blue(p) * 114) / 1000
                if (lum < 128) {
                    val i = yy * bpr + (xx shr 3)
                    data[i] = (data[i].toInt() or (0x80 shr (xx and 7))).toByte()
                }
            }
        }
        bmp.recycle()
        return Raster(bpr, h, data)
    }

    /** Закрыть соединение. Вызывается только из потока печати — там же живёт сокет. */
    private fun closeQuietly() {
        try { socket?.close() } catch (e: IOException) { /* уже закрыто */ }
        socket = null
        out = null
        inp = null
        connectedMac = null
    }

    /** Завершение работы экрана: соединение закрывается в потоке печати, после текущего задания. */
    fun shutdown() {
        worker.execute { closeQuietly() }
        worker.shutdown()
    }

    private companion object {
        val SPP_UUID: UUID = UUID.fromString("00001101-0000-1000-8000-00805F9B34FB")
        val MAC_RE = Regex("^([0-9A-F]{2}:){5}[0-9A-F]{2}$")
        const val KEY_MAC = "mac"
        const val KEY_NAME = "name"
        const val KEY_DOTS = "dots"
        const val BAND = 128
        const val MAX_HEIGHT = 2400
        const val STATUS_WAIT_MS = 400L
        const val M_MAC = "printer_mac"
        const val M_NAME = "printer_name"
        const val M_DOTS = "printer_dots"
    }
}
