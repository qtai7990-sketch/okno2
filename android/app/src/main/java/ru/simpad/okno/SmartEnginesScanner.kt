package ru.simpad.okno

import android.app.Activity
import org.json.JSONObject

/**
 * Рабочий сканер: Smart ID Engine (Smart Engines) — паспорт, NFC-чип, миграционная карта, штрихкод IMEI.
 * SDK выдаётся по лицензии. Порядок подключения — docs/SCANNER.md.
 * Пока SDK не добавлен, сканер сообщает, что не подключён.
 */
@Suppress("UNUSED_PARAMETER")
class SmartEnginesScanner(activity: Activity) : DocumentScanner {

    override fun scan(kind: String, done: (JSONObject) -> Unit) {
        done(scanFail("Сканер документов ещё не подключён: нужна лицензия и SDK Smart Engines (см. docs/SCANNER.md)"))
    }
}
